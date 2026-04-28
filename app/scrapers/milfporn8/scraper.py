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


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "milfporn8.net" or h.endswith(".milfporn8.net")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, referer: str = "https://milfporn8.net/") -> str:
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
    for suffix in (" | MILFPorn8.com", " - MILFPorn8.com", " | Milf Porn 8", " - Milf Porn 8"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _clean_list_title(title: str | None) -> Optional[str]:
    t = _clean_title(title)
    if not t:
        return None
    t = re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?\s+\d{1,3}%\s+\d[\d\.\s]*[kKmMbB]?\s*$", "", t).strip()
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
    m = re.search(r"\b(\d[\d\s,\.]*\s*[KMBkmb]?)\b", text)
    if not m:
        return None
    return _normalize_numberish(m.group(1))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,|\n]", value) if x.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _parse_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=False)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if isinstance(parsed, dict):
            if "@graph" in parsed and isinstance(parsed["@graph"], list):
                out.extend([x for x in parsed["@graph"] if isinstance(x, dict)])
            else:
                out.append(parsed)
        elif isinstance(parsed, list):
            out.extend([x for x in parsed if isinstance(x, dict)])
    return out


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
        return url
    return None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://milfporn8.net{href}"
    if not href.startswith("http"):
        return None
    parsed = urlparse(href)
    if "milfporn8.net" not in parsed.netloc.lower():
        return None
    if not re.match(r"^/videos/\d+/[^/]+/?$", parsed.path or "", flags=re.IGNORECASE):
        return None
    if parsed.query:
        return None
    return urlunparse(("https", "milfporn8.net", parsed.path.rstrip("/") + "/", "", "", ""))


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for m in re.finditer(r"https?://[^\s\"'<>]+", unescaped, flags=re.IGNORECASE):
        u = m.group(0).strip()
        if u and _detect_media_format(u):
            urls.append(u)
    return list(dict.fromkeys(urls))


def _detect_media_format(url: str) -> Optional[str]:
    low = (url or "").lower()
    path = urlparse(url).path.lower() if url else ""
    if "/get_file/" in low:
        return "mp4"
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mp4"):
        return "mp4"
    return None


def _is_preview_media_url(url: str) -> bool:
    path = urlparse(url).path.lower() if url else ""
    return "_preview.mp4" in path or path.endswith("/preview.mp4")


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    ad_hosts_or_markers = (
        "bngdin.com",
        "bongacams",
        "spyglass",
        "reklon.net",
        "doubleclick",
        "googlesyndication",
        "adservice",
        "exoclick",
        "trafficjunky",
        "/promo.php",
        "dynamic_banner",
    )
    return any(marker in s for marker in ad_hosts_or_markers)


def _extract_native_embed_url(html: str, video_url: str) -> Optional[str]:
    m = re.search(r"https?://(?:www\.)?milfporn8\.net/embed/\d+\b", html, flags=re.IGNORECASE)
    if m:
        return m.group(0).strip()
    vm = re.search(r"/videos/(\d+)/", video_url)
    if vm:
        return f"https://milfporn8.net/embed/{vm.group(1)}"
    return None


def _stream_quality_from_url(url: str) -> str:
    low = (url or "").lower()
    if _is_preview_media_url(url):
        return "preview"
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if _detect_media_format(url) == "hls":
        return "adaptive"
    return "source"


def _extract_streams(soup: BeautifulSoup, html: str, video_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("//"):
            href = f"https:{href}"
        elif href.startswith("/"):
            href = urljoin(video_url, href)
        fmt = _detect_media_format(href)
        if href.startswith("http") and href not in seen and fmt:
            seen.add(href)
            streams.append({"url": href, "quality": _stream_quality_from_url(href), "format": fmt})
    for video in soup.select("video"):
        for source in video.select("source[src]"):
            src = (source.get("src") or "").strip()
            if not src:
                continue
            if src.startswith("//"):
                src = f"https:{src}"
            elif src.startswith("/"):
                src = urljoin(video_url, src)
            fmt = _detect_media_format(src)
            if not src.startswith("http") or src in seen or not fmt:
                continue
            seen.add(src)
            streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})
    for src in _extract_inline_urls(html):
        if src in seen:
            continue
        fmt = _detect_media_format(src)
        if not fmt:
            continue
        seen.add(src)
        streams.append({"url": src, "quality": _stream_quality_from_url(src), "format": fmt})
    for iframe in soup.select("iframe[src]"):
        src = (iframe.get("src") or "").strip()
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = urljoin(video_url, src)
        if not src.startswith("http") or src in seen or _is_probable_ad_iframe(src):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": "embed", "format": "embed"})
    native_embed = _extract_native_embed_url(html, video_url)
    if native_embed and native_embed not in seen:
        seen.add(native_embed)
        streams.append({"url": native_embed, "quality": "milfporn8", "format": "embed"})

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
        if fmt == "embed" and "milfporn8.net/embed/" in (item.get("url") or "").lower():
            return (1, 1)
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
    return {"streams": materialized, "hls": hls_url, "default": default_url, "has_video": bool(materialized)}


