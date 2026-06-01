from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://motherless.com/"
SITE_HOST = "motherless.com"
SITE_ALIASES = frozenset({"motherless.com", "www.motherless.com"})
CDN_HOST_MARKERS = ("motherlessmedia.com",)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
    "Cookie": "age_verified=1",
}

_VIDEO_ID_RE = re.compile(r"^[A-F0-9]{4,12}$", re.IGNORECASE)
_GALLERY_PATH_RE = re.compile(r"^G[VIGF]?[A-F0-9]+$", re.IGNORECASE)
_VIDEO_PATH_RE = re.compile(
    r"^https?://(?:www\.)?motherless\.com/(?:g/[a-z0-9_]+/)?(?P<id>[A-F0-9]{4,12})/?(?:$|[?#])",
    re.IGNORECASE,
)
_CDN_VIDEO_RE = re.compile(
    r"motherlessmedia\.com/videos/(?P<id>[A-F0-9]{4,12})(?:-720p)?\.mp4",
    re.IGNORECASE,
)
_LIST_LINK_RE = re.compile(
    r'href="[^"]*/(?P<id>[A-F0-9]{4,12})"\s+title="(?P<title>[^"]+)"',
    re.IGNORECASE,
)
_FILEURL_RE = re.compile(
    r"""(?:__)?fileurl\s*=\s*(["'])(?P<url>(?:(?!\1).)+)\1""",
    re.IGNORECASE,
)
_SETUP_FILE_RE = re.compile(
    r"""setup\(\{\s*["']file["']\s*:\s*(["'])(?P<url>(?:(?!\1).)+)\1""",
    re.IGNORECASE,
)
_MP4_CDN_RE = re.compile(
    r"https?://[^\s\"'<>]*motherlessmedia\.com[^\s\"'<>]*\.mp4[^\s\"'<>]*",
    re.IGNORECASE,
)
_CODENAME_RE = re.compile(r'data-codename=["\']([A-F0-9]{4,12})["\']', re.IGNORECASE)

