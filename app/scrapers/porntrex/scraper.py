from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import aiohttp
import httpx
from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

logger = logging.getLogger(__name__)

BASE_SITE = "https://www.porntrex.com/"
SITE_HOST = "porntrex.com"
SITE_ALIASES = frozenset({"porntrex.com", "www.porntrex.com"})

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_DEFAULT_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Referer": BASE_SITE,
    "Cookie": "age_pass=1; PHPSESSID=1",
}

_VIDEO_PATH_RE = re.compile(r"^/video/(\d+)/[^/]+/?$", re.IGNORECASE)
_VIDEO_HREF_RE = re.compile(
    r'href=["\'](?:https?://(?:www\.)?porntrex\.com)?/video/(\d+)/([^"\']+)/?["\']',
    re.IGNORECASE,
)
_KT_URL_KEYS = r"(?:video_url|video_url_text|video_alt_url|video_alt_url2)"
_KT_PATTERNS = [
    rf"{_KT_URL_KEYS}\s*[:=]\s*'([^']+)'",
    rf'{_KT_URL_KEYS}\s*[:=]\s*"([^"]+)"',
]


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".porntrex.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _browser_headers(referer: str | None = None) -> dict[str, str]:
    """Full browser-like headers on every PornTrex request (stable User-Agent)."""
    headers = dict(_DEFAULT_HEADERS)
    headers["User-Agent"] = _USER_AGENT
    headers["Referer"] = referer or BASE_SITE
    return headers


def _sanitize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.scheme:
        parsed = urlparse(f"https://{url.lstrip('/')}")
    path = parsed.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunparse((parsed.scheme or "https", parsed.netloc, path, parsed.params, parsed.query, parsed.fragment))


async def _fetch_with_curl_cffi(url: str, *, headers: dict[str, str]) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(_sanitize_url(url))
                if resp.status_code == 200 and resp.text:
                    return resp.text
        except Exception:
            continue
    return None


async def _fetch_with_httpx(url: str, *, headers: dict[str, str]) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(45.0, connect=20.0),
        headers=headers,
    ) as client:
        resp = await client.get(_sanitize_url(url))
        resp.raise_for_status()
        return resp.text


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    clean_url = _sanitize_url(url)
    headers = _browser_headers(referer)
    timeout = aiohttp.ClientTimeout(total=45, connect=20, sock_read=30)

    try:
        return await pool_fetch_html(clean_url, headers=headers, timeout=timeout)
    except Exception as pool_err:
        logger.warning("PornTrex pool fetch failed for %s: %s", clean_url, pool_err)

    text = await _fetch_with_curl_cffi(clean_url, headers=headers)
    if text:
        return text

    try:
        return await _fetch_with_httpx(clean_url, headers=headers)
    except Exception as httpx_err:
        logger.warning("PornTrex httpx fetch failed for %s: %s", clean_url, httpx_err)
        raise pool_err from httpx_err


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
    for suffix in (
        " / Embed Player",
        " - PornTrex",
        " | PornTrex",
        " - porntrex.com",
        " | porntrex.com",
        " - PornTrex.com",
        " | PornTrex.com",
        " - Best 4k Porn Site",
        " - Best 4k Porn Site - PornTrex",
        " - Best Free HD Porn Videos",
        " - Best Free HD Porn Videos - Best 4k Porn Site",
        " - Best Free HD Porn Videos - Best 4k Porn Site - PornTrex",
    ):
        if t.lower().endswith(suffix.lower()):
            t = t[: -len(suffix)].strip()
    if t.lower().startswith("best free hd porn videos"):
        return None
    return t or None


def _clean_list_title(title: str | None) -> Optional[str]:
    t = _clean_title(title)
    if not t:
        return None
    t = re.sub(
        r"\s+\d{1,2}:\d{2}(?::\d{2})?\s+\d{1,3}%\s+\d[\d\.\s]*[kKmMbB]?\s*$",
        "",
        t,
    ).strip()
    t = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s*$", "", t).strip()
    return t or None


