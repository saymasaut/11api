from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://www.porntrex.com/"
SITE_HOST = "porntrex.com"
SITE_ALIASES = frozenset({"porntrex.com", "www.porntrex.com"})

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
    "Cookie": "age_pass=1",
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
    for suffix in (
        " - PornTrex",
        " | PornTrex",
        " - porntrex.com",
        " | porntrex.com",
        " - PornTrex.com",
        " | PornTrex.com",
    ):
        if t.lower().endswith(suffix.lower()):
            t = t[: -len(suffix)].strip()
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
    raw = str(text).strip()
    m = re.search(
        r"(\d[\d\s,\.]*)\s*views?\b",
        raw,
        flags=re.IGNORECASE,
    )
    if m:
        return _normalize_numberish(m.group(1))
    m = re.search(
        r"\bviews?\s*[:\-]?\s*(\d[\d\s,\.]*\s*[KMBkmb]?)\b",
        raw,
        flags=re.IGNORECASE,
    )
    if m:
        return _normalize_numberish(m.group(1))
    m = re.search(r"\b(\d[\d\s,\.]*\s*[KMBkmb])\b", raw, flags=re.IGNORECASE)
    if m:
        return _normalize_numberish(m.group(1))
    m = re.search(r"(\d[\d\s,\.]+)", raw)
    if m:
        return _normalize_numberish(m.group(1))
    return None


def _normalize_asset_url(url: str) -> Optional[str]:
    url = (url or "").strip()
    if not url or url.startswith("data:"):
        return None
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return urljoin(BASE_SITE, url)
    if url.startswith("http"):
        return url
    return None


def _url_from_style(style: str | None) -> Optional[str]:
    if not style:
        return None
    m = re.search(r"url\(['\"]?(.*?)['\"]?\)", style, flags=re.IGNORECASE)
    if not m:
        return None
    return _normalize_asset_url(m.group(1))


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
        normalized = _normalize_asset_url(url)
        if normalized:
            return normalized
    return None


def _thumbnail_from_screenshot_item(el: Any) -> Optional[str]:
    """PornTrex cards use .screenshot-item (img or background on ptx.cdntrex.com)."""
    if el is None:
        return None

    for attr in ("data-src", "data-original", "data-thumb", "src"):
        normalized = _normalize_asset_url(str(el.get(attr) or ""))
        if normalized and "videos_screenshots" in normalized.lower():
            return normalized

    from_style = _url_from_style(el.get("style"))
    if from_style and "videos_screenshots" in from_style.lower():
        return from_style

    if getattr(el, "name", None) == "img":
        return _best_image_url(el)

    img = el.select_one("img.screenshot-item, img") if hasattr(el, "select_one") else None
    if img:
        thumb = _best_image_url(img)
        if thumb:
            return thumb

    return None


