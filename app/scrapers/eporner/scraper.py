from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

BASE_SITE = "https://www.eporner.com/"
SITE_HOST = "eporner.com"
SITE_ALIASES = frozenset({"eporner.com", "www.eporner.com"})

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "epcolor=auto; age_pass=1",
    "Referer": BASE_SITE,
}

_VIDEO_ID_RE = re.compile(
    r"eporner\.com/(?:video-|hd-porn/|embed/)(?P<id>[\w-]+)",
    re.IGNORECASE,
)
_HASH_RE = re.compile(r'hash\s*[:=]\s*["\']([\da-f]{32})', re.IGNORECASE)
_VIDEO_HREF_RE = re.compile(
    r'href=["\'](?:https?://(?:www\.)?eporner\.com)?/(?:video-|hd-porn/)([\w-]+)(?:/[^"\']*)?["\']',
    re.IGNORECASE,
)
_M3U8_RE = re.compile(r"https?://[^\s\"'<>]+\.m3u8(?:\?[^\s\"'<>]*)?", re.IGNORECASE)
_MP4_RE = re.compile(r"https?://[^\s\"'<>]+\.mp4(?:\?[^\s\"'<>]*)?", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".eporner.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _encode_base_n(num: int, base: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if num == 0:
        return "0"
    out: list[str] = []
    n = num
    while n:
        n, rem = divmod(n, base)
        out.append(alphabet[rem])
    return "".join(reversed(out))


def _calc_xhr_hash(vid_hash: str) -> str:
    return "".join(_encode_base_n(int(vid_hash[i : i + 8], 16), 36) for i in range(0, 32, 8))


def _extract_video_id(url: str) -> Optional[str]:
    m = _VIDEO_ID_RE.search(url or "")
    return m.group("id") if m else None


def _canonical_video_url(video_id: str, slug: str | None = None) -> str:
    if slug:
        return f"https://www.eporner.com/video-{video_id}/{slug.strip('/')}/"
    return f"https://www.eporner.com/video-{video_id}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://www.eporner.com{href}"
    if "eporner.com" not in href.lower():
        return None
    vid = _extract_video_id(href)
    if not vid:
        return None
    parsed = urlparse(href.split("?", 1)[0])
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    slug = None
    if parts:
        last = parts[-1]
        if last != vid and not last.startswith("video-"):
            slug = last
    return _canonical_video_url(vid, slug)


async def _fetch_with_curl_cffi(url: str, *, json_mode: bool = False) -> Optional[str | dict]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    if json_mode:
        headers["Accept"] = "application/json, text/plain, */*"

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                if json_mode:
                    return resp.json()
                return resp.text
        except Exception:
            continue
    return None


async def fetch_html(url: str) -> str:
    text = await _fetch_with_curl_cffi(url)
    if text and isinstance(text, str):
        return text

    from app.core.pool import fetch_html as pool_fetch_html

    return await pool_fetch_html(url, headers=_DEFAULT_HEADERS)


async def fetch_json(url: str, *, params: dict | None = None) -> dict:
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urlencode(params)}"

    data = await _fetch_with_curl_cffi(url, json_mode=True)
    if isinstance(data, dict):
        return data

    headers = dict(_DEFAULT_HEADERS)
    headers["Accept"] = "application/json, text/plain, */*"
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(45.0, connect=45.0),
        headers=headers,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def _quality_rank(quality: str) -> int:
    q = (quality or "").lower().replace("p", "")
    if q == "adaptive" or q == "m3u8":
        return 10_000
    if q.isdigit():
        return int(q)
    m = re.search(r"(\d{3,4})", quality)
    return int(m.group(1)) if m else 0


def _streams_from_xhr_payload(video: dict) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    hls_url: Optional[str] = None

    if video.get("available") is False:
        return {"streams": [], "hls": None, "default": None, "has_video": False}

    sources = video.get("sources") or {}
    if not isinstance(sources, dict):
        return {"streams": [], "hls": None, "default": None, "has_video": False}

    for kind, formats_dict in sources.items():
        if not isinstance(formats_dict, dict):
            continue
        for format_id, format_dict in formats_dict.items():
            if not isinstance(format_dict, dict):
                continue
            src = (format_dict.get("src") or "").strip().replace("\\/", "/")
            if not src.startswith("http") or src in seen:
                continue
            seen.add(src)
            if kind == "hls" or ".m3u8" in src:
                height = re.search(r"(\d{3,4})", format_id or "")
                quality = f"{height.group(1)}p" if height else "adaptive"
                streams.append({"url": src, "quality": quality, "format": "hls"})
                if not hls_url:
                    hls_url = src
            else:
                height = re.search(r"(\d{3,4})", format_id or "")
                quality = f"{height.group(1)}p" if height else (format_id or "unknown")
                streams.append({"url": src, "quality": quality, "format": "mp4"})

    streams.sort(key=lambda s: _quality_rank(s.get("quality", "")), reverse=True)
    default = hls_url or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default,
        "has_video": bool(streams),
    }