def _normalize_numberish(value: str | None) -> Optional[str]:
    if not value:
        return None
    txt = str(value).strip().replace(",", "").replace("\u00a0", " ")
    txt = re.sub(r"\s+", "", txt)
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return m.group(0) if m else None


def _extract_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(
        r"(\d[\d\s,\.]*)\s*views?\b",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"\bviews?\s*[:\-]?\s*(\d[\d\s,\.]*\s*[KMBkmb]?)\b",
            text,
            flags=re.IGNORECASE,
        )
    if not m:
        m = re.search(r"\b(\d[\d\s,\.]*\s*[KMBkmb])\b", text, flags=re.IGNORECASE)
    if not m:
        return None
    return _normalize_numberish(m.group(1))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy-src", "srcset", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url or url.startswith("data:"):
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return urljoin(BASE_SITE, url)
        return url
    return None


def _extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"/video/(\d+)/", url or "", flags=re.IGNORECASE)
    return m.group(1) if m else None


def _canonical_video_url(video_id: str, slug: str | None = None) -> str:
    if slug:
        slug = slug.strip("/")
        return f"https://www.porntrex.com/video/{video_id}/{slug}/"
    return f"https://www.porntrex.com/video/{video_id}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://www.porntrex.com{href}"

    parsed = urlparse(href)
    host = (parsed.netloc or "").lower()
    if SITE_HOST not in host:
        return None
    if not _VIDEO_PATH_RE.match(parsed.path or ""):
        return None
    if parsed.query:
        return None

    parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
    if len(parts) < 3 or parts[0].lower() != "video":
        return None
    vid, slug = parts[1], parts[2]
    return _canonical_video_url(vid, slug)


def _resolve_kt_url(raw: str, page_url: str) -> str:
    raw = (raw or "").strip()
    m = re.match(r"^function/\d+/(https?://.+)$", raw)
    if m:
        return m.group(1)
    if raw.startswith("//"):
        return f"https:{raw}"
    if raw.startswith("/"):
        return urljoin(page_url, raw)
    return raw


def _detect_media_format(url: str) -> Optional[str]:
    low = (url or "").lower()
    path = urlparse(url).path.lower() if url else ""
    if "/get_file/" in low:
        return "mp4"
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mp4"):
        return "mp4"
    if "/embed/" in low and SITE_HOST in low:
        return "embed"
    return None


def _is_non_video_asset_url(url: str) -> bool:
    low = (url or "").lower()
    path = urlparse(url).path.lower() if url else ""
    if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg")):
        return True
    blocked = (
        "/screenshots/",
        "/thumb/",
        "/thumbs/",
        "/thumbnails/",
        "/poster/",
        "/preview.jpg",
        "cdntrex.com/contents/videos_screenshots",
    )
    return any(marker in low for marker in blocked)


def _is_preview_media_url(url: str) -> bool:
    path = urlparse(url).path.lower() if url else ""
    return "_preview.mp4" in path or path.endswith("/preview.mp4")


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    blocked = (
        "bongacams",
        "adspyglass",
        "doubleclick",
        "googlesyndication",
        "adservice",
        "exoclick",
        "trafficjunky",
        "popads",
        "theporndude",
        "jerky",
    )
    return any(marker in s for marker in blocked)


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for m in re.finditer(r"https?://[^\s\"'<>]+", unescaped, flags=re.IGNORECASE):
        u = m.group(0).strip()
        if u and _detect_media_format(u):
            urls.append(u)
    return list(dict.fromkeys(urls))


