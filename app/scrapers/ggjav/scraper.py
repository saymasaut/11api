from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://ggjav.com/"
SITE_HOST = "ggjav.com"
SITE_ALIASES = frozenset({"ggjav.com", "ggjav.tv", "www.ggjav.com", "www.ggjav.tv"})

_VIDEO_PAGE_RE = re.compile(
    r"^https://(?:www\.)?(?:ggjav\.com|ggjav\.tv)/main/video\?id=(?P<vid>\d+)",
    re.IGNORECASE,
)
_VIDEO_HREF_RE = re.compile(r"/main/video\?id=(?P<vid>\d+)", re.IGNORECASE)
_LINKS_BLOB_RE = re.compile(r'var\s+l\s*=\s*"([A-Za-z0-9+/=]+)"')
_M3U8_RE = re.compile(r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*", re.IGNORECASE)
_VIDEO_SRC_RE = re.compile(r'var\s+videoSrc\s*=\s*"([^"]+)"', re.IGNORECASE)

_PREFERRED_STREAM_KEYS = (
    "ggjav",
    "mmfl04",
    "mmsw02",
    "embedrise",
    "tapewithadblock",
    "streamtape",
    "dood",
    "mixdrop",
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".ggjav.com") or h.endswith(".ggjav.tv")


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
        " - GGJAV | 最齊全的免費線上AV，線上A片，高清日本AV，線上成人影片，JAV",
        " | GGJAV",
        " - GGJAV",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    return t or None


def _normalize_numberish(value: str | None) -> Optional[str]:
    if not value:
        return None
    txt = str(value).strip().replace(",", "").replace("\u00a0", " ")
    txt = re.sub(r"\s+", "", txt)
    if "%" in txt:
        return txt
    txt = re.sub(r"[^0-9KMBkmb\.%]", "", txt)
    return txt.upper() or None


def _extract_video_id(url: str) -> Optional[str]:
    raw = (url or "").strip()
    m = _VIDEO_PAGE_RE.match(raw)
    if m:
        return m.group("vid")
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if not any(host == alias or host.endswith(f".{alias}") for alias in ("ggjav.com", "ggjav.tv")):
        return None
    if (parsed.path or "").lower() != "/main/video":
        return None
    q = dict(parse_qsl(parsed.query))
    vid = q.get("id")
    return str(vid) if vid and str(vid).isdigit() else None


def _canonical_video_url(video_id: str) -> str:
    return f"https://{SITE_HOST}/main/video?id={video_id}"


def _embed_url(video_id: str) -> str:
    return f"https://{SITE_HOST}/main/embed?id={video_id}"


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


def _decode_links_blob(blob: str) -> dict[str, list[str]]:
    abl = base64.b64decode(blob)
    text = "".join(chr(b - 0x58) for b in abl)
    data = json.loads(text)
    out: dict[str, list[str]] = {}
    for key, val in data.items():
        if isinstance(val, list):
            out[str(key)] = [str(u).strip() for u in val if u]
        elif isinstance(val, str) and val.strip():
            out[str(key)] = [val.strip()]
    return out


def _hls_from_ggjav_embed_url(embed_url: str) -> Optional[str]:
    parsed = urlparse(embed_url)
    q = dict(parse_qsl(parsed.query))
    raw_u = q.get("u")
    if not raw_u:
        return None
    try:
        base = base64.b64decode(raw_u).decode("utf-8", errors="ignore").strip()
    except Exception:
        return None
    if not base:
        return None
    if ".m3u8" in base:
        return base
    if base.endswith(".mp4"):
        return f"{base}/index.m3u8"
    return None


def _streams_from_embed_html(html: str, *, fallback_embed: str) -> list[dict[str, str]]:
    streams: list[dict[str, str]] = []
    m = _VIDEO_SRC_RE.search(html)
    if m:
        src = m.group(1).strip()
        if src.startswith("/"):
            src = urljoin(fallback_embed, src)
        if src.startswith("http") and ".m3u8" in src:
            streams.append({"url": src, "quality": "adaptive", "format": "hls"})
    if not streams:
        for url in _M3U8_RE.findall(html):
            streams.append({"url": url, "quality": "adaptive", "format": "hls"})
            break
    return streams


async def _streams_from_player_links(
    links: dict[str, list[str]],
    *,
    video_id: str,
    referer: str,
) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    hls_url: Optional[str] = None
    default_url = _embed_url(video_id)

    for key in _PREFERRED_STREAM_KEYS:
        for raw_url in links.get(key, []):
            if not raw_url.startswith("http"):
                continue
            if key == "ggjav" and "ggjav.com" in raw_url:
                candidate = _hls_from_ggjav_embed_url(raw_url)
                if candidate:
                    hls_url = candidate
                    streams.insert(0, {"url": candidate, "quality": "adaptive", "format": "hls"})
                try:
                    embed_html = await fetch_page(raw_url, referer=referer)
                    for s in _streams_from_embed_html(embed_html, fallback_embed=raw_url):
                        if s["url"] not in {x["url"] for x in streams}:
                            streams.append(s)
                            if not hls_url and s["format"] == "hls":
                                hls_url = s["url"]
                except Exception:
                    pass
                streams.append({"url": raw_url, "quality": "ggjav", "format": "embed"})
            else:
                streams.append({"url": raw_url, "quality": key, "format": "embed"})

    if not streams:
        streams.append({"url": default_url, "quality": "ggjav", "format": "embed"})

    return {
        "streams": streams,
        "hls": hls_url,
        "default": hls_url or streams[0]["url"],
        "has_video": True,
    }


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("div.item"):
        if len(items) >= limit:
            break
        link = block.select_one('a[href*="/main/video?id="]')
        if not link:
            m = _VIDEO_HREF_RE.search(str(block))
            if not m:
                continue
            url = _canonical_video_url(m.group("vid"))
        else:
            url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title_el = block.select_one(".item_title a") or block.select_one(".item_title")
        img = block.select_one("img.item_image") or block.select_one("img")
        title = _clean_title(
            _first_non_empty(
                title_el.get_text(" ", strip=True) if title_el else None,
                img.get("alt") if img else None,
            )
        ) or "Unknown Video"

        views = None
        likes = None
        views_el = block.select_one(".item_views")
        if views_el:
            left = views_el.select_one(".float-left")
            right = views_el.select_one(".float-right")
            if left:
                views = _normalize_numberish(left.get_text(" ", strip=True))
            if right:
                likes = _normalize_numberish(right.get_text(" ", strip=True))

        tags = [f"Likes: {likes}"] if likes else None

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": _best_image_url(img),
                "views": views,
                "uploader_name": None,
                "tags": tags,
            }
        )

    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    q = dict(parse_qsl(parsed.query.replace("&&", "&"), keep_blank_values=True))
    q["page"] = str(max(1, page))
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


