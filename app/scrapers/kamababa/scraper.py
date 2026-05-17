from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    # Updated to handle the new x-suffixed domain
    return h == "kamababax.com" or h.endswith(".kamababax.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.kamababax.com/",
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


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,|\n]", value) if x.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_duration(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        total = int(value)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
    if isinstance(value, str):
        v = value.strip()
        match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v)
        if match:
            h = int(match.group(1) or 0)
            m = int(match.group(2) or 0)
            s = int(match.group(3) or 0)
            if h > 0:
                return f"{h}:{m:02d}:{s:02d}"
            return f"{m}:{s:02d}"
        return v or None
    return str(value).strip() or None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-lazy-src", "data-original", "srcset", "src"):
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


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = title.strip()
    # Updated title cleaning suffixes
    for suffix in (" - Kamababax", " | Kamababax", " - TheKamababax", " | TheKamababax", " - KamaBaba", " | KamaBaba"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _clean_views_text(v: str | None) -> Optional[str]:
    if not v:
        return None
    txt = str(v).strip().replace(",", "").replace("\u00a0", "")
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


def _extract_views_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(\d[\d,\.]*\s*[KMBkmb]?)\s*(?:views|view)?\b", text, re.IGNORECASE)
    if not m:
        return None
    return _clean_views_text(m.group(1))


def _extract_views_from_container(container: Any) -> Optional[str]:
    if container is None:
        return None
    tag = container.select_one(".views")
    if tag:
        txt = tag.get_text(" ", strip=True)
        if txt:
            direct = _extract_views_text(txt)
            if direct:
                return direct
            return _clean_views_text(txt)
    return None


def _quality_from_url(url: str, *, fallback: str = "source") -> str:
    low = (url or "").lower()
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if ".m3u8" in low:
        return "adaptive"
    return fallback


def _is_probable_video_post(parsed: Any) -> bool:
    path = parsed.path.rstrip("/")
    if not path:
        return False
    segments = [s for s in path.split("/") if s]
    if len(segments) != 1:
        return False
    slug = segments[0].lower()
    blocked_exact = {
        "contact-us", "contact", "video-removal", "privacy-policy",
        "18-usc-2257", "categories", "category", "tags", "tag",
        "support", "advertise", "jobs", "unblock-kmb", "about-us",
        "author", "feed", "page", "wp-admin", "wp-content", "login",
        "register", "reset-password",
    }
    return slug not in blocked_exact


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://www.kamababax.com{href}"
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if "kamababax.com" not in parsed.netloc.lower():
        return None
    if any(
        x in parsed.path.lower()
        for x in (
            "/wp-content/", "/wp-json/", "/category/", "/categories/",
            "/tag/", "/tags/", "/page/", "/author/", "/feed/", "/support/",
        )
    ):
        return None
    if parsed.query:
        return None
    if not _is_probable_video_post(parsed):
        return None
    slug = parsed.path.strip("/").split("/", 1)[0]
    return urlunparse(("https", "www.kamababax.com", f"/{slug}/", "", "", ""))


def _extract_inline_urls(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    urls: list[str] = []
    for pat in (
        r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*",
        r"https?://[^\s\"'<>]+\.mp4[^\s\"'<>]*",
    ):
        for m in re.finditer(pat, unescaped, flags=re.IGNORECASE):
            url = m.group(0).strip()
            if url:
                urls.append(url)
    return list(dict.fromkeys(urls))


def _slug_tokens_from_url(page_url: str) -> set[str]:
    try:
        parsed = urlparse(page_url)
        slug = (parsed.path or "").strip("/").split("/", 1)[0]
        if not slug:
            return set()
        return {t for t in re.split(r"[^a-z0-9]+", slug.lower()) if len(t) >= 3}
    except Exception:
        return set()


def _candidate_url_score(stream_url: str, slug_tokens: set[str]) -> tuple[int, int]:
    low = (stream_url or "").lower()
    # Check for both old and new CDN patterns if applicable
    cdn_score = 2 if "cdn.kamababax.com" in low or "cdn.kamababa" in low else 0
    token_hits = sum(1 for t in slug_tokens if t in low)
    return (cdn_score + min(token_hits, 3), token_hits)


def _normalize_media_url(src: str, base: str = "https://www.kamababax.com/") -> Optional[str]:
    u = (src or "").strip()
    if not u:
        return None
    if u.startswith("//"):
        u = f"https:{u}"
    elif u.startswith("/"):
        u = urljoin(base, u)
    if not u.startswith("http"):
        return None
    return u


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    blocked_markers = (
        "googlesyndication", "doubleclick", "adservice", "trafficjunky",
        "blazingserver.net", "videobaba.xyz", "dscgirls.live", "xlviiirdr.com",
        "/delivery/afr.php", "/ox/", "zoneid=", "campaignid=", "creativeid=",
        "spot=", "affid=",
    )
    return any(x in s for x in blocked_markers)


def _is_probable_playable_embed(src: str) -> bool:
    s = (src or "").strip()
    if not s:
        return False
    low = s.lower()
    if _is_probable_ad_iframe(low):
        return False
    return any(
        marker in low
        for marker in ("/embed/", "player", "stream", ".m3u8", ".mp4", "video", "iframe")
    )


def _decode_tubeserver_url(src: str) -> Optional[str]:
    try:
        parsed = urlparse(src)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        token = (query.get("tubeserver") or "").strip()
        if not token:
            return None
        normalized = token.replace("-", "+").replace("_", "/")
        normalized += "=" * ((4 - (len(normalized) % 4)) % 4)
        decoded = base64.b64decode(normalized).decode("utf-8", errors="ignore").strip()
        media_url = _normalize_media_url(decoded)
        if not media_url:
            return None
        if media_url.lower().endswith(".mp4") or ".mp4?" in media_url.lower():
            return media_url
        return None
    except Exception:
        return None


def _extract_streams(soup: BeautifulSoup, html: str, page_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    # Generic video tags extraction
    for video in soup.select("video"):
        src = _normalize_media_url(video.get("src") or "")
        if src and src not in seen:
            seen.add(src)
            streams.append({"url": src, "quality": _quality_from_url(src), "format": "hls" if ".m3u8" in src.lower() else "mp4"})
        for source in video.select("source[src]"):
            src = _normalize_media_url(source.get("src") or "")
            if not src or src in seen:
                continue
            seen.add(src)
            streams.append({"url": src, "quality": _quality_from_url(src), "format": "hls" if ".m3u8" in src.lower() else "mp4"})

    # Regex fallback for scripts
    for src in _extract_inline_urls(html):
        if src in seen:
            continue
        seen.add(src)
        streams.append({"url": src, "quality": _quality_from_url(src), "format": "hls" if ".m3u8" in src.lower() else "mp4"})

    server_idx = 1
    for iframe in soup.select("iframe[src]"):
        iframe_src = _normalize_media_url(iframe.get("src") or "")
        if not iframe_src:
            continue

        decoded_mp4 = _decode_tubeserver_url(iframe_src)
        if decoded_mp4 and decoded_mp4 not in seen:
            seen.add(decoded_mp4)
            streams.append({"url": decoded_mp4, "quality": _quality_from_url(decoded_mp4), "format": "mp4"})

        if iframe_src in seen or not _is_probable_playable_embed(iframe_src):
            continue
        seen.add(iframe_src)
        streams.append({"url": iframe_src, "quality": f"Server {server_idx}", "format": "embed"})
        server_idx += 1

    # Sorting and selecting default
    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        q = item.get("quality") or ""
        digits = re.search(r"(\d{3,4})", q)
        quality_score = int(digits.group(1)) if digits else 0
        if fmt == "mp4": return (3, quality_score)
        if fmt == "hls": return (2, quality_score)
        return (1, 0)

    materialized = [json.loads(s) for s in list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in streams)))]
    materialized.sort(key=_score, reverse=True)

    default_url = None
    for fmt in ("mp4", "hls", "embed"):
        match = next((s for s in materialized if s.get("format") == fmt), None)
        if match:
            default_url = match.get("url")
            break

    return {
        "streams": materialized,
        "hls": next((s.get("url") for s in materialized if s.get("format") == "hls"), None),
        "default": default_url,
        "has_video": bool(materialized),
    }


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

    # Initialize extracted value placeholders
    ld_views: Optional[str] = None
    ld_duration: Optional[str] = None

    # Pull metrics out of JSON-LD data structures if available
    for graph in json_ld:
        if not isinstance(graph, dict):
            continue
        # Check standard VideoObject schemas
        if graph.get("@type") == "VideoObject" or "VideoObject" in _as_list(graph.get("@type")):
            if "interactionCount" in graph:
                ld_views = _extract_views_text(str(graph["interactionCount"]))
            elif graph.get("interactionStatistic"):
                stats = _as_list(graph["interactionStatistic"])
                for stat in stats:
                    if isinstance(stat, dict) and "userInteractionCount" in stat:
                        ld_views = _clean_views_text(str(stat["userInteractionCount"]))
            if "duration" in graph:
                ld_duration = _normalize_duration(graph["duration"])

    # Fallback to BeautifulSoup logic if JSON-LD parsing leaves fields empty
    html_views = _extract_views_from_container(soup) or _extract_views_text(soup.get_text(" ", strip=False))
    
    # Check common HTML selectors for standalone duration formats (e.g., "05:21")
    html_duration = None
    duration_tag = soup.select_one(".duration, .video-duration, .time")
    if duration_tag:
        html_duration = _normalize_duration(duration_tag.get_text(strip=True))

    views = _first_non_empty(ld_views, html_views)
    duration = _first_non_empty(ld_duration, html_duration)

    video = _extract_streams(soup, html, url)

    return {
        "url": url,
        "title": title,
        "thumbnail_url": _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image")),
        "views": views,
        "duration": duration,
        "video": video,
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url)
    return parse_video_page(html, url)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or "www.kamababax.com"
    path = parsed.path or "/"
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query_items), ""))

    clean_path = re.sub(r"/page/\d+/?$", "/", path or "/")
    if query_items.get("s"):
        query_items["paged"] = str(page)
        return urlunparse((scheme, netloc, clean_path or "/", "", urlencode(query_items), ""))

    paged_path = clean_path.rstrip("/") + f"/page/{page}/"
    return urlunparse((scheme, netloc, paged_path, "", urlencode(query_items), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url)
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

        title = _clean_title(a.get("title") or (img.get("alt") if img else None) or a.get_text(" ", strip=True)) or "Unknown Video"

        seen.add(href)
        items.append({
            "url": href,
            "title": title,
            "thumbnail_url": thumb,
            "uploader_name": None,
        })

    return items[:limit]