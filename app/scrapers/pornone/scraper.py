from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://pornone.com/"
SITE_HOST = "pornone.com"
SITE_ALIASES = frozenset({"pornone.com", "www.pornone.com"})
CDN_HOST_MARKERS = (
    "pornone.com",
    "s307.pornone.com",
    "s308.pornone.com",
    "th-eu",
    "cdn-eu-g",
)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
    "Cookie": "age_verified=1; cookies_accepted=1",
}

_LOCALE_CODES = frozenset(
    {"de", "nl", "au", "es", "ar", "mx", "fr", "it", "pt", "se", "jp"}
)
_RESERVED_PATH_HEADS = frozenset(
    {
        "shorts",
        "login",
        "register",
        "channels",
        "pornstars",
        "categories",
        "playlist",
        "queue",
        "search",
        "blog",
        "terms",
        "privacy",
        "dmca",
        "contact",
        "advertise",
        "content-partners",
        "cookie-policy",
        "parental-control",
        "live-sex",
        "tiktok18",
        "cams",
        "embed",
    }
)

_VIDEO_HREF_RE = re.compile(
    r"""href=["'](?:https?://(?:www\.)?pornone\.com)?"""
    r"""(?P<path>(?:/[a-z0-9-]+){2,}/\d{6,}/?)["']""",
    re.IGNORECASE,
)
_RELATED_ITEM_RE = re.compile(
    r"\{thumb:\s*'([^']*)',\s*url:\s*'([^']+)',\s*title:\s*'([^']*)',\s*duration:\s*'(\d+)'\}",
    re.IGNORECASE,
)
_CONTENT_URL_RE = re.compile(
    r'"contentUrl"\s*:\s*"([^"]+\.mp4[^"]*)"',
    re.IGNORECASE,
)
_MP4_CDN_RE = re.compile(
    r"https?://[^\s\"'<>]*pornone\.com[^\s\"'<>]*\.mp4[^\s\"'<>]*",
    re.IGNORECASE,
)
_VIEWS_RE = re.compile(r"([\d,.]+[KMB]?)\s*(?:views|Views)", re.IGNORECASE)


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