async def _streams_from_xhr(video_id: str, webpage: str) -> dict[str, Any]:
    m = _HASH_RE.search(webpage)
    if not m:
        return {"streams": [], "hls": None, "default": None, "has_video": False}

    xhr_url = f"https://www.eporner.com/xhr/video/{video_id}"
    params = {
        "hash": _calc_xhr_hash(m.group(1)),
        "device": "generic",
        "domain": "www.eporner.com",
        "fallback": "false",
    }
    try:
        data = await fetch_json(xhr_url, params=params)
        return _streams_from_xhr_payload(data)
    except Exception:
        return {"streams": [], "hls": None, "default": None, "has_video": False}


def _streams_from_api_v2_video(video: dict) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    all_q = video.get("all_qualities") or {}
    if isinstance(all_q, dict):
        for q, src in all_q.items():
            src = (src or "").strip()
            if not src.startswith("http") or src in seen:
                continue
            seen.add(src)
            q_label = f"{q}p" if str(q).isdigit() else str(q)
            streams.append({"url": src, "quality": q_label, "format": "mp4"})

    default_q = video.get("default_quality") or {}
    if isinstance(default_q, dict):
        src = (default_q.get("url") or "").strip()
        if src.startswith("http") and src not in seen:
            seen.add(src)
            quality = default_q.get("quality") or "default"
            streams.append({"url": src, "quality": str(quality), "format": "mp4"})

    streams.sort(key=lambda s: _quality_rank(s.get("quality", "")), reverse=True)
    default = streams[0]["url"] if streams else None
    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


async def _streams_from_api_v2(video_id: str) -> dict[str, Any]:
    api_url = (
        f"https://www.eporner.com/api/v2/video/search/"
        f"?id={video_id}&per_page=1&thumbsize=big"
    )
    try:
        data = await fetch_json(api_url)
        videos = data.get("videos") or []
        if videos:
            return _streams_from_api_v2_video(videos[0])
    except Exception:
        pass
    return {"streams": [], "hls": None, "default": None, "has_video": False}


def _streams_from_html(html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    hls_url: Optional[str] = None
    html_norm = html.replace("\\/", "/")

    for pat, fmt in ((_M3U8_RE, "hls"), (_MP4_RE, "mp4")):
        for raw in pat.findall(html_norm):
            url = raw.strip()
            if not url.startswith("http") or url in seen:
                continue
            if "/thumb" in url.lower() or "preview" in url.lower():
                continue
            seen.add(url)
            m = re.search(r"/(\d{3,4})p/", url, re.I) or re.search(r"_(\d{3,4})p\.mp4", url, re.I)
            quality = f"{m.group(1)}p" if m else ("adaptive" if fmt == "hls" else "unknown")
            streams.append({"url": url, "quality": quality, "format": fmt})
            if fmt == "hls" and not hls_url:
                hls_url = url

    soup = BeautifulSoup(html, "lxml")
    for source in soup.select("video source[src]"):
        src = (source.get("src") or "").strip()
        if not src.startswith("http") or src in seen:
            continue
        seen.add(src)
        quality = source.get("quality") or source.get("label") or "unknown"
        fmt = "hls" if ".m3u8" in src else "mp4"
        streams.append({"url": src, "quality": str(quality), "format": fmt})
        if fmt == "hls" and not hls_url:
            hls_url = src

    streams.sort(key=lambda s: _quality_rank(s.get("quality", "")), reverse=True)
    default = hls_url or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default,
        "has_video": bool(streams),
    }


async def _resolve_streams(video_id: str, html: str) -> dict[str, Any]:
    for result in (
        await _streams_from_xhr(video_id, html),
        await _streams_from_api_v2(video_id),
        _streams_from_html(html),
    ):
        if result.get("has_video"):
            return result
    return {"streams": [], "hls": None, "default": None, "has_video": False}


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
    for suffix in (" - EPORNER.COM", " | EPORNER.COM", " - EPORNER", " | EPORNER"):
        if suffix.lower() in t.lower():
            t = re.split(re.escape(suffix), t, flags=re.I)[0].strip()
    return t or None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for attr in ("data-src", "data-original", "data-thumb", "src"):
        val = img.get(attr)
        if val and not str(val).startswith("data:"):
            url = str(val).strip()
            if url.startswith("//"):
                return f"https:{url}"
            return url
    return None