def parse_video_page(
    html: str,
    url: str,
    *,
    video: dict[str, Any] | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_id = _extract_video_id(url) or ""
    page_url = _canonical_video_url(video_id) if video_id else url

    title = _clean_title(
        _first_non_empty(
            soup.select_one(".title_text").get_text(" ", strip=True) if soup.select_one(".title_text") else None,
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description"))

    poster = soup.select_one(".info img[src]")
    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        poster.get("src") if poster else None,
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    views = None
    review = soup.select_one(".review")
    if review:
        like_el = review.select_one("#like_time")
        if like_el:
            views = _normalize_numberish(like_el.get_text(" ", strip=True))

    tags: list[str] = []
    for a in soup.select("a.ctg_button[href], .ctg a[href]"):
        tag = a.get_text(strip=True)
        if tag and tag not in tags:
            tags.append(tag)

    uploader = None
    model = soup.select_one(".model .model_name")
    if model:
        uploader = model.get_text(strip=True) or None

    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or {
        "streams": [{"url": _embed_url(video_id), "quality": "ggjav", "format": "embed"}],
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
        "upload_date": None,
        "video": video_data,
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    video_id = _extract_video_id(url)
    if not video_id:
        raise ValueError(f"Unsupported GGJAV URL: {url}")

    page_url = _canonical_video_url(video_id)
    html = await fetch_page(page_url, referer=BASE_SITE)

    video_data: dict[str, Any] | None = None
    m = _LINKS_BLOB_RE.search(html)
    if m:
        try:
            links = _decode_links_blob(m.group(1))
            video_data = await _streams_from_player_links(
                links, video_id=video_id, referer=page_url
            )
        except Exception:
            video_data = None

    if not video_data or not video_data.get("hls"):
        try:
            embed_html = await fetch_page(_embed_url(video_id), referer=page_url)
            streams = _streams_from_embed_html(embed_html, fallback_embed=page_url)
            if streams:
                hls = streams[0]["url"]
                video_data = {
                    "streams": streams + [{"url": _embed_url(video_id), "quality": "ggjav", "format": "embed"}],
                    "hls": hls,
                    "default": hls,
                    "has_video": True,
                }
        except Exception:
            pass

    return parse_video_page(html, page_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
