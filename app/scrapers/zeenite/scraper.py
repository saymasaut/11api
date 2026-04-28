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

BASE_SITE = "https://zeenite.com/"
SITE_HOST = "zeenite.com"


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == SITE_HOST or h.endswith(f".{SITE_HOST}")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, referer: str = BASE_SITE) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
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
        " watch online",
        " - Zeenite",
        " | Zeenite",
        " - zeenite.com",
        " | zeenite.com",
    ):
        if t.lower().endswith(suffix.lower()):
            t = t[: -len(suffix)].strip()
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
    m = re.search(r"\bviews?\s*[:\-]?\s*(\d[\d\s,\.]*\s*[KMBkmb]?)\b", text, flags=re.IGNORECASE)
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
        if not url:
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return urljoin(BASE_SITE, url)
        return url
    return None


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

    parsed = urlparse(href)
    host = parsed.netloc.lower()
    if SITE_HOST not in host:
        return None
    if parsed.query:
        return None
    if not re.match(r"^/videos/\d+/[^/]+/?$", parsed.path or "", flags=re.IGNORECASE):
        return None
    return urlunparse(("https", SITE_HOST, parsed.path.rstrip("/") + "/", "", "", ""))


def _detect_media_format(url: str) -> Optional[str]:
    low = (url or "").lower()
    path = urlparse(url).path.lower() if url else ""
    if "/get_file/" in low:
        return "mp4"
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mp4"):
        return "mp4"
    if "/embed/" in low:
        return "embed"
    return None


def _is_non_video_asset_url(url: str) -> bool:
    low = (url or "").lower()
    path = urlparse(url).path.lower() if url else ""
    image_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg")
    if path.endswith(image_exts):
        return True
    blocked_markers = (
        "/screenshots/",
        "/thumb/",
        "/thumbs/",
        "/thumbnails/",
        "/poster/",
        "/preview.jpg",
    )
    return any(marker in low for marker in blocked_markers)


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for m in re.finditer(r"https?://[^\s\"'<>]+", unescaped, flags=re.IGNORECASE):
        u = m.group(0).strip()
        if u and _detect_media_format(u):
            urls.append(u)
    return list(dict.fromkeys(urls))


def _stream_quality_from_url(url: str) -> str:
    low = (url or "").lower()
    if "zeenite.com/embed/" in low:
        return "zeenite"
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if _detect_media_format(url) == "hls":
        return "adaptive"
    return "source"


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    blocked = (
        "app.yrotary.com",
        "/api/spots/",
        "adspyglass",
        "traforama",
        "doubleclick",
        "googlesyndication",
        "adservice",
        "exoclick",
        "trafficjunky",
        "popads",
        "adnxs",
        "cloudfront.net/pop",
    )
    return any(marker in s for marker in blocked)


def _embed_quality_label(src: str, server_idx: int) -> str:
    low = (src or "").lower()
    if "zeenite.com/embed/" in low:
        return "zeenite"
    return f"Server {server_idx}"


def _extract_streams(soup: BeautifulSoup, html: str, page_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    # Some pages expose direct media via anchors (including /get_file/ routes).
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("//"):
            href = f"https:{href}"
        elif href.startswith("/"):
            href = urljoin(page_url, href)
        if not href.startswith("http"):
            continue
        if _is_probable_ad_iframe(href):
            continue
        if _is_non_video_asset_url(href):
            continue
        fmt = _detect_media_format(href)
        if not fmt or href in seen:
            continue
        seen.add(href)
        streams.append({"url": href, "quality": _stream_quality_from_url(href), "format": fmt})

    for video in soup.select("video"):
        src = (video.get("src") or "").strip()
        if src:
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(page_url, src)
            if _is_probable_ad_iframe(src):
                continue
            if _is_non_video_asset_url(src):
                continue
            fmt = _detect_media_format(src)
            if src.startswith("http") and src not in seen and fmt in ("mp4", "hls"):
                seen.add(src)
                streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})
        for source in video.select("source[src]"):
            src = (source.get("src") or "").strip()
            if not src:
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(page_url, src)
            if _is_probable_ad_iframe(src):
                continue
            if _is_non_video_asset_url(src):
                continue
            fmt = _detect_media_format(src)
            if not src.startswith("http") or src in seen or fmt not in ("mp4", "hls"):
                continue
            seen.add(src)
            streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})

    for src in _extract_inline_urls(html):
        if src in seen:
            continue
        if _is_probable_ad_iframe(src):
            continue
        if _is_non_video_asset_url(src):
            continue
        fmt = _detect_media_format(src)
        if fmt not in ("mp4", "hls", "embed"):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})

    server_idx = 1
    for iframe in soup.select("iframe[src]"):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = urljoin(page_url, src)
        if not src.startswith("http") or src in seen or _is_probable_ad_iframe(src):
            continue
        if _is_non_video_asset_url(src):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": _embed_quality_label(src, server_idx), "format": "embed"})
        server_idx += 1

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        qtxt = item.get("quality") or ""
        q = re.search(r"(\d{3,4})", qtxt)
        qnum = int(q.group(1)) if q else 0
        if fmt == "mp4":
            return (3, qnum)
        if fmt == "hls":
            return (2, qnum)
        return (1, 0)

    uniq = list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in streams)))
    materialized = [json.loads(s) for s in uniq]
    materialized.sort(key=_score, reverse=True)

    default_url = None
    for preferred in ("mp4", "hls", "embed"):
        m = next((s for s in materialized if s.get("format") == preferred), None)
        if m:
            default_url = m.get("url")
            break

    hls_url = next((s.get("url") for s in materialized if s.get("format") == "hls"), None)
    return {
        "streams": materialized,
        "hls": hls_url,
        "default": default_url,
        "has_video": bool(materialized),
    }