def _extract_kt_player_urls(html: str, page_url: str) -> list[tuple[str, str]]:
    """Return (key, resolved_url) pairs from kt_player config in inline scripts."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    quality_map = {
        "video_url": "default",
        "video_url_text": "default",
        "video_alt_url": "alt",
        "video_alt_url2": "alt2",
    }
    for pattern in _KT_PATTERNS:
        for m in re.finditer(pattern, html):
            raw = m.group(1).strip()
            key_m = re.match(r"(\w+)", m.group(0))
            key = key_m.group(1) if key_m else "video_url"
            resolved = _resolve_kt_url(raw, page_url)
            if not resolved.startswith("http"):
                continue
            if "get_file" not in resolved and not re.search(r"\.mp4|\.m3u8", resolved):
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append((quality_map.get(key, key), resolved))
    return found


def _stream_quality_from_url(url: str, *, label: str | None = None) -> str:
    if label and label not in ("default", "alt", "alt2", "source"):
        return label
    low = (url or "").lower()
    if _is_preview_media_url(url):
        return "preview"
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if "_720p" in low or "720p" in low:
        return "720p"
    if "_1080p" in low or "1080p" in low:
        return "1080p"
    if "_2160p" in low or "2160p" in low or "_4k" in low:
        return "2160p"
    if "_360p" in low:
        return "360p"
    if "_480p" in low:
        return "480p"
    if _detect_media_format(url) == "hls":
        return "adaptive"
    return label or "source"


def _embed_page_url(video_id: str) -> str:
    return f"https://www.porntrex.com/embed/{video_id}/"


def _is_video_detail_page(html: str, video_id: str | None) -> bool:
    """True when the HTML looks like a real video page, not a homepage/age-gate shell."""
    if not html or not video_id:
        return False
    if video_id not in html:
        return False
    low = html.lower()
    if "get_file" in low or "video_url" in low:
        return True
    if f"/embed/{video_id}" in low and "kt_player" in low:
        return True
    if soup_h1 := re.search(r"<h1[^>]*>([^<]{3,200})</h1>", html, re.IGNORECASE):
        h1 = _clean_title(soup_h1.group(1))
        if h1 and "best free hd porn" not in h1.lower():
            return True
    return False


def _list_card_container(item: Any) -> Any:
    """Find the nearest wrapper with this card's duration/views (avoid section-wide parents)."""
    best = item
    for parent in list(getattr(item, "parents", []))[:6]:
        if getattr(parent, "name", None) not in ("div", "li", "article"):
            continue
        if parent.select(".video-item[data-item-id], .thumb-item[data-item-id]"):
            siblings = parent.select(".video-item[data-item-id], .thumb-item[data-item-id]")
            if len(siblings) > 1:
                break
        text = parent.get_text(" ", strip=True) if hasattr(parent, "get_text") else ""
        if _extract_duration(text) or re.search(r"\bviews?\b", text, re.IGNORECASE):
            best = parent
    return best


def _parse_list_video_item(item: Any) -> Optional[dict[str, Any]]:
    vid = (item.get("data-item-id") or "").strip()
    link = item.select_one('a[href*="/video/"]') if hasattr(item, "select_one") else None
    if link is None:
        return None

    href = _normalize_video_href(link.get("href") or "")
    if not href and vid:
        slug = ""
        path_parts = (link.get("href") or "").strip("/").split("/")
        if len(path_parts) >= 3 and path_parts[-2] == vid:
            slug = path_parts[-1]
        href = _canonical_video_url(vid, slug or None)
    if not href:
        return None

    img = link.find("img") or item.select_one("img")
    thumb = _best_image_url(img)
    title = _clean_list_title(
        _first_non_empty(
            link.get("title"),
            img.get("alt") if img else None,
            link.get_text(" ", strip=True),
        )
    )

    container = _list_card_container(item)
    ctext = container.get_text(" ", strip=True) if hasattr(container, "get_text") else ""
    duration = None
    views = None
    for sel in (".duration", ".time", ".video-duration", ".thumb-duration", ".item-duration"):
        el = (item.select_one(sel) if hasattr(item, "select_one") else None) or (
            container.select_one(sel) if hasattr(container, "select_one") else None
        )
        if el and not duration:
            duration = _extract_duration(el.get_text(" ", strip=True))
    if not duration:
        duration = _extract_duration(ctext)
    views_el = (
        (item.select_one(".views") if hasattr(item, "select_one") else None)
        or (container.select_one(".views") if hasattr(container, "select_one") else None)
    )
    if views_el:
        views = _extract_views(views_el.get_text(" ", strip=True))
    if not views:
        views = _extract_views(ctext)

    return {
        "url": href,
        "title": title or "Unknown Video",
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": None,
    }


