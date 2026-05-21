from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://hohoj.tv/"
SITE_HOST = "hohoj.tv"

_VIDEO_ID_RE = re.compile(r"^https://(?:www\.)?hohoj\.tv/video\?id=(?P<vid>\d+)", re.IGNORECASE)
_EMBED_ID_RE = re.compile(r"^https://(?:www\.)?hohoj\.tv/embed\?id=(?P<vid>\d+)", re.IGNORECASE)
_VIDEO_HREF_RE = re.compile(r"/video\?id=(?P<vid>\d+)", re.IGNORECASE)
_M3U8_RE = re.compile(r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*", re.IGNORECASE)


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
        "Accept-Language": "en-US,en;q=0.9,zh-TW,zh;q=0.8",
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
    for suffix in (
        " HoHoJ 打J好幫手 | 免費線上AV | 高清日本AV | 免費A片",
        " HoHoJ.TV Player",
        " | HoHoJ",
        " - HoHoJ",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    return t or None


def _normalize_numberish(value: str | None) -> Optional[str]:
    if not value:
        return None
    txt = str(value).strip().replace(",", "").replace("\u00a0", " ")
    txt = re.sub(r"\s+", "", txt)
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


def _extract_video_id(url: str) -> Optional[str]:
    raw = (url or "").strip()
    for pattern in (_VIDEO_ID_RE, _EMBED_ID_RE):
        m = pattern.match(raw)
        if m:
            return m.group("vid")
    parsed = urlparse(raw)
    if (parsed.netloc or "").lower().endswith(SITE_HOST) and (parsed.path or "").lower() in ("/video", "/embed"):
        q = dict(parse_qsl(parsed.query))
        vid = q.get("id")
        if vid and str(vid).isdigit():
            return str(vid)
    return None


def _canonical_video_url(video_id: str) -> str:
    return f"https://{SITE_HOST}/video?id={video_id}"


def _embed_url(video_id: str) -> str:
    return f"https://{SITE_HOST}/embed?id={video_id}"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    m = _VIDEO_HREF_RE.search(href)
    if not m:
        return None
    return _canonical_video_url(m.group("vid"))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy-src", "src"):
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return urljoin(BASE_SITE, url)
        return url
    return None


def _rating_stats(rating_el: Any) -> tuple[Optional[str], Optional[str]]:
    if rating_el is None:
        return None, None
    spans = rating_el.find_all("span", recursive=False)
    if not spans:
        spans = rating_el.select("span")
    views = _normalize_numberish(spans[0].get_text(strip=True)) if len(spans) > 0 else None
    likes = _normalize_numberish(spans[1].get_text(strip=True)) if len(spans) > 1 else None
    return views, likes


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("div.video-item"):
        if len(items) >= limit:
            break
        block_html = str(block)
        m = _VIDEO_HREF_RE.search(block_html)
        if not m:
            continue
        url = _canonical_video_url(m.group("vid"))
        if url in seen:
            continue
        seen.add(url)

        title_el = block.select_one(".video-item-title")
        img = block.select_one("img")
        title = _clean_title(
            _first_non_empty(
                title_el.get_text(" ", strip=True) if title_el else None,
                img.get("alt") if img else None,
            )
        ) or "Unknown Video"

        views, likes = _rating_stats(block.select_one(".video-item-rating"))
        badge_el = block.select_one(".video-item-badge")
        tags: list[str] = []
        if badge_el and badge_el.get_text(strip=True):
            tags.append(badge_el.get_text(strip=True))
        if likes:
            tags.append(f"Likes: {likes}")

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": _best_image_url(img),
                "views": views,
                "uploader_name": None,
                "tags": tags or None,
            }
        )

    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    q["p"] = str(max(1, page))
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            parsed.path or "/",
            "",
            urlencode(q),
            "",
        )
    )


def _streams_from_embed_html(html: str, video_id: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    embed = _embed_url(video_id)
    streams.append({"url": embed, "quality": "hohoj", "format": "embed"})

    soup = BeautifulSoup(html, "lxml")
    video_el = soup.select_one("video#my-video") or soup.select_one("video[src]")
    hls_url = video_el.get("src") if video_el and video_el.get("src") else None
    if not hls_url:
        m = re.search(r'var\s+videoSrc\s*=\s*"([^"]+)"', html)
        if m:
            hls_url = m.group(1).strip()
    if not hls_url:
        found = _M3U8_RE.findall(html)
        hls_url = found[0] if found else None

    if hls_url and hls_url.startswith("http"):
        streams.insert(0, {"url": hls_url, "quality": "adaptive", "format": "hls"})
        return {
            "streams": streams,
            "hls": hls_url,
            "default": hls_url,
            "has_video": True,
        }

    return {"streams": streams, "hls": None, "default": embed, "has_video": True}


def parse_video_page(html: str, url: str, *, embed_html: str | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_id = _extract_video_id(url) or ""
    page_url = _canonical_video_url(video_id) if video_id else url

    title = _clean_title(
        _first_non_empty(
            soup.select_one("h5.mt-3").get_text(" ", strip=True) if soup.select_one("h5.mt-3") else None,
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description"))

    hidden_poster = soup.select_one("img[hidden][src]")
    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        hidden_poster.get("src") if hidden_poster else None,
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    info = soup.select_one(".info")
    views = None
    upload_date = None
    if info:
        eye_parent = info.select_one(".fa-eye")
        if eye_parent and eye_parent.parent:
            views = _normalize_numberish(eye_parent.parent.get_text(" ", strip=True))
        date_span = info.select("span")[-1] if info.select("span") else None
        if date_span:
            upload_date = date_span.get_text(strip=True) or None

    tags: list[str] = []
    for a in soup.select(".ctg a[href]"):
        tag = a.get_text(strip=True)
        if tag:
            tags.append(tag)

    uploader = None
    model = soup.select_one(".model .model-name")
    if model:
        uploader = model.get_text(strip=True) or None

    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != page_url]

    video = _streams_from_embed_html(embed_html or "", video_id) if embed_html else {
        "streams": [{"url": _embed_url(video_id), "quality": "hohoj", "format": "embed"}],
        "hls": None,
        "default": _embed_url(video_id),
        "has_video": bool(video_id),
    }

    return {
        "url": page_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": views,
        "uploader_name": uploader,
        "category": None,
        "tags": tags or None,
        "upload_date": upload_date,
        "video": video,
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    video_id = _extract_video_id(url)
    if not video_id:
        raise ValueError(f"Unsupported HoHoJ URL: {url}")

    page_url = _canonical_video_url(video_id)
    embed_url = _embed_url(video_id)
    html, embed_html = await asyncio.gather(
        fetch_page(page_url, referer=BASE_SITE),
        fetch_page(embed_url, referer=page_url),
    )
    return parse_video_page(html, page_url, embed_html=embed_html)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
