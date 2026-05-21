from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html, get_random_user_agent

BASE_SITE = "https://www.porntrex.com/"
SITE_HOST = "porntrex.com"
SITE_ALIASES = frozenset({"porntrex.com", "www.porntrex.com"})

_DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": BASE_SITE,
    "Origin": "https://www.porntrex.com",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Cookie": "age_pass=1; accessAgeDisclaimerPH=1; accessAgeDisclaimerUK=1",
}

_HOMEPAGE_PATHS = frozenset({"", "/", "//"})
_LIST_FALLBACK_PATH = "/latest-updates"
_KVS_PAGE_SIZE = 32

_VIDEO_PATH_RE = re.compile(r"^/video/(\d+)/[^/]+/?$", re.IGNORECASE)
_KT_URL_KEYS = r"(?:video_url|video_url_text|video_alt_url|video_alt_url2|url|file)"
_KT_PATTERNS = [
    rf"{_KT_URL_KEYS}\s*[:=]\s*['\"]([^'\"]+)['\"]",
    r"['\"]?quality_(\d+p|adaptive)['\"]?\s*:\s*['\"]([^'\"]+)['\"]",
]


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".porntrex.com")


def _normalize_site_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    path = parsed.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or f"www.{SITE_HOST}",
            path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def _browser_headers(*, referer: str | None = None, accept: str | None = None) -> dict[str, str]:
    ref = _normalize_site_url(referer or BASE_SITE)
    headers = dict(_DEFAULT_HEADERS)
    headers["User-Agent"] = get_random_user_agent()
    headers["Referer"] = ref
    headers["Sec-Fetch-Site"] = "same-origin" if SITE_HOST in ref else "cross-site"
    if accept:
        headers["Accept"] = accept
    return headers


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    target = _normalize_site_url(url)
    return await pool_fetch_html(target, headers=_browser_headers(referer=referer))


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    for suffix in (" / Embed Player", " - PornTrex", " | PornTrex", " - porntrex.com", " | porntrex.com"):
        if t.lower().endswith(suffix.lower()):
            t = t[: -len(suffix)].strip()
    return t or None


def _clean_list_title(title: str | None) -> Optional[str]:
    t = _clean_title(title)
    if not t:
        return None
    t = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s+\d{1,3}%\s+\d.*$", "", t).strip()
    return t or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b|\b\d{1,2}:\d{2}\b", text)
    return m.group(0) if m else None


def _extract_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(\d[\d\s,\.]*[KMBkmb]?)\s*views?", text, flags=re.IGNORECASE)
    return m.group(1).strip().upper() if m else None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy-src", "src"):
        v = img.get(key)
        if v and not str(v).startswith("data:"):
            url = str(v).strip()
            return urljoin(BASE_SITE, url) if url.startswith("/") else url
    return None


def _canonical_video_url(video_id: str, slug: str | None = None) -> str:
    return f"https://www.porntrex.com/video/{video_id}/{slug.strip('/') if slug else 'video'}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://www.porntrex.com{href}"
    parsed = urlparse(href)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 2 and parts[0].lower() == "video":
        return _canonical_video_url(parts[1], parts[2] if len(parts) > 2 else None)
    return None


def _detect_media_format(url: str) -> Optional[str]:
    low = (url or "").lower()
    if ".m3u8" in low:
        return "hls"
    if ".mp4" in low or "/get_file/" in low:
        return "mp4"
    if "/embed/" in low:
        return "embed"
    return None


def _extract_kt_player_urls(html: str, page_url: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    
    # Clean escaped slashes in the Javascript output strings
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    
    for pattern in _KT_PATTERNS:
        for m in re.finditer(pattern, unescaped):
            raw = m.group(len(m.groups())).strip()
            if not raw.startswith("http") and not raw.startswith("/") and not raw.startswith("//"):
                continue
            resolved = urljoin(page_url, raw) if raw.startswith("/") else raw
            if resolved in seen:
                continue
            seen.add(resolved)
            
            # Detect implicit or declared labels
            label = "source"
            if "720" in resolved or (m.lastindex and "720" in m.group(1)):
                label = "720p"
            elif "1080" in resolved or (m.lastindex and "1080" in m.group(1)):
                label = "1080p"
            elif "480" in resolved or (m.lastindex and "480" in m.group(1)):
                label = "480p"
                
            found.append((label, resolved))
    return found


def _extract_streams(soup: BeautifulSoup, html: str, video_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    # 1. Parse using config matching mechanics
    for label, src in _extract_kt_player_urls(html, video_url):
        fmt = _detect_media_format(src)
        if fmt and src not in seen:
            seen.add(src)
            streams.append({"url": src, "quality": label, "format": fmt})

    # 2. Extract explicit video tag nodes
    for video in soup.select("video"):
        for source in video.select("source[src]"):
            src = urljoin(video_url, source.get("src", "").strip())
            fmt = _detect_media_format(src)
            if fmt and src not in seen:
                seen.add(src)
                streams.append({"url": src, "quality": "source", "format": fmt})

    # 3. Fallback to general DOM anchors parsing
    for a in soup.select("a[href]"):
        href = urljoin(video_url, a.get("href", "").strip())
        fmt = _detect_media_format(href)
        if fmt and "get_file" in href and href not in seen:
            seen.add(href)
            streams.append({"url": href, "quality": "download", "format": fmt})

    # Fallback default evaluation object assignment selection setup
    default_url = streams[0]["url"] if streams else None
    hls_url = next((s["url"] for s in streams if s["format"] == "hls"), None)

    return {
        "streams": streams,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(streams),
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 32) -> list[dict[str, Any]]:
    target_url = base_url
    if page > 1:
        # Construct standard pagination query string params matching backend expectations
        parsed = urlparse(base_url)
        q = dict(parse_qsl(parsed.query))
        q["from"] = str((page - 1) * _KVS_PAGE_SIZE)
        target_url = urlunparse(parsed._replace(query=urlencode(q)))

    html = await fetch_page(target_url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []

    # Target selectors covering classic listing templates
    selectors = [
        ".video-item", 
        ".thumb-item", 
        "div[data-item-id]",
        ".items .item"
    ]
    
    found_elements = []
    for selector in selectors:
        found_elements = soup.select(selector)
        if found_elements:
            break

    for el in found_elements:
        parsed_item = _parse_list_video_item(el)
        if parsed_item:
            items.append(parsed_item)
            if len(items) >= limit:
                break

    return items


def _parse_list_video_item(item: Any) -> Optional[dict[str, Any]]:
    link = item.select_one('a[href*="/video/"]')
    if not link:
        return None

    href = _normalize_video_href(link.get("href", ""))
    if not href:
        return None

    img = item.select_one("img")
    thumb = _best_image_url(img)
    
    raw_title = link.get("title") or (img.get("alt") if img else None) or item.get_text()
    title = _clean_list_title(raw_title)

    text_content = item.get_text(" ", strip=True)
    duration = _extract_duration(text_content)
    views = _extract_views(text_content)

    return {
        "url": href,
        "title": title or "Unknown Video",
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": None,
    }


async def crawl_videos(
    base_url: str,
    start_page: int = 1,
    max_pages: int = 5,
    per_page_limit: int = 32,
    max_items: int = 500,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for page in range(start_page, start_page + max_pages):
        page_items = await list_videos(base_url=base_url, page=page, limit=per_page_limit)
        if not page_items:
            break
        for it in page_items:
            if it["url"] not in seen:
                seen.add(it["url"])
                results.append(it)
            if len(results) >= max_items:
                return results[:max_items]
    return results