def _extract_native_embed_url(html: str, video_url: str) -> Optional[str]:
    m = re.search(
        r"https?://(?:www\.)?porntrex\.com/embed/\d+\b",
        html,
        flags=re.IGNORECASE,
    )
    if m:
        return m.group(0).strip().rstrip("/") + "/"
    vid = _extract_video_id(video_url)
    if vid:
        return f"https://www.porntrex.com/embed/{vid}/"
    return None


def _extract_streams(soup: BeautifulSoup, html: str, video_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for label, src in _extract_kt_player_urls(html, video_url):
        if src in seen:
            continue
        seen.add(src)
        streams.append(
            {
                "url": src,
                "quality": _stream_quality_from_url(src, label=label),
                "format": _detect_media_format(src) or "mp4",
            }
        )

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("//"):
            href = f"https:{href}"
        elif href.startswith("/"):
            href = urljoin(video_url, href)
        if _is_non_video_asset_url(href) or _is_probable_ad_iframe(href):
            continue
        fmt = _detect_media_format(href)
        if href.startswith("http") and href not in seen and fmt:
            seen.add(href)
            streams.append(
                {
                    "url": href,
                    "quality": _stream_quality_from_url(href),
                    "format": fmt,
                }
            )

    for video in soup.select("video"):
        for source in video.select("source[src]"):
            src = (source.get("src") or "").strip()
            if not src:
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(video_url, src)
            if _is_non_video_asset_url(src):
                continue
            fmt = _detect_media_format(src)
            if not src.startswith("http") or src in seen or not fmt:
                continue
            seen.add(src)
            streams.append(
                {
                    "url": src,
                    "quality": _stream_quality_from_url(src),
                    "format": fmt,
                }
            )

    for src in _extract_inline_urls(html):
        if src in seen or _is_non_video_asset_url(src):
            continue
        fmt = _detect_media_format(src)
        if not fmt:
            continue
        seen.add(src)
        streams.append(
            {
                "url": src,
                "quality": _stream_quality_from_url(src),
                "format": fmt,
            }
        )

    native_embed = _extract_native_embed_url(html, video_url)
    if native_embed and native_embed not in seen:
        seen.add(native_embed)
        streams.append({"url": native_embed, "quality": "porntrex", "format": "embed"})

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        stream_url = item.get("url") or ""
        qtxt = item.get("quality") or ""
        q = re.search(r"(\d{3,4})", qtxt)
        qnum = int(q.group(1)) if q else 0
        if fmt == "mp4":
            return (2, qnum) if _is_preview_media_url(stream_url) else (3, qnum)
        if fmt == "hls":
            return (2, qnum)
        if fmt == "embed" and f"{SITE_HOST}/embed/" in stream_url.lower():
            return (1, 1)
        return (1, 0)

    uniq = list(dict.fromkeys(json.dumps(s, sort_keys=True) for s in streams))
    materialized = [json.loads(s) for s in uniq]
    materialized.sort(key=_score, reverse=True)

    default_url = None
    for preferred in ("mp4", "hls", "embed"):
        match = next((s for s in materialized if s.get("format") == preferred), None)
        if match:
            default_url = match.get("url")
            break

    hls_url = next((s.get("url") for s in materialized if s.get("format") == "hls"), None)
    return {
        "streams": materialized,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(materialized),
    }


async def _get_file_to_remote_playable(get_file_url: str, *, referer: str) -> Optional[str]:
    base = get_file_url.split("?", 1)[0].strip().rstrip("/")
    ref = referer.strip() if referer.strip().startswith("http") else BASE_SITE
    headers = _browser_headers(ref)
    headers["Accept"] = "*/*"
    headers["Sec-Fetch-Dest"] = "video"
    headers["Sec-Fetch-Mode"] = "no-cors"
    headers["Sec-Fetch-Site"] = "same-origin"

    async def _attempt(url: str, method: str, range_hdr: Optional[str]) -> Optional[str]:
        h = dict(headers)
        if range_hdr:
            h["Range"] = range_hdr
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            if method == "HEAD":
                resp = await client.head(url, headers=h)
            else:
                resp = await client.get(url, headers=h)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if not loc:
                return None
            if _is_non_video_asset_url(loc) or _is_probable_ad_iframe(loc):
                return None
            fmt = _detect_media_format(loc)
            if fmt in ("mp4", "hls"):
                return loc
        return None

    attempts = [
        (f"{base}/", "HEAD", None),
        (f"{base}/", "GET", "bytes=0-"),
        (f"{base}/", "GET", "bytes=0-0"),
        (base, "HEAD", None),
        (base, "GET", "bytes=0-"),
        (base, "GET", "bytes=0-0"),
    ]
    for u, method, rng in attempts:
        try:
            resolved = await asyncio.wait_for(_attempt(u, method, rng), timeout=16.0)
            if resolved:
                return resolved
        except Exception:
            continue
    return None


def _url_contains_video_id(url: str, video_id: str) -> bool:
    low = (url or "").lower()
    vid = str(video_id).lower()
    return (
        f"/{vid}/" in low
        or f"/{vid}." in low
        or f"{vid}.mp4" in low
        or f"%2f{vid}%2f" in low
    )


async def _resolve_video_streams_to_remote_playable(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_mp4 = [
        s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "")
    ]
    if not get_file_mp4:
        return
    video_id = _extract_video_id(referer)

    async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
        resolved = await _get_file_to_remote_playable(stream["url"], referer=referer)
        return stream, resolved

    resolved_pairs = await asyncio.gather(*[_resolve_one(s) for s in get_file_mp4])
    for stream, resolved in resolved_pairs:
        if resolved:
            if video_id and not _url_contains_video_id(resolved, video_id):
                streams.remove(stream)
                continue
            stream["url"] = resolved
        else:
            # Keep original /get_file/ URL (playable with Referer header).
            pass

    direct_mp4 = [
        s for s in streams if s.get("format") == "mp4" and "get_file" not in (s.get("url") or "")
    ]
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)

    if direct_mp4:
        video["default"] = direct_mp4[0]["url"]
    elif get_file_mp4:
        video["default"] = get_file_mp4[0]["url"]
    elif hls:
        video["default"] = hls["url"]
    elif embed:
        video["default"] = embed["url"]
    else:
        video["default"] = None

    video["hls"] = hls["url"] if hls else None
    video["has_video"] = bool(streams)


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_video_href(url) or url

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    text_blob = soup.get_text(" ", strip=True)
    views_el = soup.select_one(".views, .video-info .views")
    views_text = views_el.get_text(" ", strip=True) if views_el else None
    duration = _extract_duration(text_blob)
    views = _extract_views(views_text) or _extract_views(text_blob)

    tags: list[str] = []
    for el in soup.select(".tags a, a[href*='/tags/'], a[href*='/category/']"):
        tag = el.get_text(" ", strip=True)
        if tag and tag not in tags and len(tag) < 80:
            tags.append(tag)
    tags = list(dict.fromkeys(tags))

    uploader = None
    up = soup.select_one('a[href*="/models/"], a[href*="/channels/"], a[href*="/members/"]')
    if up:
        uploader = up.get_text(" ", strip=True) or None

    return {
        "url": canon,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": _meta(soup, prop="article:section"),
        "tags": tags or None,
        "upload_date": _first_non_empty(
            _meta(soup, prop="article:published_time"),
            _meta(soup, prop="article:modified_time"),
        ),
        "video": _extract_streams(soup, html, canon),
        "related_videos": [],
        "preview_url": None,
    }