async def _get_file_to_remote_playable(get_file_url: str, *, referer: str) -> Optional[str]:
    """
    Resolve /get_file/ URLs to the final CDN-playable URL.
    """
    base = get_file_url.split("?", 1)[0].strip().rstrip("/")
    ref = referer.strip() if referer.strip().startswith("http") else BASE_SITE
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Referer": ref,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
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


def _extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"/videos/(\d+)/", url or "", flags=re.IGNORECASE)
    return m.group(1) if m else None


def _url_contains_video_id(url: str, video_id: str) -> bool:
    low = (url or "").lower()
    vid = str(video_id).lower()
    return (
        f"/{vid}/" in low
        or f"/{vid}." in low
        or f"%2f{vid}%2f" in low
        or f"%2f{vid}.mp4" in low
        or f"{vid}.mp4" in low
    )


async def _resolve_video_streams_to_remote_playable(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_mp4 = [s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "")]
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
            streams.remove(stream)

    direct_mp4 = [s for s in streams if s.get("format") == "mp4" and "get_file" not in (s.get("url") or "")]
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)

    if direct_mp4:
        video["default"] = direct_mp4[0]["url"]
    elif hls:
        video["default"] = hls["url"]
    elif embed:
        video["default"] = embed["url"]
    else:
        video["default"] = None

    video["hls"] = hls["url"] if hls else None
    video["has_video"] = bool(direct_mp4) or bool(hls) or bool(embed)


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

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
        _meta(soup, name="twitter:description"),
        _meta(soup, name="description"),
    )
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    text_blob = soup.get_text(" ", strip=True)
    views_el = soup.select_one(".views")
    views_text = views_el.get_text(" ", strip=True) if views_el else None
    duration = _extract_duration(text_blob)
    views = _extract_views(views_text) or _extract_views(text_blob)

    tags: list[str] = []
    for el in soup.select(".tags a, a[href*='/tags/']"):
        tag = el.get_text(" ", strip=True)
        if tag:
            tags.append(tag)
    if not tags:
        mt = re.search(r"Tags?\s*:\s*(.+?)(?:Link to this video|Embed code|Comments?\s*\(|$)", text_blob, flags=re.IGNORECASE)
        if mt:
            tags = [t.strip() for t in mt.group(1).split() if t.strip()]
    tags = list(dict.fromkeys(tags))

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": None,
        "category": _meta(soup, prop="article:section"),
        "tags": tags,
        "upload_date": _first_non_empty(
            _meta(soup, prop="article:published_time"),
            _meta(soup, prop="article:modified_time"),
        ),
        "video": _extract_streams(soup, html, url),
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url, referer=url)
    data = parse_video_page(html, url)
    await _resolve_video_streams_to_remote_playable(data.get("video", {}), referer=url)
    return data


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or SITE_HOST
    path = p.path or "/"
    query_items = dict(parse_qsl(p.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    clean_path = re.sub(r"/page/\d+/?$", "/", path)
    query_items["page"] = str(page)
    return urlunparse((scheme, netloc, clean_path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.select("a[href]"):
        if len(items) >= limit:
            break
        href = _normalize_video_href(a.get("href") or "")
        if not href or href in seen:
            continue

        container = a.find_parent(["article", "li", "div"]) or a
        img = a.find("img") or (container.find("img") if container else None)
        thumb = _best_image_url(img)
        if not thumb:
            continue

        title = a.get("title") or (img.get("alt") if img else None) or a.get_text(" ", strip=True)
        title = _clean_title(title) or "Unknown Video"

        ctext = container.get_text(" ", strip=True) if container else ""
        duration = _extract_duration(ctext)
        views_el = container.select_one(".views") if container else None
        views_text = views_el.get_text(" ", strip=True) if views_el else None
        views = _extract_views(views_text) or _extract_views(ctext)

        seen.add(href)
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
