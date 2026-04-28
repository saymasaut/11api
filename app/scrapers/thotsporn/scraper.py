from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://thotsporn.com/"
SITE_HOST = "thotsporn.com"


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


async def fetch_page(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": BASE_SITE,
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
    for suffix in (" - Thots Porn", " | Thots Porn"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_media_url(src: str, base: str = BASE_SITE) -> Optional[str]:
    u = (src or "").strip()
    if not u:
        return None
    if u.startswith("//"):
        u = f"https:{u}"
    elif u.startswith("/"):
        u = urljoin(base, u)
    if not u.startswith("http"):
        return None
    # Resolve known embed mirror host to canonical player host.
    try:
        p = urlparse(u)
        host = (p.netloc or "").lower()
        if "vidhidepre.com" in host:
            u = urlunparse((p.scheme or "https", "callistanise.com", p.path, p.params, p.query, p.fragment))
    except Exception:
        pass
    return u


def _quality_from_url(url: str, *, fallback: str = "source") -> str:
    low = (url or "").lower()
    q = re.search(r"([1-9]\d{2,3})p", low)
    if q:
        return f"{q.group(1)}p"
    if ".m3u8" in low:
        return "adaptive"
    return fallback


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


def _extract_inline_media_candidates(html: str) -> list[str]:
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    out: list[str] = []
    patterns = (
        r"""(?:file|src)\s*[:=]\s*["'](https?://[^"']+\.(?:m3u8|mp4)[^"']*)["']""",
        r"""["'](https?://[^"']+\.(?:m3u8|mp4)[^"']*)["']""",
    )
    for pat in patterns:
        for m in re.finditer(pat, unescaped, flags=re.IGNORECASE):
            url = (m.group(1) if m.groups() else m.group(0)).strip()
            if url.startswith(("http://", "https://")):
                out.append(url)
    return list(dict.fromkeys(out))


def _is_probable_ad_iframe(src: str) -> bool:
    s = (src or "").lower()
    if "callistanise.com/embed/" in s:
        return False
    if "vidhidepre.com/embed/" in s:
        return False
    blocked_markers = (
        "googlesyndication",
        "doubleclick",
        "adservice",
        "trafficjunky",
        "/delivery/afr.php",
        "/ox/",
        "zoneid=",
        "campaignid=",
        "creativeid=",
        "spot=",
        "affid=",
        "popads",
        "exoclick",
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
        for marker in (
            "callistanise.com/embed/",
            "vidhidepre.com/embed/",
            "/embed/",
            "player",
            "stream",
            ".m3u8",
            ".mp4",
            "video",
            "iframe",
        )
    )


def _is_blocked_stream_url(url: str) -> bool:
    low = (url or "").lower()
    return (
        "prog-public-ht.project1content.com" in low
        or "/mediabook/" in low
        or "mediabook_320p.mp4" in low
    )


def _extract_streams(soup: BeautifulSoup, html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for video in soup.select("video"):
        src = _normalize_media_url(video.get("src") or "")
        if src and src not in seen:
            if _is_blocked_stream_url(src):
                continue
            seen.add(src)
            streams.append({"url": src, "quality": _quality_from_url(src), "format": "hls" if ".m3u8" in src.lower() else "mp4"})
        for source in video.select("source[src]"):
            src = _normalize_media_url(source.get("src") or "")
            if not src or src in seen:
                continue
            if _is_blocked_stream_url(src):
                continue
            seen.add(src)
            streams.append({"url": src, "quality": _quality_from_url(src), "format": "hls" if ".m3u8" in src.lower() else "mp4"})

    for src in _extract_inline_urls(html):
        if src in seen:
            continue
        if _is_blocked_stream_url(src):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": _quality_from_url(src), "format": "hls" if ".m3u8" in src.lower() else "mp4"})

    for src in _extract_inline_media_candidates(html):
        if src in seen:
            continue
        if _is_blocked_stream_url(src):
            continue
        seen.add(src)
        streams.append({"url": src, "quality": _quality_from_url(src), "format": "hls" if ".m3u8" in src.lower() else "mp4"})

    # Explicit fallback patterns seen on ThotsPorn embeds/CDN wrappers.
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")
    for pat in (
        r"""https?://callistanise\.com/stream/[^\s"'<>]+\.m3u8[^\s"'<>]*""",
        r"""https?://callistanise\.com/embed/[^\s"'<>/]+""",
        r"""https?://vidhidepre\.com/embed/[^\s"'<>/]+""",
    ):
        for m in re.finditer(pat, unescaped, flags=re.IGNORECASE):
            raw = m.group(0).strip()
            stream_url = _normalize_media_url(raw)
            if not stream_url or stream_url in seen:
                continue
            if _is_blocked_stream_url(stream_url):
                continue
            seen.add(stream_url)
            if "/embed/" in stream_url.lower():
                streams.append({"url": stream_url, "quality": "Server 1", "format": "embed"})
            else:
                streams.append({"url": stream_url, "quality": _quality_from_url(stream_url, fallback="adaptive"), "format": "hls"})

    server_idx = 1
    for iframe in soup.select("iframe[src]"):
        iframe_src = _normalize_media_url(iframe.get("src") or "")
        if not iframe_src or iframe_src in seen:
            continue
        if not _is_probable_playable_embed(iframe_src):
            continue
        seen.add(iframe_src)
        streams.append({"url": iframe_src, "quality": f"Server {server_idx}", "format": "embed"})
        server_idx += 1

    for tag in soup.select("meta[itemprop='embedURL'][content]"):
        embed_url = _normalize_media_url(tag.get("content") or "")
        if not embed_url or embed_url in seen:
            continue
        if not _is_probable_playable_embed(embed_url):
            continue
        seen.add(embed_url)
        streams.append({"url": embed_url, "quality": f"Server {server_idx}", "format": "embed"})
        server_idx += 1

    for m in re.finditer(r"""iframe(?:Src)?\s*[:=]\s*['"]([^'"]+)['"]""", html, flags=re.IGNORECASE):
        embed_url = _normalize_media_url(m.group(1))
        if not embed_url or embed_url in seen:
            continue
        if not _is_probable_playable_embed(embed_url):
            continue
        seen.add(embed_url)
        streams.append({"url": embed_url, "quality": f"Server {server_idx}", "format": "embed"})
        server_idx += 1

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        q = item.get("quality") or ""
        digits = re.search(r"(\d{3,4})", q)
        quality_score = int(digits.group(1)) if digits else 0
        if fmt == "mp4":
            return (3, quality_score)
        if fmt == "hls":
            return (2, quality_score)
        return (1, 0)

    streams = list(dict.fromkeys((json.dumps(s, sort_keys=True) for s in streams)))
    materialized = [json.loads(s) for s in streams]
    materialized.sort(key=_score, reverse=True)

    default_url = None
    for fmt in ("mp4", "hls", "embed"):
        match = next((s for s in materialized if s.get("format") == fmt), None)
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


def _extract_views_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(\d[\d,\.]*\s*[KMBkmb]?)\s*(?:views|view)?\b", text, re.IGNORECASE)
    if not m:
        return None
    txt = m.group(1).strip().replace(",", "").replace("\u00a0", "")
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


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
    duration = None
    dm = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text_blob)
    if dm:
        duration = dm.group(0)

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": _extract_views_text(text_blob),
        "uploader_name": None,
        "category": _meta(soup, prop="article:section"),
        "tags": [],
        "upload_date": _first_non_empty(
            _meta(soup, prop="article:published_time"),
            _meta(soup, prop="article:modified_time"),
        ),
        "video": _extract_streams(soup, html),
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_page(url)
    return parse_video_page(html, url)


def _is_probable_video_post(parsed: Any) -> bool:
    path = parsed.path.rstrip("/")
    if not path:
        return False
    segments = [s for s in path.split("/") if s]
    if len(segments) != 1:
        return False
    slug = segments[0].lower()
    blocked_exact = {
        "categories",
        "category",
        "tags",
        "tag",
        "actors",
        "actor",
        "feed",
        "page",
        "wp-admin",
        "wp-content",
        "login",
        "register",
        "reset-password",
        "privacy-policy",
        "terms",
        "dmca",
        "18-u-s-c-2257",
        "18-usc-2257",
    }
    return slug not in blocked_exact


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    if not href.startswith("http"):
        return None

    parsed = urlparse(href)
    if SITE_HOST not in parsed.netloc.lower():
        return None
    if any(
        x in parsed.path.lower()
        for x in (
            "/wp-content/",
            "/wp-json/",
            "/category/",
            "/categories/",
            "/tag/",
            "/tags/",
            "/actor/",
            "/actors/",
            "/page/",
            "/author/",
            "/feed/",
        )
    ):
        return None
    if parsed.query:
        return None
    if not _is_probable_video_post(parsed):
        return None
    slug = parsed.path.strip("/").split("/", 1)[0]
    return urlunparse(("https", SITE_HOST, f"/{slug}/", "", "", ""))


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or SITE_HOST
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

        title = a.get("title") or (img.get("alt") if img else None) or a.get_text(" ", strip=True)
        title = _clean_title(title) or "Unknown Video"

        ctext = container.get_text(" ", strip=True) if container else ""
        duration = None
        dm = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", ctext)
        if dm:
            duration = dm.group(0)

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": _extract_views_text(ctext),
                "uploader_name": None,
            }
        )

    return items[:limit]
