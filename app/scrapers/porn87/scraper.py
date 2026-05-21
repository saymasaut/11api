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

BASE_SITE = "https://porn87.com/"
SITE_HOST = "porn87.com"
SITE_ALIASES = frozenset({"porn87.com", "porn87.tv", "www.porn87.com", "www.porn87.tv"})

_VIDEO_PAGE_RE = re.compile(
    r"^https://(?:www\.)?(?:porn87\.com|porn87\.tv)/main/html\?id=(?P<vid>\d+)",
    re.IGNORECASE,
)
_VIDEO_HREF_RE = re.compile(r"/main/html\?id=(?P<vid>\d+)", re.IGNORECASE)
_LINKS_BLOB_RE = re.compile(r'var\s+l\s*=\s*"([A-Za-z0-9+/=]+)"')
_M3U8_RE = re.compile(r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*", re.IGNORECASE)
_VIDEO_SRC_RE = re.compile(r'var\s+videoSrc\s*=\s*"([^"]+)"', re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".porn87.com") or h.endswith(".porn87.tv")


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
        " Porn87 Player",
        " | Porn87",
        " - Porn87",
        "丨高清日本AV丨線上AV",
        " Porn87丨",
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
    host = (parsed.netloc or "").lower()
    if not any(host == alias or host.endswith(f".{alias}") for alias in ("porn87.com", "porn87.tv")):
        return None
    if (parsed.path or "").lower() != "/main/html":
        return None
    q = dict(parse_qsl(parsed.query))
    vid = q.get("id")
    return str(vid) if vid and str(vid).isdigit() else None


def _canonical_video_url(video_id: str) -> str:
    return f"https://{SITE_HOST}/main/html?id={video_id}"


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
    for key in ("preview_image", "data-src", "data-original", "src"):
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
            out[str(key)] = [str(u).strip() for u in val if u and str(u).strip().startswith("http")]
        elif isinstance(val, str) and val.strip().startswith("http"):
            out[str(key)] = [val.strip()]
    return out


def _streams_from_embed_html(html: str, *, fallback_embed: str) -> list[dict[str, str]]:
    streams: list[dict[str, str]] = []
    soup = BeautifulSoup(html, "lxml")
    video_el = soup.select_one("video#my-video") or soup.select_one("video[src]")
    src = video_el.get("src") if video_el else None
    if not src:
        m = _VIDEO_SRC_RE.search(html)
        src = m.group(1).strip() if m else None
    if src:
        if src.startswith("/"):
            src = urljoin(fallback_embed, src)
        if src.startswith("http"):
            fmt = "hls" if ".m3u8" in src else "mp4"
            streams.append({"url": src, "quality": "adaptive", "format": fmt})
    if not streams:
        for url in _M3U8_RE.findall(html):
            streams.append({"url": url, "quality": "adaptive", "format": "hls"})
            break
    return streams


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("div.chunk"):
        if len(items) >= limit:
            break
        link = block.select_one('a[href*="/main/html?id="]')
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title_span = link.select_one("span")
        img = link.select_one("img.video_thumbnail")
        title = _clean_title(
            _first_non_empty(
                title_span.get_text(" ", strip=True) if title_span else None,
                img.get("alt") if img and img.get("alt") else None,
            )
        ) or "Unknown Video"

        duration = None
        time_el = block.select_one(".video_time p") or block.select_one(".video_time")
        if time_el:
            duration = _extract_duration(time_el.get_text(" ", strip=True))

        views = None
        likes = None
        for span in block.find_all("span"):
            text = span.get_text(" ", strip=True)
            if span.find("i", class_=re.compile(r"fi-eye")):
                views = _normalize_numberish(text)
            elif span.find("i", class_=re.compile(r"fi-heart")):
                likes = _normalize_numberish(text)

        tags = [f"Likes: {likes}"] if likes else None

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": _best_image_url(img),
                "duration": duration,
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
    # Site pagination is 1-based (`page=2` for UI page 2); page 1 omits the param.
    page_num = max(1, int(page) if page else 1)
    if page_num <= 1:
        q.pop("page", None)
    else:
        q["page"] = str(page_num)
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


async def _streams_for_video(html: str, video_id: str, *, referer: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    hls_url: Optional[str] = None
    embed = _embed_url(video_id)

    m = _LINKS_BLOB_RE.search(html)
    if m:
        try:
            links = _decode_links_blob(m.group(1))
            for key, urls in links.items():
                for raw_url in urls:
                    streams.append({"url": raw_url, "quality": key, "format": "embed"})
        except Exception:
            pass

    try:
        embed_html = await fetch_page(embed, referer=referer)
        for s in _streams_from_embed_html(embed_html, fallback_embed=embed):
            if s["url"] not in {x["url"] for x in streams}:
                streams.insert(0, s)
                if s["format"] == "hls" and not hls_url:
                    hls_url = s["url"]
    except Exception:
        pass

    if not any(s["format"] == "embed" for s in streams):
        streams.append({"url": embed, "quality": "porn87", "format": "embed"})

    return {
        "streams": streams,
        "hls": hls_url,
        "default": hls_url or (streams[0]["url"] if streams else embed),
        "has_video": bool(streams),
    }


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_id = _extract_video_id(url) or ""
    page_url = _canonical_video_url(video_id) if video_id else url

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.select_one(".title_text").get_text(" ", strip=True) if soup.select_one(".title_text") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="description"))

    poster_img = soup.select_one("img.video_thumbnail") or soup.select_one(".info img")
    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _best_image_url(poster_img),
        poster_img.get("src") if poster_img else None,
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"
    if thumbnail and thumbnail.startswith("/"):
        thumbnail = urljoin(BASE_SITE, thumbnail)

    duration = None
    time_el = soup.select_one(".video_time p") or soup.select_one(".video_time")
    if time_el:
        duration = _extract_duration(time_el.get_text(" ", strip=True))

    views = None
    for span in soup.select("span"):
        if span.find("i", class_=re.compile(r"fi-eye")):
            views = _normalize_numberish(span.get_text(" ", strip=True))
            break

    tags: list[str] = []
    for a in soup.select('a[href*="/main/tag?name="], a[href*="/main/search?name="]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags:
            tags.append(tag)

    uploader = None
    for a in soup.select('a[href*="/main/model"]'):
        uploader = a.get_text(strip=True) or None
        if uploader:
            break

    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or {
        "streams": [{"url": _embed_url(video_id), "quality": "porn87", "format": "embed"}],
        "hls": None,
        "default": _embed_url(video_id),
        "has_video": bool(video_id),
    }

    return {
        "url": page_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
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
        raise ValueError(f"Unsupported Porn87 URL: {url}")

    page_url = _canonical_video_url(video_id)
    html = await fetch_page(page_url, referer=BASE_SITE)
    video_data = await _streams_for_video(html, video_id, referer=page_url)
    return parse_video_page(html, page_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