async def _fetch_embed_html(video_id: str, *, referer: str) -> str:
    return await fetch_page(_embed_page_url(video_id), referer=referer)


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url)
    if not canon:
        raise ValueError(f"Unsupported PornTrex URL: {url}")

    video_id = _extract_video_id(canon)
    html = await fetch_page(canon, referer=canon)
    data = parse_video_page(html, canon)

    embed_html: str | None = None
    if video_id:
        try:
            embed_html = await _fetch_embed_html(video_id, referer=canon)
        except Exception:
            embed_html = None

    if embed_html:
        embed_soup = BeautifulSoup(embed_html, "lxml")
        embed_video = _extract_streams(embed_soup, embed_html, canon)
        main_video = data.get("video") or {}
        main_has_playable = any(
            s.get("format") in ("mp4", "hls")
            and "get_file" in (s.get("url") or "")
            for s in (main_video.get("streams") or [])
        )
        if embed_video.get("has_video") and not main_has_playable:
            data["video"] = embed_video

        if not _is_video_detail_page(html, video_id):
            embed_title = _clean_title(
                embed_soup.title.get_text(strip=True) if embed_soup.title else None
            )
            if embed_title:
                data["title"] = embed_title
            slug_title = _clean_title(
                (urlparse(canon).path or "").strip("/").split("/")[-1].replace("-", " ")
            )
            if slug_title and (not data.get("title") or data.get("title") == "Unknown Video"):
                data["title"] = slug_title

    await _resolve_video_streams_to_remote_playable(data.get("video", {}), referer=canon)
    return data