def _parse_duration_seconds(sec: int | None) -> Optional[str]:
    if not sec or sec <= 0:
        return None
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("div.mb, div.eporner-video-ins, article.mb"):
        if len(items) >= limit:
            break
        link = block.select_one('a[href*="/video-"], a[href*="/hd-porn/"]')
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title_el = block.select_one(".mbtit a, .mbtitle a, a[title]") or link
        img = block.select_one("img")
        dur_el = block.select_one(".mbtim, .duration, .mbtime")
        views_el = block.select_one(".mbvie, .views, .mbviews")

        items.append(
            {
                "url": url,
                "title": _clean_title(
                    _first_non_empty(
                        title_el.get_text(" ", strip=True) if title_el else None,
                        link.get("title"),
                        img.get("alt") if img else None,
                    )
                )
                or "Unknown Video",
                "thumbnail_url": _best_image_url(img),
                "duration": dur_el.get_text(strip=True) if dur_el else None,
                "views": re.sub(r"[^\d]", "", views_el.get_text()) if views_el else None,
                "uploader_name": None,
                "tags": None,
            }
        )

    if len(items) < limit:
        for vid, _ in _VIDEO_HREF_RE.findall(str(soup)):
            if len(items) >= limit:
                break
            url = _canonical_video_url(vid)
            if url in seen:
                continue
            seen.add(url)
            items.append(
                {
                    "url": url,
                    "title": "Unknown Video",
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "tags": None,
                }
            )

    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = f"{BASE_SITE.rstrip('/')}/{raw.lstrip('/')}"
    parsed = urlparse(raw)
    page_num = max(1, int(page) if page else 1)

    path = (parsed.path or "/").rstrip("/") or ""
    if re.search(r"/\d+$", path):
        path = re.sub(r"/\d+$", "", path)

    if page_num <= 1:
        new_path = path or "/"
    else:
        new_path = f"{path}/{page_num}" if path else f"/{page_num}"

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or f"www.{SITE_HOST}",
            new_path + "/",
            "",
            parsed.query,
            "",
        )
    )


def parse_video_page(
    html: str,
    url: str,
    *,
    video: dict[str, Any] | None = None,
    api_video: dict | None = None,
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")
    video_id = _extract_video_id(url)
    canon = _normalize_video_href(url) or (f"https://www.eporner.com/video-{video_id}/" if video_id else url)

    title = _clean_title(
        _first_non_empty(
            api_video.get("title") if api_video else None,
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        api_video.get("default_thumb", {}).get("src") if api_video and isinstance(api_video.get("default_thumb"), dict) else None,
        _meta(soup, prop="og:image"),
        _best_image_url(soup.select_one("meta[property='og:image'], img")),
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    duration = None
    if api_video:
        duration = api_video.get("length_min") or _parse_duration_seconds(api_video.get("length_sec"))
    if not duration:
        dur_meta = _meta(soup, prop="video:duration") or _meta(soup, name="duration")
        if dur_meta and dur_meta.isdigit():
            duration = _parse_duration_seconds(int(dur_meta))

    views = None
    if api_video and api_video.get("views") is not None:
        views = str(api_video.get("views"))
    if not views:
        views_el = soup.select_one("#cinemaviews, #cinemaviews1, .views")
        if views_el:
            views = re.sub(r"[^\d]", "", views_el.get_text())

    tags: list[str] = []
    if api_video and api_video.get("keywords"):
        tags = [t.strip() for t in str(api_video["keywords"]).split(",") if t.strip()]
    for a in soup.select('a[href*="/cat/"], a[href*="/tag/"], a[rel="tag"]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags and len(tag) < 60:
            tags.append(tag)

    related = _parse_list_items(soup, limit=24)
    related = [r for r in related if r.get("url") != canon]

    video_data = video or {
        "streams": [],
        "hls": None,
        "default": canon,
        "has_video": False,
    }

    return {
        "url": canon,
        "title": title,
        "description": _meta(soup, prop="og:description") or _meta(soup, name="description"),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views or None,
        "uploader_name": None,
        "category": None,
        "tags": tags or None,
        "upload_date": api_video.get("added") if api_video else None,
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
        raise ValueError(f"Unsupported Eporner URL: {url}")

    html = ""
    api_video: dict | None = None
    try:
        html = await fetch_html(url)
    except Exception:
        pass

    try:
        api_data = await fetch_json(
            f"https://www.eporner.com/api/v2/video/search/?id={video_id}&per_page=1&thumbsize=big"
        )
        videos = api_data.get("videos") or []
        if videos:
            api_video = videos[0]
    except Exception:
        pass

    video_data = await _resolve_streams(video_id, html)
    if not video_data.get("has_video") and api_video:
        video_data = _streams_from_api_v2_video(api_video)

    return parse_video_page(html, url, video=video_data, api_video=api_video)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_html(page_url)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