async def _get_file_to_remote_playable(get_file_url: str, *, referer: str) -> Optional[str]:
    base = get_file_url.split("?", 1)[0].strip().rstrip("/")
    ref = referer.strip() if referer.strip().startswith("http") else "https://milfporn8.net/"
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
            resp = await client.head(url, headers=h) if method == "HEAD" else await client.get(url, headers=h)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            if loc and "remote_control.php" in loc:
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
    )


async def _resolve_video_streams_to_remote_playable(video: dict[str, Any], *, referer: str) -> None:
    streams: list[dict[str, str]] = video.get("streams") or []
    get_file_mp4 = [s for s in streams if s.get("format") == "mp4" and "get_file" in (s.get("url") or "")]
    if not get_file_mp4:
        return
    video_id = _extract_video_id(referer)

    async def _resolve_one(stream: dict[str, str]) -> tuple[dict[str, str], Optional[str]]:
        return stream, await _get_file_to_remote_playable(stream["url"], referer=referer)

    resolved_pairs = await asyncio.gather(*[_resolve_one(s) for s in get_file_mp4])
    for stream, resolved in resolved_pairs:
        if resolved:
            if video_id and not _url_contains_video_id(resolved, video_id):
                streams.remove(stream)
                continue
            stream["url"] = resolved
        else:
            streams.remove(stream)

    remote_mp4 = [s for s in streams if s.get("format") == "mp4" and "remote_control.php" in (s.get("url") or "")]
    hls = next((s for s in streams if s.get("format") == "hls"), None)
    embed = next((s for s in streams if s.get("format") == "embed"), None)
    if remote_mp4:
        video["default"] = remote_mp4[0]["url"]
    elif hls:
        video["default"] = hls["url"]
    elif embed:
        video["default"] = embed["url"]
    else:
        video["default"] = None
    video["hls"] = hls["url"] if hls else None
    video["has_video"] = bool(remote_mp4) or bool(hls) or bool(embed)


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    json_ld = _parse_json_ld(soup)
    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"
    description = _first_non_empty(_meta(soup, prop="og:description"), _meta(soup, name="twitter:description"), _meta(soup, name="description"))
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"
    text_blob = soup.get_text(" ", strip=True)
    duration = _extract_duration(text_blob)
    views = _extract_views(text_blob)
    upload_date = _first_non_empty(_meta(soup, prop="article:published_time"), _meta(soup, prop="article:modified_time"))
    tags: list[str] = []
    uploader_name = None
    for obj in json_ld:
        types = obj.get("@type")
        tnames = [str(x).lower() for x in types] if isinstance(types, list) else [str(types).lower()]
        if "videoobject" not in tnames:
            continue
        title = _clean_title(_first_non_empty(obj.get("name"), title)) or title
        description = _first_non_empty(description, obj.get("description"))
        thumb = obj.get("thumbnailUrl") or obj.get("thumbnail")
        if isinstance(thumb, list):
            thumb = next((x for x in thumb if isinstance(x, str) and x.strip()), None)
        thumbnail = _first_non_empty(thumbnail, thumb)
        duration = _first_non_empty(duration, str(obj.get("duration")) if obj.get("duration") else None)
        upload_date = _first_non_empty(upload_date, obj.get("datePublished"), obj.get("dateModified"))
        tags.extend(_as_list(obj.get("keywords")))
        author = obj.get("author")
        if isinstance(author, dict):
            uploader_name = _first_non_empty(author.get("name"), uploader_name)
        elif isinstance(author, str):
            uploader_name = _first_non_empty(author, uploader_name)
    if description is None:
        m = re.search(r"Submitted by:\s*(.+?)\s+Download:", text_blob, flags=re.IGNORECASE)
        if m:
            description = m.group(1).strip()
    video = _extract_streams(soup, html, url)
    tags = list(dict.fromkeys([t for t in tags if t]))
    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader_name,
        "category": None,
        "tags": tags,
        "upload_date": upload_date,
        "video": video,
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
    netloc = p.netloc or "milfporn8.net"
    path = p.path or "/"
    query_items = dict(parse_qsl(p.query, keep_blank_values=True))
    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))
    clean_path = re.sub(r"/page/\d+/?$", "/", path)
    if query_items.get("q") or "/search/" in clean_path:
        query_items["page"] = str(page)
        return urlunparse((scheme, netloc, clean_path, "", urlencode(query_items), ""))
    paged_path = clean_path.rstrip("/") + f"/{page}/"
    return urlunparse((scheme, netloc, paged_path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or "https://milfporn8.net/")
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
        title_el = container.select_one(".title") if container else None
        duration_el = container.select_one(".duration") if container else None
        views_el = container.select_one(".views") if container else None
        title = (
            (title_el.get_text(" ", strip=True) if title_el else None)
            or a.get("title")
            or (img.get("alt") if img else None)
            or a.get_text(" ", strip=True)
        )
        title = _clean_list_title(title) or "Unknown Video"
        ctext = container.get_text(" ", strip=True) if container else ""
        duration = _extract_duration(duration_el.get_text(" ", strip=True) if duration_el else ctext)
        views = _extract_views(views_el.get_text(" ", strip=True) if views_el else ctext)
        seen.add(href)
        items.append({"url": href, "title": title, "thumbnail_url": thumb, "duration": duration, "views": views, "uploader_name": None})
    return items[:limit]
