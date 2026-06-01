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

_VIDEO_ID_RE = re.compile(r"^[A-F0-9]{5,9}$", re.IGNORECASE)
_VIDEO_PATH_RE = re.compile(
    r"^https?://(?:www\.)?motherless\.com/(?:g/[a-z0-9_]+/)?(?P<id>[A-F0-9]{5,9})/?(?:$|[?#])",
    re.IGNORECASE,
)
_LIST_HREF_RE = re.compile(
    r'href="(?P<href>/[A-F0-9]{5,9})"\s+title="(?P<title>[^"]+)"',
    re.IGNORECASE,
)
_SETUP_FILE_RE = re.compile(
    r"""setup\(\{\s*["']file["']\s*:\s*(["'])(?P<url>(?:(?!\1).)+)\1""",
    re.IGNORECASE,
)
_FILEURL_RE = re.compile(
    r"""fileurl\s*=\s*(["'])(?P<url>(?:(?!\1).)+)\1""",
    re.IGNORECASE,
)
_MP4_CDN_RE = re.compile(
    r"https?://[^\s\"'<>]*motherlessmedia\.com[^\s\"'<>]*\.mp4[^\s\"'<>]*",
    re.IGNORECASE,
)
_VIEWS_RE = re.compile(r"([\d,.]+)\s+Views", re.IGNORECASE)
_FAVS_RE = re.compile(r"([\d,.]+)\s+Favorites", re.IGNORECASE)


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


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer or BASE_SITE
    return await pool_fetch_html(url, headers=headers)


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


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    for suffix in (" | MOTHERLESS.COM ™", " - MOTHERLESS.COM", " | MOTHERLESS.COM"):
        if suffix.lower() in t.lower():
            t = re.split(re.escape(suffix), t, flags=re.I)[0].strip()
    return t or None


def _normalize_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return digits or None


def _extract_video_id(url: str) -> Optional[str]:
    raw = (url or "").strip().split("#", 1)[0].split("?", 1)[0]
    m = _VIDEO_PATH_RE.match(raw if raw.endswith("/") else raw + "/")
    if m:
        return m.group("id").upper()

    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != SITE_HOST:
        return None

    path = (parsed.path or "").strip("/")
    if not path:
        return None

    parts = [p for p in path.split("/") if p]
    if not parts:
        return None

    head = parts[0].upper()
    if head.startswith("GV") and len(head) > 2:
        return None
    if head in {"VIDEOS", "TERM", "BOARDS", "GROUPS", "GALLERIES", "M", "U", "GV", "GI", "GF", "GM"}:
        return None

    if parts[0].lower() == "g" and len(parts) >= 2:
        candidate = parts[-1]
    else:
        candidate = parts[-1]

    if _VIDEO_ID_RE.fullmatch(candidate):
        return candidate.upper()
    return None


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
        if not v or str(v).startswith("data:"):
            continue
        url = str(v).strip()
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _quality_from_url(url: str) -> str:
    low = (url or "").lower()
    if "-720p" in low or "720p" in low:
        return "720p"
    qm = re.search(r"(\d{3,4})p", low)
    if qm:
        return f"{qm.group(1)}p"
    return "default"


def _cdn_candidates(video_id: str) -> list[str]:
    vid = video_id.upper()
    return [
        f"https://cdn5-videos.motherlessmedia.com/videos/{vid}-720p.mp4",
        f"https://cdn5-videos.motherlessmedia.com/videos/{vid}.mp4",
        f"http://cdn4.videos.motherlessmedia.com/videos/{vid}.mp4?fs=opencloud",
    ]


def _streams_from_html(html: str, video_id: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for pattern in (_SETUP_FILE_RE, _FILEURL_RE):
        for m in pattern.finditer(html):
            url = m.group("url").replace("\\/", "/").strip()
            if not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            streams.append(
                {
                    "url": url,
                    "quality": _quality_from_url(url),
                    "format": "mp4",
                }
            )

    for url in _MP4_CDN_RE.findall(html):
        url = url.replace("\\/", "/").strip()
        if url in seen:
            continue
        seen.add(url)
        streams.append(
            {
                "url": url,
                "quality": _quality_from_url(url),
                "format": "mp4",
            }
        )

    for url in _cdn_candidates(video_id):
        if url in seen:
            continue
        seen.add(url)
        streams.append(
            {
                "url": url,
                "quality": _quality_from_url(url),
                "format": "mp4",
            }
        )

    def _score(s: dict[str, str]) -> int:
        q = s.get("quality", "")
        digits = "".join(ch for ch in q if ch.isdigit())
        return int(digits) if digits else 0

    streams.sort(key=_score, reverse=True)
    default = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


def _parse_list_items(soup: BeautifulSoup, html: str, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for m in _LIST_HREF_RE.finditer(html):
        if len(items) >= limit:
            break
        url = _normalize_video_href(m.group("href"))
        if not url or url in seen:
            continue
        seen.add(url)
        title = _clean_title(m.group("title")) or "Unknown Video"
        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": None,
                "duration": None,
                "views": None,
                "uploader_name": None,
                "tags": None,
            }
        )

    for block in soup.select(".thumb-container, .media-item, .desktop-thumb, .mobile-thumb"):
        if len(items) >= limit:
            break
        link = block.select_one('a[href^="/"][title], a[href^="/"]')
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        img = block.select_one("img")
        dur_el = block.select_one(".duration, .media-meta-duration")
        items.append(
            {
                "url": url,
                "title": _clean_title(
                    _first_non_empty(
                        link.get("title"),
                        link.get_text(" ", strip=True),
                        img.get("alt") if img else None,
                    )
                )
                or "Unknown Video",
                "thumbnail_url": _best_image_url(img),
                "duration": dur_el.get_text(strip=True) if dur_el else None,
                "views": None,
                "uploader_name": None,
                "tags": None,
            }
        )

    if len(items) < limit:
        for a in soup.select('a[href^="/"]'):
            if len(items) >= limit:
                break
            href = a.get("href") or ""
            if not _VIDEO_ID_RE.fullmatch(href.strip("/")):
                continue
            url = _canonical_video_url(href.strip("/"))
            if url in seen:
                continue
            seen.add(url)
            items.append(
                {
                    "url": url,
                    "title": _clean_title(a.get("title") or a.get_text(" ", strip=True)) or "Unknown Video",
                    "thumbnail_url": _best_image_url(a.select_one("img")),
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


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_id = _extract_video_id(url) or ""
    page_url = _canonical_video_url(video_id) if video_id else url

    title = _clean_title(
        _first_non_empty(
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _best_image_url(soup.select_one("video[poster], img")),
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    views = None
    vm = _VIEWS_RE.search(html)
    if vm:
        views = _normalize_views(vm.group(1))

    uploader = None
    up = soup.select_one('a[href^="/m/"], span.username, .media-meta-member a')
    if up:
        uploader = up.get("title") or up.get_text(strip=True) or None
        if uploader and uploader.startswith("/"):
            uploader = uploader.strip("/").split("/")[-1]

    upload_date = None
    dm = re.search(
        r"class=[\"']count[^>]+>(\d+\s+[a-zA-Z]{3}\s+\d{4})<",
        html,
        re.IGNORECASE,
    )
    if dm:
        upload_date = dm.group(1).strip()

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

    return {
        "url": page_url,
        "title": title,
        "description": _meta(soup, prop="og:description") or _meta(soup, name="description"),
        "thumbnail_url": thumbnail,
        "duration": None,
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