_RESERVED_PATH_HEADS = frozenset(
    {
        "videos",
        "term",
        "boards",
        "groups",
        "galleries",
        "m",
        "u",
        "gv",
        "gi",
        "gf",
        "gm",
        "g",
        "iframe",
        "search",
        "login",
        "register",
    }
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    if h in SITE_ALIASES:
        return True
    return any(marker in h for marker in CDN_HOST_MARKERS)


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _is_cloudflare_challenge(html: str) -> bool:
    if not html:
        return True
    low = html.lower()
    return (
        "just a moment" in low
        or "cf_chl_opt" in low
        or "challenge-platform" in low
        or "enable javascript and cookies" in low
    )


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> str | None:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer or BASE_SITE

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                text = resp.text
                if _is_cloudflare_challenge(text):
                    continue
                return text
        except Exception:
            continue
    return None


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    text = await _fetch_with_curl_cffi(url, referer=referer)
    if text:
        return text

    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer or BASE_SITE
    html = await pool_fetch_html(url, headers=headers)
    if _is_cloudflare_challenge(html):
        raise ValueError(f"Blocked by challenge page: {url}")
    return html


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _meta(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> Optional[str]:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    return None


def _clean_title(title: str | None, *, video_id: str | None = None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    for suffix in (" | MOTHERLESS.COM ™", " - MOTHERLESS.COM", " | MOTHERLESS.COM"):
        if suffix.lower() in t.lower():
            t = re.split(re.escape(suffix), t, flags=re.I)[0].strip()
    if video_id and t.upper() == video_id.upper():
        return None
    return t or None


def _normalize_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return digits or None


def _is_gallery_codename(code: str) -> bool:
    return bool(_GALLERY_PATH_RE.fullmatch((code or "").strip()))


def _is_video_codename(code: str) -> bool:
    code = (code or "").strip().upper()
    if not _VIDEO_ID_RE.fullmatch(code):
        return False
    return not _is_gallery_codename(code)


def _extract_video_id(url: str) -> Optional[str]:
    raw = (url or "").strip().split("#", 1)[0].split("?", 1)[0]
    m = _VIDEO_PATH_RE.match(raw if raw.endswith("/") else raw + "/")
    if m and _is_video_codename(m.group("id")):
        return m.group("id").upper()

    m_cdn = _CDN_VIDEO_RE.search(raw)
    if m_cdn and _is_video_codename(m_cdn.group("id")):
        return m_cdn.group("id").upper()

    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != SITE_HOST and "motherlessmedia.com" not in host:
        return None

    path = (parsed.path or "").strip("/")
    if not path:
        return None

    parts = [p for p in path.split("/") if p]
    if not parts:
        return None

    if parts[0].lower() == "iframe" and len(parts) >= 2:
        candidate = parts[1]
        return candidate.upper() if _is_video_codename(candidate) else None

    if parts[0].lower() in _RESERVED_PATH_HEADS and parts[0].lower() != "g":
        return None

    if parts[0].lower() == "g":
        if len(parts) < 2:
            return None
        candidate = parts[-1]
    elif len(parts) == 1:
        candidate = parts[0]
        if _is_gallery_codename(candidate):
            return None
    else:
        return None

    return candidate.upper() if _is_video_codename(candidate) else None


def _canonical_video_url(video_id: str) -> str:
    return f"https://{SITE_HOST}/{video_id.upper()}"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    vid = _extract_video_id(href)
    return _canonical_video_url(vid) if vid else None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-thumb", "src"):
        v = img.get(key)
        if not v or str(v).startswith("data:") or "plc.gif" in str(v):
            continue
        url = str(v).strip()
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _quality_from_url(url: str, *, res: str | None = None) -> str:
    if res:
        res = str(res).strip().lower()
        if res.endswith("p") and res[:-1].isdigit():
            return res
    low = (url or "").lower()
    if "-720p" in low:
        return "720p"
    qm = re.search(r"(\d{3,4})p", low)
    if qm:
        return f"{qm.group(1)}p"
    return "default"


def _cdn_unsigned_candidates(video_id: str) -> list[str]:
    vid = video_id.upper()
    out: list[str] = []
    for n in (5, 4, 3, 2, 1):
        out.append(f"https://cdn{n}-videos.motherlessmedia.com/videos/{vid}-720p.mp4")
        out.append(f"https://cdn{n}-videos.motherlessmedia.com/videos/{vid}.mp4")
    out.append(f"http://cdn4.videos.motherlessmedia.com/videos/{vid}.mp4?fs=opencloud")
    return out


def _add_stream(
    streams: list[dict[str, str]],
    seen: set[str],
    url: str,
    *,
    res: str | None = None,
) -> None:
    url = (url or "").replace("\\/", "/").strip()
    if not url.startswith("http") or url in seen:
        return
    if "motherlessmedia.com" not in url.lower() and ".mp4" not in url.lower():
        return
    seen.add(url)
    streams.append(
        {
            "url": url,
            "quality": _quality_from_url(url, res=res),
            "format": "mp4",
        }
    )


def _streams_from_html(html: str, video_id: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for pattern in (_FILEURL_RE, _SETUP_FILE_RE):
        for m in pattern.finditer(html):
            _add_stream(streams, seen, m.group("url"))

    soup = BeautifulSoup(html, "lxml")
    for source in soup.select("video source[src], #ml-video source[src]"):
        src = source.get("src") or ""
        _add_stream(streams, seen, src, res=source.get("res"))

    for url in _MP4_CDN_RE.findall(html):
        _add_stream(streams, seen, url)

    if not streams and video_id:
        for url in _cdn_unsigned_candidates(video_id):
            _add_stream(streams, seen, url)

    def _score(s: dict[str, str]) -> int:
        q = s.get("quality", "")
        digits = "".join(ch for ch in q if ch.isdigit())
        score = int(digits) if digits else 0
        if "hash=" in (s.get("url") or "").lower():
            score += 10000
        return score

    streams.sort(key=_score, reverse=True)
    default = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


def _parse_thumb_block(block: Any) -> Optional[dict[str, Any]]:
    thumb_el = block.select_one(".desktop-thumb[data-codename], .mobile-thumb[data-codename]")
    codename = None
    if thumb_el:
        codename = (thumb_el.get("data-codename") or "").strip().upper()
    if not codename or not _is_video_codename(codename):
        return None

    url = _canonical_video_url(codename)
    title_el = block.select_one("a.caption.title, a.caption.title.pop")
    img = block.select_one("img.static, img[data-strip-src], img[alt]")
    dur_el = block.select_one("span.size")
    views_el = block.select_one("span.hits span.value, .hits .value")
    uploader_el = block.select_one("a.uploader")

    title = _clean_title(
        _first_non_empty(
            title_el.get("title") if title_el else None,
            title_el.get_text(" ", strip=True) if title_el else None,
            img.get("alt") if img else None,
        ),
        video_id=codename,
    ) or codename

    return {
        "url": url,
        "title": title,
        "thumbnail_url": _best_image_url(img),
        "duration": dur_el.get_text(strip=True) if dur_el else None,
        "views": _normalize_views(views_el.get_text(strip=True) if views_el else None),
        "uploader_name": uploader_el.get_text(strip=True) if uploader_el else None,
        "tags": None,
    }


def _parse_list_items(soup: BeautifulSoup, html: str, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("div.thumb-container.video"):
        if len(items) >= limit:
            break
        parsed = _parse_thumb_block(block)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if len(items) < limit:
        for m in _LIST_LINK_RE.finditer(html):
            if len(items) >= limit:
                break
            vid = m.group("id").upper()
            if not _is_video_codename(vid):
                continue
            url = _canonical_video_url(vid)
            if url in seen:
                continue
            seen.add(url)
            items.append(
                {
                    "url": url,
                    "title": _clean_title(m.group("title"), video_id=vid) or vid,
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "tags": None,
                }
            )

    if len(items) < limit:
        for vid in _CODENAME_RE.findall(html):
            if len(items) >= limit:
                break
            vid = vid.upper()
            if not _is_video_codename(vid):
                continue
            url = _canonical_video_url(vid)
            if url in seen:
                continue
            seen.add(url)
            items.append(
                {
                    "url": url,
                    "title": vid,
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "tags": None,
                }
            )

    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    page_num = max(1, int(page) if page else 1)
    qs = {k: v[-1] for k, v in parse_qs(parsed.query).items() if v}

    if page_num <= 1:
        qs.pop("page", None)
    else:
        qs["page"] = str(page_num)

    query = urlencode(qs) if qs else ""
    path = parsed.path or "/"
    if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
        path = f"{path}/"

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            path,
            "",
            query,
            "",
        )
    )


def _is_missing_media_page(html: str) -> bool:
    low = (html or "").lower()
    return (
        "file not found" in low
        or "the page you're looking for cannot be found" in low
        or "404 - motherless.com" in low
    )


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_id = _extract_video_id(url) or ""
    page_url = _canonical_video_url(video_id) if video_id else url

    raw_title = _first_non_empty(
        soup.select_one(".media-meta-title h1").get_text(" ", strip=True)
        if soup.select_one(".media-meta-title h1")
        else None,
        soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
        _meta(soup, prop="og:title"),
        soup.title.get_text(strip=True) if soup.title else None,
    )
    title = _clean_title(raw_title, video_id=video_id) or raw_title or video_id or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        (soup.select_one("video[data-poster]") or {}).get("data-poster")
        if soup.select_one("video[data-poster]")
        else None,
        _best_image_url(soup.select_one("video[poster], img")),
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    views = None
    for el in soup.select(".media-meta-info span.count, .media-meta span.count"):
        txt = el.get_text(" ", strip=True)
        if "view" in txt.lower():
            views = _normalize_views(txt)
            break

    uploader = None
    up = soup.select_one('.media-meta-member a[href^="/m/"], a.uploader[href^="/m/"]')
    if up:
        uploader = up.get_text(strip=True) or None
        if not uploader:
            href = up.get("href") or ""
            uploader = href.strip("/").split("/")[-1] or None

    upload_date = None
    dm = re.search(
        r'class=["\']count[^>]+>(\d+\s+[a-zA-Z]{3}\s+\d{4})<',
        html,
        re.IGNORECASE,
    )
    if dm:
        upload_date = dm.group(1).strip()
    else:
        for el in soup.select(".media-meta-info span.count"):
            txt = el.get_text(" ", strip=True)
            if re.search(r"\bago\b", txt, re.I):
                upload_date = txt
                break

    duration = None
    dur_el = soup.select_one(".media-meta-duration, .media-meta-info .duration, span.size")
    if dur_el:
        duration = dur_el.get_text(strip=True) or None

    tags: list[str] = []
    kw = _meta(soup, name="keywords")
    if kw:
        tags.extend([t.strip() for t in kw.split(",") if t.strip()])
    for a in soup.select('a[href*="/term/videos/"]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags and len(tag) < 80:
            tags.append(tag)

    related = _parse_list_items(soup, html, limit=24)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or _streams_from_html(html, video_id)
    if video_id and not video_data.get("streams"):
        video_data = _streams_from_html("", video_id)

    if _is_missing_media_page(html) and not video_data.get("has_video"):
        title = title if title != video_id else f"Missing media {video_id}"

    return {
        "url": page_url,
        "title": title,
        "description": _meta(soup, prop="og:description") or _meta(soup, name="description"),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": None,
        "tags": tags or None,
        "upload_date": upload_date,
        "video": {
            k: v
            for k, v in video_data.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    video_id = _extract_video_id(url)
    if not video_id:
        raise ValueError(f"Unsupported Motherless URL: {url}")

    page_url = _canonical_video_url(video_id)
    html = await fetch_page(page_url, referer=BASE_SITE)
    video_data = _streams_from_html(html, video_id)
    return parse_video_page(html, page_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or BASE_SITE
    page_url = _build_list_page_url(normalized_base, page)
    try:
        html = await fetch_page(page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, html, limit=limit)