def _find_list_block(el: Any) -> Any:
    for parent in el.parents:
        classes = " ".join(parent.get("class") or []).lower()
        if any(
            token in classes
            for token in ("video-item", "thumb-block", "thumb", "item-thumb", "video-thumb")
        ):
            return parent
    return el.parent


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    blocks: list[Any] = []
    for sel in (
        "div.video-item",
        "div.thumb-block",
        "div.thumb",
        "li.video-item",
        ".videos-list .item",
        ".list-videos .item",
    ):
        blocks.extend(soup.select(sel))

    if not blocks:
        for title_el in soup.select(".video-item-title"):
            blocks.append(_find_list_block(title_el))

    for block in blocks:
        if len(items) >= limit:
            break

        link = block.select_one("a[href*='/video/']") if hasattr(block, "select_one") else None
        href = _normalize_video_href(link.get("href") or "") if link else None
        if not href:
            m = _VIDEO_HREF_RE.search(str(block))
            if m:
                href = _canonical_video_url(m.group(1), m.group(2))
        if not href or href in seen:
            continue
        seen.add(href)

        title_el = (
            block.select_one(".video-item-title")
            if hasattr(block, "select_one")
            else None
        )
        title = _clean_list_title(
            _first_non_empty(
                title_el.get_text(" ", strip=True) if title_el else None,
                link.get("title") if link else None,
                link.get_text(" ", strip=True) if link else None,
            )
        ) or "Unknown Video"

        screenshot_el = (
            block.select_one(".screenshot-item, img.screenshot-item")
            if hasattr(block, "select_one")
            else None
        )
        thumb = _thumbnail_from_screenshot_item(screenshot_el)
        if not thumb and link:
            thumb = _thumbnail_from_screenshot_item(link.find("img")) or _best_image_url(
                link.find("img")
            )

        dur_el = (
            block.select_one(".video-item-duration, .info.video-item-duration")
            if hasattr(block, "select_one")
            else None
        )
        duration = _extract_duration(
            dur_el.get_text(" ", strip=True) if dur_el else None
        )

        views_el = (
            block.select_one(".video-item-views, .info.video-item-views")
            if hasattr(block, "select_one")
            else None
        )
        views = _extract_views(views_el.get_text(" ", strip=True) if views_el else None)

        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": None,
            }
        )

    return items[:limit]


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
    headers = {
        "User-Agent": _DEFAULT_HEADERS["User-Agent"],
        "Referer": ref,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": _DEFAULT_HEADERS.get("Cookie", ""),
    }

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


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_video_href(url)
    if not canon:
        raise ValueError(f"Unsupported PornTrex URL: {url}")

    html = await fetch_page(canon, referer=canon)
    data = parse_video_page(html, canon)
    await _resolve_video_streams_to_remote_playable(data.get("video", {}), referer=canon)
    return data


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = f"{BASE_SITE.rstrip('/')}/{raw.lstrip('/')}"
    parsed = urlparse(raw)
    page_num = max(1, int(page) if page else 1)

    path = (parsed.path or "/").rstrip("/") or ""
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if page_num <= 1:
        new_path = path or "/"
        return urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc or f"www.{SITE_HOST}",
                new_path + ("/" if new_path != "/" else "/"),
                "",
                urlencode(query_items),
                "",
            )
        )

    if re.search(r"/\d+/?$", path):
        path = re.sub(r"/\d+/?$", "", path)

    if "page" not in query_items:
        new_path = f"{path}/{page_num}" if path else f"/{page_num}"
        return urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc or f"www.{SITE_HOST}",
                new_path + "/",
                "",
                urlencode(query_items),
                "",
            )
        )

    query_items["page"] = str(page_num)
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or f"www.{SITE_HOST}",
            path + "/" if path else "/",
            "",
            urlencode(query_items),
            "",
        )
    )


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items = _parse_list_items(soup, limit=limit)
    if items:
        return items

    # Fallback when markup lacks video-item wrappers (older/alternate templates).
    seen: set[str] = set()
    for a in soup.select("a[href*='/video/']"):
        if len(items) >= limit:
            break
        href = _normalize_video_href(a.get("href") or "")
        if not href or href in seen:
            continue
        seen.add(href)
        container = a.find_parent(["article", "li", "div"]) or a
        screenshot_el = container.select_one(".screenshot-item, img.screenshot-item")
        thumb = _thumbnail_from_screenshot_item(screenshot_el) or _best_image_url(
            a.find("img") or container.find("img")
        )
        title_el = container.select_one(".video-item-title")
        dur_el = container.select_one(".video-item-duration, .info.video-item-duration")
        views_el = container.select_one(".video-item-views, .info.video-item-views")
        items.append(
            {
                "url": href,
                "title": _clean_list_title(
                    _first_non_empty(
                        title_el.get_text(" ", strip=True) if title_el else None,
                        a.get("title"),
                        a.get_text(" ", strip=True),
                    )
                )
                or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": _extract_duration(
                    dur_el.get_text(" ", strip=True) if dur_el else None
                ),
                "views": _extract_views(
                    views_el.get_text(" ", strip=True) if views_el else None
                ),
                "uploader_name": None,
            }
        )

    return items[:limit]
