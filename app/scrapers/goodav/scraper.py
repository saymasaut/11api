from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html
from app.scrapers.ggjav.scraper import _hls_from_ggjav_embed_url, _streams_from_embed_html

BASE_SITE = "http://goodav17.com/"
SITE_HOST = "goodav17.com"
SITE_ALIASES = frozenset({"goodav17.com", "www.goodav17.com"})

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?goodav17\.com/html/(?P<vid>\d+)/?",
    re.IGNORECASE,
)
_VIDEO_HREF_RE = re.compile(r"/html/(?P<vid>\d+)/?", re.IGNORECASE)
_PATH_PAGE_SUFFIX_RE = re.compile(r"^(.+)/(\d+)$")
_GGJAV_EMBED_RE = re.compile(
    r"https?://(?:www\.)?ggjav\.(?:com|tv)/main/embed\?[^\"'\s<>]+",
    re.IGNORECASE,
)
_M3U8_RE = re.compile(r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            import json

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
        " | 正妹AV",
        " - 正妹AV",
        "丨免費線上成人影片",
        "正妹AV,",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    if t.startswith("免費線上成人影片,免費線上A片,"):
        t = t.split(",", 2)[-1].strip()
    return t or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return m.group(0) if m else None


def _extract_video_id(url: str) -> Optional[str]:
    raw = (url or "").strip()
    m = _VIDEO_PAGE_RE.match(raw)
    if m:
        return m.group("vid")
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != SITE_HOST:
        return None
    m2 = _VIDEO_HREF_RE.search(parsed.path or "")
    return m2.group("vid") if m2 else None


def _canonical_video_url(video_id: str) -> str:
    return f"http://{SITE_HOST}/html/{video_id}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"http:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    m = _VIDEO_HREF_RE.search(href)
    if not m:
        return None
    return _canonical_video_url(m.group("vid"))


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("large_image", "src", "small_image", "data-src"):
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


def _ggjav_embed_from_html(html: str) -> Optional[str]:
    m = _GGJAV_EMBED_RE.search(html)
    if m:
        return m.group(0).strip()
    soup = BeautifulSoup(html, "lxml")
    frame = soup.select_one("iframe#video_frame, iframe.video_frame")
    if frame and frame.get("src"):
        src = str(frame.get("src")).strip()
        if "ggjav" in src and "embed" in src:
            if src.startswith("//"):
                return f"https:{src}"
            if src.startswith("http"):
                return src
    return None


async def _streams_for_video(html: str, *, referer: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    hls_url: Optional[str] = None
    embed_url = _ggjav_embed_from_html(html)

    if embed_url:
        candidate = _hls_from_ggjav_embed_url(embed_url)
        if candidate:
            hls_url = candidate
            streams.append({"url": candidate, "quality": "adaptive", "format": "hls"})
        try:
            embed_html = await fetch_page(embed_url, referer=referer)
            for s in _streams_from_embed_html(embed_html, fallback_embed=embed_url):
                if s["url"] not in {x["url"] for x in streams}:
                    streams.append(s)
                    if not hls_url and s["format"] == "hls":
                        hls_url = s["url"]
        except Exception:
            pass
        streams.append({"url": embed_url, "quality": "ggjav", "format": "embed"})

    if not streams:
        for url in _M3U8_RE.findall(html):
            streams.append({"url": url, "quality": "adaptive", "format": "hls"})
            hls_url = url
            break

    default = hls_url or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default,
        "has_video": bool(streams),
    }


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("div.movie"):
        if len(items) >= limit:
            break
        link = block.select_one('a[href*="/html/"]')
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        img = block.select_one("img")
        title_links = block.select("a[href*='/html/']")
        title = _clean_title(
            _first_non_empty(
                title_links[-1].get_text(" ", strip=True) if title_links else None,
                img.get("alt") if img and img.get("alt") else None,
            )
        ) or "Unknown Video"

        duration = _extract_duration(img.get("alt") if img else None)

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": _best_image_url(img),
                "duration": duration,
                "views": None,
                "uploader_name": None,
                "tags": None,
            }
        )

    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    path = (parsed.path or "/").rstrip("/") or "/"
    page_num = max(1, int(page) if page else 1)

    if path in ("", "/"):
        new_path = "/" if page_num <= 1 else f"/{page_num}/"
    else:
        m = _PATH_PAGE_SUFFIX_RE.match(path)
        if m:
            new_path = f"{m.group(1)}/{page_num}/"
        elif page_num <= 1:
            new_path = f"{path}/"
        else:
            new_path = f"{path}/{page_num}/"

    return urlunparse(
        (
            parsed.scheme or "http",
            parsed.netloc or SITE_HOST,
            new_path,
            "",
            parsed.query,
            "",
        )
    )


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_id = _extract_video_id(url) or ""
    page_url = _canonical_video_url(video_id) if video_id else url

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _best_image_url(soup.select_one("img.large_image, .movie_image img, img[large_image]")),
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    tags: list[str] = []
    for a in soup.select('a[href*="/type/"], a[href*="/actor/"]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags and len(tag) < 40:
            tags.append(tag)

    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != page_url]

    embed = _ggjav_embed_from_html(html)
    video_data = video or {
        "streams": [{"url": embed, "quality": "ggjav", "format": "embed"}] if embed else [],
        "hls": None,
        "default": embed,
        "has_video": bool(embed),
    }

    return {
        "url": page_url,
        "title": title,
        "description": _meta(soup, prop="og:description"),
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": None,
        "uploader_name": None,
        "category": None,
        "tags": tags or None,
        "upload_date": None,
        "video": video_data,
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    video_id = _extract_video_id(url)
    if not video_id:
        raise ValueError(f"Unsupported GoodAV URL: {url}")

    page_url = _canonical_video_url(video_id)
    html = await fetch_page(page_url, referer=BASE_SITE)
    video_data = await _streams_for_video(html, referer=page_url)
    return parse_video_page(html, page_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
