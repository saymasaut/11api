from __future__ import annotations

import asyncio
import json
import os
import re
import time
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

_VIDEO_PATH_RE = re.compile(r"^/video/(\d+)(?:/([^/]+))?/?$", re.IGNORECASE)
_VIDEO_HREF_RE = re.compile(
    r'href=["\'](?:https?://(?:www\.)?porntrex\.com)?/video/(\d+)(?:/([^"\']*?))?/?["\']',
    re.IGNORECASE,
)
_VIDEO_PATH_INLINE_RE = re.compile(
    r"(?:https?://(?:www\.)?porntrex\.com)?/video/(\d+)(?:/([\w\-]+))?/?",
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
                if resp.status_code == 200:
                    return resp.text
        except Exception:
            continue
    return None


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer or BASE_SITE
    text = await _fetch_with_curl_cffi(url)
    if text:
        return text
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
    t = re.sub(r"\s+", " ", t)
    for suffix in (
        " - PornTrex",
        " | PornTrex",
        " - porntrex.com",
        " | porntrex.com",
        " - PornTrex.com",
        " | PornTrex.com",
        " — PornTrex",
    ):
        if t.lower().endswith(suffix.lower()):
            t = t[: -len(suffix)].strip()
    # Strip trailing watch-page stats accidentally merged into og:title.
    t = re.sub(
        r"\s+\d{1,3}:\d{2}(?::\d{2})?\s+\d[\d\s,\.]*\s*views?\s*$",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    t = re.sub(r"\s+\d{1,3}:\d{2}(?::\d{2})?\s*$", "", t).strip()
    return t or None


def _seconds_to_duration(seconds: int) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _normalize_duration(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.isdigit():
        return _seconds_to_duration(int(raw))

    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", raw, flags=re.IGNORECASE)
    if m:
        h = int(m.group(1) or 0)
        mm = int(m.group(2) or 0)
        s = int(m.group(3) or 0)
        return _seconds_to_duration(h * 3600 + mm * 60 + s)

    m = re.search(r"\b(\d{1,3}):(\d{2})(?::(\d{2}))?\b", raw)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if c:
            return f"{a}:{b:02d}:{c:02d}"
        if a > 59:
            return _seconds_to_duration(a * 60 + b)
        return f"{a}:{b:02d}"

    return None


def _extract_duration(text: str | None) -> Optional[str]:
    return _normalize_duration(text)


_QUALITY_PREFIX_RE = re.compile(
    r"^(?:(?:4k|2160p|1440p|1080p|720p|480p|360p|hd|vr)\s*)+",
    re.IGNORECASE,
)


def _parse_list_card_text(raw: str | None) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    PornTrex cards often concatenate metadata into one string, e.g.
    ``23:1059 853 views 95%1080p HDTitle`` (duration glued to views count).
    """
    blob = (raw or "").strip()
    if not blob:
        return None, None, None

    duration: Optional[str] = None
    views: Optional[str] = None
    title = blob

    m = re.match(r"^(\d{1,2}:\d{2}(?::\d{2})?)", title)
    if m:
        duration = m.group(1)
        title = title[m.end() :].lstrip()

    m = re.match(r"^(\d[\d\s,\.]{0,14}?)\s*views?\b", title, flags=re.IGNORECASE)
    if m:
        views = _normalize_numberish(m.group(1))
        title = title[m.end() :].lstrip()
    else:
        m = re.match(r"^(\d{1,3}(?:\s+\d{3})+)", title)
        if m:
            views = _normalize_numberish(m.group(1))
            title = title[m.end() :].lstrip()

    title = re.sub(r"^\d{1,3}%\s*", "", title).strip()
    title = _QUALITY_PREFIX_RE.sub("", title).strip()
    title = _clean_title(title) or None
    return title, duration, views


def _clean_list_title(title: str | None) -> Optional[str]:
    parsed_title, _, _ = _parse_list_card_text(title)
    if parsed_title:
        return parsed_title
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


def _list_title_raw(title_el: Any, link: Any) -> Optional[str]:
    if title_el is not None:
        anchor = title_el if getattr(title_el, "name", None) == "a" else title_el.select_one("a")
        if anchor is not None:
            return _first_non_empty(anchor.get("title"), anchor.get_text(" ", strip=True))
        chunks: list[str] = []
        for child in title_el.children:
            name = getattr(child, "name", None)
            if name and name != "a":
                classes = " ".join(child.get("class") or []).lower()
                if "info" in classes or "video-item-duration" in classes or "video-item-views" in classes:
                    continue
            text = (
                child.get_text(" ", strip=True)
                if hasattr(child, "get_text")
                else str(child).strip()
            )
            if text:
                chunks.append(text)
        if chunks:
            return " ".join(chunks)
        return title_el.get_text(" ", strip=True) or None
    if link is not None:
        return _first_non_empty(link.get("title"), link.get("aria-label"))
    return None


def _list_duration_from_block(block: Any, *, fallback_text: str | None = None) -> Optional[str]:
    if hasattr(block, "select_one"):
        for sel in (
            ".video-item-duration",
            ".info.video-item-duration",
            ".duration",
            ".thumb-duration",
        ):
            el = block.select_one(sel)
            if el:
                dur = _normalize_duration(el.get_text(" ", strip=True) or el.get("content"))
                if dur:
                    return dur
    _, dur, _ = _parse_list_card_text(fallback_text)
    return dur


def _normalize_numberish(value: str | None) -> Optional[str]:
    if not value:
        return None
    txt = str(value).strip().replace(",", "").replace("\u00a0", " ")
    txt = re.sub(r"\s+", "", txt)
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


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


def _card_container_for_anchor(anchor: Any) -> Any:
    for parent in anchor.parents:
        if getattr(parent, "name", None) in ("body", "html", "[document]"):
            break
        classes = " ".join(parent.get("class") or []).lower()
        if any(
            token in classes
            for token in (
                "video-item",
                "thumb-block",
                "thumb",
                "item-thumb",
                "video-thumb",
                "thumb-list",
                "videos-list",
                "list-videos",
            )
        ):
            return parent
    return anchor.parent


def _list_item_from_context(
    *,
    href: str,
    link: Any | None,
    block: Any | None,
) -> dict[str, Any]:
    title_el = None
    screenshot_el = None
    views_el = None
    if block is not None and hasattr(block, "select_one"):
        title_el = block.select_one(".video-item-title, a.video-item-title")
        screenshot_el = block.select_one(".screenshot-item, img.screenshot-item")
        views_el = block.select_one(".video-item-views, .info.video-item-views")

    raw_title = _list_title_raw(title_el, link) or (
        link.get_text(" ", strip=True) if link is not None else None
    )
    parsed_title, parsed_dur, parsed_views = _parse_list_card_text(raw_title)
    title = (
        parsed_title
        or _clean_list_title(raw_title)
        or _clean_list_title(link.get("title") if link is not None else None)
        or "Unknown Video"
    )

    thumb = _thumbnail_from_screenshot_item(screenshot_el)
    if not thumb and link is not None:
        thumb = _thumbnail_from_screenshot_item(link.find("img")) or _best_image_url(
            link.find("img")
        )
    if not thumb and block is not None:
        thumb = _thumbnail_from_screenshot_item(
            block.select_one(".screenshot-item, img.screenshot-item, img")
        )

    duration = (
        _list_duration_from_block(block, fallback_text=raw_title) if block is not None else None
    ) or parsed_dur
    views = (
        _extract_views(views_el.get_text(" ", strip=True) if views_el else None)
        if views_el is not None
        else None
    ) or parsed_views

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": None,
    }


def _parse_list_items(soup: BeautifulSoup, *, limit: int, html: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(href: str, link: Any | None = None, block: Any | None = None) -> None:
        if len(items) >= limit:
            return
        canon = _normalize_video_href(href)
        if not canon or canon in seen:
            return
        seen.add(canon)
        ctx_block = block
        if link is not None and ctx_block is None:
            ctx_block = _card_container_for_anchor(link)
        items.append(
            _list_item_from_context(href=canon, link=link, block=ctx_block)
        )

    for link in soup.select("a[href*='/video/']"):
        _add(link.get("href") or "", link=link)

    if len(items) < limit:
        for block in soup.select(
            "div.video-item, div.thumb-block, div.thumb, li.video-item, "
            ".videos-list .item, .list-videos .item, .thumbs .thumb"
        ):
            if len(items) >= limit:
                break
            inner = block.select_one("a[href*='/video/']") if hasattr(block, "select_one") else None
            if not inner:
                continue
            _add(inner.get("href") or "", link=inner, block=block)

    if len(items) < limit and html:
        for vid, slug in _VIDEO_PATH_INLINE_RE.findall(html):
            slug = (slug or "").strip() or None
            _add(_canonical_video_url(vid, slug))

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
    if not href or href.startswith("#") or href.lower().startswith("javascript:"):
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://www.porntrex.com{href}"

    parsed = urlparse(href.split("#", 1)[0])
    host = (parsed.netloc or "").lower()
    if host and SITE_HOST not in host:
        return None

    m = _VIDEO_PATH_RE.match(parsed.path or "")
    if not m:
        inline = _VIDEO_PATH_INLINE_RE.search(href)
        if not inline:
            return None
        vid, slug = inline.group(1), (inline.group(2) or "").strip() or None
        return _canonical_video_url(vid, slug)

    vid = m.group(1)
    slug = (m.group(2) or "").strip() or None
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
        if resp.status_code == 200 and method != "HEAD":
            ct = (resp.headers.get("content-type") or "").lower()
            if "video" in ct or "octet-stream" in ct:
                return url
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if not loc:
                return None
            if loc.startswith("//"):
                loc = f"https:{loc}"
            elif loc.startswith("/"):
                loc = urljoin(BASE_SITE, loc)
            if _is_non_video_asset_url(loc) or _is_probable_ad_iframe(loc):
                return None
            loc_low = loc.lower()
            if "remote_control.php" in loc_low or "cdntrex.com" in loc_low:
                return loc
            fmt = _detect_media_format(loc)
            if fmt in ("mp4", "hls"):
                return loc
        return None

    rnd = int(time.time() * 1000)
    attempts = [
        (f"{base}/?rnd={rnd}", "HEAD", None),
        (f"{base}/?rnd={rnd}", "GET", "bytes=0-"),
        (f"{base}/?rnd={rnd}", "GET", "bytes=0-0"),
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
        or f"%2f{vid}.mp4" in low
        or f"file=%2f{vid}" in low
        or f"file=/{vid}" in low
    )


def _mp4_stream_score(item: dict[str, str]) -> int:
    q = item.get("quality") or ""
    m = re.search(r"(\d{3,4})", str(q))
    return int(m.group(1)) if m else 0


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
            # CDN signed URLs (remote_control.php) rarely embed the numeric id in the path.
            if video_id and not _url_contains_video_id(resolved, video_id):
                if _detect_media_format(resolved) not in ("mp4", "hls"):
                    continue
            stream["url"] = resolved
        # If redirect resolution fails, keep the original /get_file/ URL (Referer required).

    mp4_streams = [s for s in streams if s.get("format") == "mp4"]
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)

    if mp4_streams:
        mp4_streams.sort(key=_mp4_stream_score, reverse=True)
        video["default"] = mp4_streams[0]["url"]
    elif hls:
        video["default"] = hls["url"]
    elif embed:
        video["default"] = embed["url"]
    else:
        video["default"] = None

    video["hls"] = hls["url"] if hls else None
    video["has_video"] = bool(mp4_streams) or bool(hls) or bool(embed)


def _parse_json_ld_video(soup: BeautifulSoup) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=False)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        objs = parsed if isinstance(parsed, list) else [parsed]
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            if t != "VideoObject" and not (isinstance(t, list) and "VideoObject" in t):
                continue
            out["title"] = _first_non_empty(out.get("title"), obj.get("name"))
            out["description"] = _first_non_empty(out.get("description"), obj.get("description"))
            thumb = obj.get("thumbnailUrl")
            if isinstance(thumb, list) and thumb:
                thumb = thumb[0]
            out["thumbnail"] = _first_non_empty(out.get("thumbnail"), thumb)
            out["duration"] = _first_non_empty(
                out.get("duration"), _normalize_duration(obj.get("duration"))
            )
            iv = obj.get("interactionCount") or obj.get("viewCount")
            if iv is not None:
                out["views"] = str(iv)
    return out


def _extract_watch_duration(soup: BeautifulSoup, html: str) -> Optional[str]:
    for sel in (
        ".video-item-duration",
        ".info.video-item-duration",
        ".duration",
        ".block-duration",
        ".video-duration",
        "#duration",
        "[itemprop='duration']",
    ):
        el = soup.select_one(sel)
        if el:
            dur = _normalize_duration(el.get("content") or el.get_text(" ", strip=True))
            if dur:
                return dur

    dur_meta = _meta(soup, prop="video:duration") or _meta(soup, name="duration")
    if dur_meta:
        dur = _normalize_duration(dur_meta)
        if dur:
            return dur

    for pattern in (
        r"video_duration\s*[=:]\s*['\"](\d+)['\"]",
        r"duration\s*[=:]\s*['\"](\d{1,3}:\d{2}(?::\d{2})?)['\"]",
        r"video_duration\s*[=:]\s*(\d{1,3}:\d{2}(?::\d{2})?)",
    ):
        m = re.search(pattern, html, flags=re.IGNORECASE)
        if m:
            dur = _normalize_duration(m.group(1))
            if dur:
                return dur

    for scope_sel in (".video-info", ".video-details", ".info-holder", "h1"):
        scope = soup.select_one(scope_sel)
        if scope:
            dur = _normalize_duration(scope.get_text(" ", strip=True))
            if dur:
                return dur

    return None


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_video_href(url) or url
    ld = _parse_json_ld_video(soup)

    h1 = soup.select_one("h1, .video-title, .title-holder h1")
    title = _clean_title(
        _first_non_empty(
            h1.get_text(" ", strip=True) if h1 else None,
            ld.get("title"),
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        ld.get("description"),
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
    )
    thumbnail = _first_non_empty(
        ld.get("thumbnail"),
        _meta(soup, prop="og:image"),
        _meta(soup, name="twitter:image"),
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    views_el = soup.select_one(
        ".video-item-views, .info.video-item-views, .views, .video-info .views"
    )
    views_text = views_el.get_text(" ", strip=True) if views_el else None
    duration = _first_non_empty(
        _extract_watch_duration(soup, html),
        ld.get("duration"),
    )
    views = (
        _extract_views(views_text)
        or ld.get("views")
        or _extract_views(soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None)
    )

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


def _is_blocked_list_html(html: str) -> bool:
    low = (html or "").lower()
    if len(html) < 2500:
        return True
    if "age-restricted" in low and "/video/" not in low:
        return True
    if "confirm you are" in low and "video-item" not in low and "/video/" not in low:
        return True
    return False


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    referer = base_url if (base_url or "").startswith("http") else BASE_SITE
    page_url = _build_list_page_url(base_url, page)

    try_urls = [page_url]
    alt_url = _build_list_page_url("https://www.porntrex.com/latest-updates/", page)
    if alt_url not in try_urls:
        try_urls.append(alt_url)

    html = ""
    for url in try_urls:
        try:
            candidate = await fetch_page(url, referer=referer)
            if candidate and not _is_blocked_list_html(candidate):
                html = candidate
                break
        except Exception:
            continue

    if not html or _is_blocked_list_html(html):
        return []

    soup = BeautifulSoup(html, "lxml")
    items = _parse_list_items(soup, limit=limit, html=html)
    if items:
        return items

    for url in try_urls[1:]:
        try:
            candidate = await fetch_page(url, referer=referer)
            if not candidate or _is_blocked_list_html(candidate):
                continue
            soup = BeautifulSoup(candidate, "lxml")
            items = _parse_list_items(soup, limit=limit, html=candidate)
            if items:
                return items
        except Exception:
            continue

    return []