def _meta(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> Optional[str]:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return None


def _first_non_empty(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _clean_title(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    s = re.sub(r"\s+", " ", title).strip()
    for suffix in (
        " — PornOne ex vPorn",
        " - PornOne ex vPorn",
        " | PornOne",
        " — PornOne",
    ):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s or None


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0:00"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_duration_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.strip()
    if t.isdigit():
        return _format_duration(int(t))
    if re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", t):
        return t
    return t


def _normalize_views(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return raw.strip().replace(",", "")


def _extract_video_id(url: str) -> Optional[str]:
    path = (urlparse(url or "").path or "").strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    last = parts[-1]
    if last.isdigit() and len(last) >= 6:
        return last
    return None


def _path_parts(path: str) -> list[str]:
    return [p for p in (path or "").strip("/").split("/") if p]


def _is_video_path_parts(parts: list[str]) -> bool:
    if len(parts) < 3 or not parts[-1].isdigit() or len(parts[-1]) < 6:
        return False
    if parts[0] in _RESERVED_PATH_HEADS:
        return False
    if parts[0] in _LOCALE_CODES:
        return len(parts) >= 4 and parts[1] not in _RESERVED_PATH_HEADS
    return True


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)

    parsed = urlparse(href.split("#", 1)[0])
    host = (parsed.netloc or "").lower()
    if "pornone.com" not in host:
        return None

    parts = _path_parts(parsed.path)
    if not _is_video_path_parts(parts):
        return None
    if parts[0] in _LOCALE_CODES:
        return None

    clean_path = "/" + "/".join(parts) + "/"
    return urlunparse(("https", SITE_HOST, clean_path, "", "", ""))


def _quality_from_source(label: str | None, res: str | None, url: str) -> str:
    if res and str(res).isdigit():
        return f"{res}p"
    if label:
        s = str(label).strip()
        if s.isdigit():
            return f"{s}p"
        if s.lower().endswith("p"):
            return s.lower()
    m = re.search(r"_(\d{3,4})x\d+_", url)
    if m:
        return f"{m.group(1)}p"
    m2 = re.search(r"(\d{3,4})p", url, re.I)
    return f"{m2.group(1)}p" if m2 else "default"


def _add_stream(
    streams: list[dict[str, str]],
    seen: set[str],
    url: str,
    *,
    label: str | None = None,
    res: str | None = None,
) -> None:
    url = (url or "").replace("\\/", "/").strip()
    if not url.startswith("http") or url in seen:
        return
    if ".mp4" not in url.lower():
        return
    seen.add(url)
    streams.append(
        {
            "url": url,
            "quality": _quality_from_source(label, res, url),
            "format": "mp4",
        }
    )


def _streams_from_html(html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    soup = BeautifulSoup(html, "lxml")
    for source in soup.select("#pornone-video-player source[src], video source[src]"):
        _add_stream(
            streams,
            seen,
            source.get("src") or "",
            label=source.get("label"),
            res=source.get("res"),
        )

    for m in _CONTENT_URL_RE.finditer(html):
        _add_stream(streams, seen, m.group(1))

    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            content = node.get("contentUrl")
            if isinstance(content, str):
                _add_stream(streams, seen, content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, str):
                        _add_stream(streams, seen, item)

    for url in _MP4_CDN_RE.findall(html):
        _add_stream(streams, seen, url)

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


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    parts = _path_parts(parsed.path)

    if parts and parts[-1].isdigit():
        parts = parts[:-1]

    if not parts:
        if page_num <= 1:
            new_path = "/"
        else:
            new_path = f"/{page_num}/"
    elif page_num <= 1:
        new_path = "/" + "/".join(parts) + "/"
    else:
        new_path = "/" + "/".join(parts) + f"/{page_num}/"

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            new_path,
            "",
            parsed.query,
            "",
        )
    )


def _parse_videocard_block(anchor: Any) -> Optional[dict[str, Any]]:
    href = anchor.get("href") or ""
    canon = _normalize_video_href(href)
    if not canon:
        return None

    title_el = anchor.select_one(".videotitle")
    img = anchor.select_one("img.thumbimg, img.imgvideo")
    dur_el = anchor.select_one(".durlabel")
    uploader_el = anchor.select_one(".author .font-semibold")
    views_el = anchor.select_one(".author span.text-right")

    title = _clean_title(
        _first_non_empty(
            title_el.get_text(" ", strip=True) if title_el else None,
            img.get("alt") if img else None,
        )
    ) or canon

    thumb = None
    if img:
        thumb = img.get("src") or img.get("data-path")
        if thumb and "images/svg" in thumb:
            thumb = None
        if thumb and thumb.endswith("/"):
            thumb = f"{thumb.rstrip('/')}/d163.jpg"

    duration = None
    if dur_el:
        dur_text = dur_el.get_text(" ", strip=True)
        dur_text = re.sub(r"^\s*HD\s*Video\s*", "", dur_text, flags=re.I).strip()
        duration = _parse_duration_text(dur_text)

    return {
        "url": canon,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": _normalize_views(views_el.get_text(strip=True) if views_el else None),
        "uploader_name": uploader_el.get_text(strip=True) if uploader_el else None,
        "tags": None,
    }


def _parse_related_json(html: str, *, limit: int, seen: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for thumb, url, title, duration in _RELATED_ITEM_RE.findall(html):
        if len(items) >= limit:
            break
        canon = _normalize_video_href(url)
        if not canon or canon in seen:
            continue
        seen.add(canon)
        dur = _parse_duration_text(duration) if duration else None
        items.append(
            {
                "url": canon,
                "title": _clean_title(title) or canon,
                "thumbnail_url": thumb or None,
                "duration": dur,
                "views": None,
                "uploader_name": None,
                "tags": None,
            }
        )
    return items


def _parse_list_items(soup: BeautifulSoup, html: str, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for anchor in soup.select("a.videocard[href], a.vidLinkFX[href]"):
        if len(items) >= limit:
            break
        parsed = _parse_videocard_block(anchor)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    for a in soup.find_all("a", href=True):
        if len(items) >= limit:
            break
        if "videocard" in (a.get("class") or []):
            continue
        canon = _normalize_video_href(a["href"])
        if not canon or canon in seen:
            continue
        seen.add(canon)
        img = a.find("img")
        title = _clean_title(
            _first_non_empty(
                a.get("title"),
                img.get("alt") if img and img.get("alt") else None,
            )
        )
        thumb = None
        if img:
            src = img.get("data-src") or img.get("src")
            if src and "images/svg" not in src:
                thumb = src
        items.append(
            {
                "url": canon,
                "title": title or canon,
                "thumbnail_url": thumb,
                "duration": None,
                "views": None,
                "uploader_name": None,
                "tags": None,
            }
        )

    if len(items) < limit:
        for m in _VIDEO_HREF_RE.finditer(html):
            if len(items) >= limit:
                break
            path = m.group("path")
            canon = _normalize_video_href(path)
            if not canon or canon in seen:
                continue
            seen.add(canon)
            items.append(
                {
                    "url": canon,
                    "title": canon,
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "tags": None,
                }
            )

    if len(items) < limit:
        extra = _parse_related_json(html, limit=limit - len(items), seen=seen)
        items.extend(extra)

    return items[:limit]


def _canonical_watch_url(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "lxml") if html else None
    if soup:
        for sel in (
            ("link", {"rel": "canonical"}),
            ("meta", {"property": "og:url"}),
        ):
            tag = soup.find(sel[0], attrs=sel[1])
            if tag:
                href = tag.get("href") or tag.get("content")
                if href:
                    canon = _normalize_video_href(str(href))
                    if canon:
                        return canon
    canon = _normalize_video_href(url)
    return canon or url


async def _fetch_with_curl_cffi(url: str) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200 and resp.text:
                    return resp.text
        except Exception:
            continue
    return None


async def fetch_page(url: str) -> str:
    text = await _fetch_with_curl_cffi(url)
    if text:
        return text
    return await pool_fetch_html(url, headers=_DEFAULT_HEADERS)


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    page_url = _canonical_watch_url(html, url)

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _meta(soup, prop="og:image")
    duration = None
    dur_meta = _meta(soup, prop="og:video:duration") or _meta(soup, prop="video:duration")
    if dur_meta and str(dur_meta).isdigit():
        duration = _format_duration(int(dur_meta))

    views = None
    vm = _VIEWS_RE.search(html)
    if vm:
        views = _normalize_views(vm.group(1))

    uploader = None
    up = soup.select_one('a[href*="/profile/"], a[href*="/u/"]')
    if up:
        uploader = up.get_text(strip=True) or None

    tags: list[str] = []
    kw = _meta(soup, name="keywords")
    if kw:
        tags.extend([t.strip() for t in kw.split(",") if t.strip()])

    video_data = video or _streams_from_html(html)
    related = _parse_list_items(soup, html, limit=24)
    related = [r for r in related if r.get("url") != page_url]

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
        "upload_date": None,
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
        raise ValueError(f"Unsupported PornOne URL: {url}")

    fetch_url = _normalize_video_href(url) or url
    html = await fetch_page(fetch_url)
    video_data = _streams_from_html(html)
    return parse_video_page(html, fetch_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_num = max(1, int(page) if page else 1)
    page_url = _build_list_page_url(base_url, page_num)
    try:
        html = await fetch_page(page_url)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, html, limit=limit)