def _normalize_list_base_url(base_url: str) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(_sanitize_url(raw))
    path = (parsed.path or "/").strip("/")
    list_path = f"/{path}/" if path else "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", list_path, "", "", ""))


def _build_list_page_url(base_url: str, page: int) -> str:
    base = _normalize_list_base_url(base_url)
    parsed = urlparse(base)
    page_num = max(1, int(page) if page else 1)
    path = (parsed.path or "/").strip("/")
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if page_num <= 1:
        list_path = f"/{path}/" if path else "/"
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", list_path, "", urlencode(query_items), "")
        )

    if path and re.search(r"/\d+$", f"/{path}"):
        path = re.sub(r"/\d+$", "", path)

    if "page" in query_items:
        query_items["page"] = str(page_num)
        list_path = f"/{path}/" if path else "/"
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", list_path, "", urlencode(query_items), "")
        )

    list_path = f"/{path}/{page_num}/" if path else f"/{page_num}/"
    return urlunparse(
        (parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", list_path, "", urlencode(query_items), "")
    )


def _list_page_candidates(base_url: str, page: int) -> list[str]:
    primary = _build_list_page_url(base_url, page)
    candidates = [primary]
    parsed = urlparse(_normalize_list_base_url(base_url))
    path = (parsed.path or "/").strip("/")
    if page <= 1 and path in ("", "latest-updates"):
        for alt in (BASE_SITE, f"{BASE_SITE}latest-updates/"):
            if alt not in candidates:
                candidates.append(_sanitize_url(alt))
    return candidates


def _is_list_page_html(html: str) -> bool:
    if not html or len(html) < 5000:
        return False
    if "adult website" in html.lower() and "data-item-id" not in html:
        return False
    return bool(
        re.search(r'data-item-id=["\']\d+["\']', html)
        or re.search(r'class=["\'][^"\']*video-item[^"\']*["\']', html, re.IGNORECASE)
    )


def _parse_list_html(html: str, *, limit: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    effective_limit = max(1, int(limit)) if limit else 100

    def _upsert(entry: dict[str, Any]) -> None:
        href = entry.get("url")
        if not href or href in seen:
            return
        seen.add(href)
        items.append(entry)

    for card in soup.select(
        ".video-item[data-item-id], .thumb-item[data-item-id], [data-item-id].video-item"
    ):
        if len(items) >= effective_limit:
            break
        parsed = _parse_list_video_item(card)
        if parsed:
            _upsert(parsed)

    if len(items) < effective_limit:
        for a in soup.select("a[href*='/video/']"):
            if len(items) >= effective_limit:
                break
            href = _normalize_video_href(a.get("href") or "")
            if not href:
                continue

            if href in seen:
                for i, existing in enumerate(items):
                    if existing.get("url") != href:
                        continue
                    container = _list_card_container(a.find_parent(["article", "li", "div"]) or a)
                    img = a.find("img") or (container.find("img") if container else None)
                    title = _clean_list_title(
                        _first_non_empty(
                            a.get("title"),
                            img.get("alt") if img else None,
                            a.get_text(" ", strip=True),
                        )
                    )
                    if title and title != "Unknown Video":
                        existing["title"] = title
                    thumb = _best_image_url(img)
                    if thumb:
                        existing["thumbnail_url"] = thumb
                    ctext = container.get_text(" ", strip=True) if container else ""
                    existing["duration"] = existing.get("duration") or _extract_duration(ctext)
                    existing["views"] = existing.get("views") or _extract_views(ctext)
                continue

            container = _list_card_container(a.find_parent(["article", "li", "div"]) or a)
            img = a.find("img") or (container.find("img") if container else None)
            thumb = _best_image_url(img)
            title = _clean_list_title(
                _first_non_empty(
                    a.get("title"),
                    img.get("alt") if img else None,
                    a.get_text(" ", strip=True),
                )
            ) or "Unknown Video"
            ctext = container.get_text(" ", strip=True) if container else ""
            duration = _extract_duration(ctext)
            views_el = container.select_one(".views") if container else None
            views_text = views_el.get_text(" ", strip=True) if views_el else None
            views = _extract_views(views_text) or _extract_views(ctext)

            _upsert(
                {
                    "url": href,
                    "title": title,
                    "thumbnail_url": thumb,
                    "duration": duration,
                    "views": views,
                    "uploader_name": None,
                }
            )

    if len(items) < effective_limit:
        for vid, slug in _VIDEO_HREF_RE.findall(str(soup)):
            if len(items) >= effective_limit:
                break
            href = _canonical_video_url(vid, slug)
            if href in seen:
                continue
            _upsert(
                {
                    "url": href,
                    "title": "Unknown Video",
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                }
            )

    return items[:effective_limit]


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    referer = _normalize_list_base_url(base_url)
    effective_limit = max(1, int(limit)) if limit else 100
    last_error: Exception | None = None

    for page_url in _list_page_candidates(base_url, page):
        try:
            html = await fetch_page(page_url, referer=referer)
        except Exception as exc:
            last_error = exc
            logger.warning("PornTrex list fetch failed for %s: %s", page_url, exc)
            continue

        if not _is_list_page_html(html):
            logger.warning("PornTrex list page invalid HTML from %s", page_url)
            continue

        items = _parse_list_html(html, limit=effective_limit)
        if items:
            return items

    if last_error:
        logger.error("PornTrex list_videos exhausted candidates for %s page %s: %s", base_url, page, last_error)
    return []


async def crawl_videos(
    base_url: str,
    start_page: int = 1,
    max_pages: int = 5,
    per_page_limit: int = 0,
    max_items: int = 500,
) -> list[dict[str, Any]]:
    if start_page < 1:
        start_page = 1
    if max_pages < 1:
        max_pages = 1
    if per_page_limit < 0:
        per_page_limit = 0
    if max_items < 1:
        max_items = 1

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_limit = per_page_limit if per_page_limit > 0 else 100

    for page in range(start_page, start_page + max_pages):
        page_items = await list_videos(base_url=base_url, page=page, limit=page_limit)
        if not page_items:
            break
        for it in page_items:
            url = str(it.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            results.append(it)
            if len(results) >= max_items:
                return results
    return results
