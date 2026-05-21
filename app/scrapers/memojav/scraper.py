from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any, Optional
from urllib.parse import unquote, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://memojav.com/"
SITE_HOST = "memojav.com"

_VIDEO_PAGE_RE = re.compile(
    r"^https://(?:www\.)?memojav\.com/video/(?P<code>[A-Za-z0-9-]+)/?$",
    re.IGNORECASE,
)

_MM_VAR_RE = re.compile(
    r'var\s+mm\s*=\s*\{[^}]*type:\s*"(?P<type>[^"]+)"[^}]*id:\s*"(?P<id>[^"]+)"[^}]*vi:\s*"(?P<vi>[^"]+)"',
    re.IGNORECASE,
)

_MP4_QUALITY_SUFFIXES = (
    ("1080p", "m37"),
    ("720p", "m22"),
    ("360p", "m18"),
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == SITE_HOST or h.endswith(f".{SITE_HOST}")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer or BASE_SITE,
    }
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
    for suffix in (" - MemoJav", " | MemoJav"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    if " | " in t:
        parts = t.split(" | ", 1)
        if re.match(r"^[A-Za-z0-9-]+$", parts[0].strip()):
            t = parts[1].strip() if len(parts) > 1 else parts[0].strip()
    return t or None


def _normalize_duration(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", raw, flags=re.IGNORECASE)
    if m:
        h = int(m.group(1) or 0)
        mm = int(m.group(2) or 0)
        s = int(m.group(3) or 0)
        if h > 0:
            return f"{h}:{mm:02d}:{s:02d}"
        return f"{mm}:{s:02d}"
    m2 = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", raw)
    return m2.group(0) if m2 else None


def _video_sig_query() -> str:
    """Replicate memojav.com/static/main.js video_sig() for /hls/get_video_info.php."""
    t = int(time.time() * 1000)
    sig = base64.b64encode(str(t).encode()).decode()
    start = len(sig) - 12
    sig10 = sig[start : start + 10]
    sts = 1
    for i, ch in enumerate(sig10):
        sts += ord(ch) * i * 1743
    return f"&sig={sig10}&sts={sts}"


def _extract_video_code(url: str) -> Optional[str]:
    m = _VIDEO_PAGE_RE.match((url or "").strip().rstrip("/") + "/")
    return m.group("code").upper() if m else None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://{SITE_HOST}{href}"
    if not href.startswith("http"):
        return None
    href = href.split("#", 1)[0].split("?", 1)[0]
    m = _VIDEO_PAGE_RE.match(href if href.endswith("/") else href + "/")
    if not m:
        return None
    code = m.group("code").upper()
    return f"https://{SITE_HOST}/video/{code}"


def _parse_mm_var(html: str) -> dict[str, str]:
    m = _MM_VAR_RE.search(html)
    if not m:
        return {}
    return {"type": m.group("type"), "id": m.group("id"), "vi": m.group("vi")}


async def _fetch_video_info(video_code: str, *, referer: str) -> dict[str, Any]:
    api_url = f"https://{SITE_HOST}/hls/get_video_info.php?id={video_code}{_video_sig_query()}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": referer,
    }
    raw = await pool_fetch_html(api_url, headers=headers)
    payload = raw.split("for (;;);", 1)[-1].strip()
    return json.loads(payload)


def _streams_from_video_info(info: dict[str, Any], video_code: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    embed_url = f"https://{SITE_HOST}/embed/{video_code}"
    streams.append({"url": embed_url, "quality": "memojav", "format": "embed"})

    if not info.get("success"):
        return {"streams": streams, "hls": None, "default": embed_url, "has_video": True}

    media_type = str(info.get("type") or "").lower()
    media_url = unquote(str(info.get("url") or ""))

    if media_type == "hls" and media_url.startswith("http"):
        streams.append({"url": media_url, "quality": "adaptive", "format": "hls"})
        return {
            "streams": streams,
            "hls": media_url,
            "default": media_url,
            "has_video": True,
        }

    if media_type == "mp4" and media_url.startswith("http"):
        mp4_streams: list[dict[str, str]] = []
        for label, suffix in _MP4_QUALITY_SUFFIXES:
            mp4_streams.append(
                {
                    "url": f"{media_url}={suffix}",
                    "quality": label,
                    "format": "mp4",
                }
            )
        streams = mp4_streams + streams
        return {
            "streams": streams,
            "hls": None,
            "default": mp4_streams[0]["url"] if mp4_streams else embed_url,
            "has_video": True,
        }

    return {"streams": streams, "hls": None, "default": embed_url, "has_video": True}


def parse_video_page(html: str, url: str, *, video_info: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_code = _extract_video_code(url) or ""
    mm = _parse_mm_var(html)

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("#title").get_text(" ", strip=True) if soup.select_one("#title") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
        soup.select_one("#title-description").get_text(" ", strip=True) if soup.select_one("#title-description") else None,
    )

    poster = soup.select_one("#poster")
    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _meta(soup, name="twitter:image"),
        mm.get("vi"),
        poster.get("src") if poster else None,
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    duration = None
    dur_meta = soup.find("meta", attrs={"itemprop": "duration"})
    if dur_meta and dur_meta.get("content"):
        duration = _normalize_duration(dur_meta.get("content"))
    if not duration:
        for node in soup.select('[itemprop="duration"]'):
            duration = _normalize_duration(node.get("content") or node.get_text(" ", strip=True))
            if duration:
                break

    tags: list[str] = []
    kw = _meta(soup, name="keywords")
    if kw:
        tags.extend([x.strip() for x in kw.split(",") if x.strip()])
    for a in soup.select('a[href^="/categories/"]'):
        tag = a.get_text(" ", strip=True)
        if tag:
            tags.append(tag)
    tags = list(dict.fromkeys(tags))

    uploader = None
    actress = soup.select_one('a[href^="/actress/"] .description-vertical, a[href^="/actress/"]')
    if actress:
        uploader = actress.get_text(" ", strip=True) or None

    preview_el = soup.select_one("#preview-vid[src]")
    preview_url = preview_el.get("src") if preview_el else None
    if preview_url and preview_url.startswith("//"):
        preview_url = f"https:{preview_url}"

    video = _streams_from_video_info(video_info or {}, video_code or mm.get("id") or video_code)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
        "uploader_name": uploader,
        "category": None,
        "tags": tags,
        "video": video,
        "related_videos": [],
        "preview_url": preview_url,
    }


async def scrape(url: str) -> dict[str, Any]:
    video_code = _extract_video_code(url)
    if not video_code:
        raise ValueError(f"Unsupported MemoJav URL: {url}")

    # Site returns 404 for trailing-slash video URLs (e.g. /video/START-579/).
    page_url = f"https://{SITE_HOST}/video/{video_code}"
    referer = f"https://{SITE_HOST}/embed/{video_code}"
    html = await fetch_page(page_url, referer=BASE_SITE)
    info = await _fetch_video_info(video_code, referer=referer)
    return parse_video_page(html, page_url, video_info=info)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or SITE_HOST
    path = p.path or "/"
    path = re.sub(r"/page-\d+/?$", "", path)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if page <= 1:
        if path and path != "/":
            path = path + "/"
        else:
            path = "/"
        return urlunparse((scheme, netloc, path, "", "", ""))
    # MemoJav returns 404 for trailing-slash page URLs (e.g. /video/page-2/).
    base = "" if path in ("", "/") else path
    new_path = f"{base}/page-{page}" if base else f"/page-{page}"
    return urlunparse((scheme, netloc, new_path, "", "", ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a.video-item[href]"):
        if len(items) >= limit:
            break
        href = _normalize_video_href(a.get("href") or "")
        if not href or href in seen:
            continue

        img = a.select_one("img.video-poster")
        title_el = a.select_one(".video-title")
        title = _clean_title(title_el.get_text(" ", strip=True) if title_el else None) or "Unknown Video"
        thumb = img.get("src") if img and img.get("src") else None
        if thumb and thumb.startswith("//"):
            thumb = f"https:{thumb}"

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": None,
                "views": None,
                "uploader_name": None,
            }
        )

    return items[:limit]
