from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import aiohttp
import httpx
from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html, get_random_user_agent

logger = logging.getLogger(__name__)

BASE_SITE = "https://www.porntrex.com/"
SITE_HOST = "porntrex.com"
SITE_ALIASES = frozenset({"porntrex.com", "www.porntrex.com"})

_DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Referer": BASE_SITE,
}

_VIDEO_PATH_RE = re.compile(r"^/video/(\d+)/[^/]+/?$", re.IGNORECASE)
_EMBED_PATH_RE = re.compile(r"^/embed/(\d+)/?$", re.IGNORECASE)
_VIDEO_HREF_RE = re.compile(
    r'href=["\'](?:https?://(?:www\.)?porntrex\.com)?/video/(\d+)/([^"\']+)/?["\']',
    re.IGNORECASE,
)
_REQUEST_LOCK = asyncio.Lock()
_NEXT_ALLOWED_REQUEST_AT = 0.0
_MIN_REQUEST_INTERVAL_SECONDS = 1.25


async def _throttle_requests() -> None:
    """Global per-process pacing to reduce upstream rate-limit triggers."""
    global _NEXT_ALLOWED_REQUEST_AT
    async with _REQUEST_LOCK:
        now = time.monotonic()
        wait_for = _NEXT_ALLOWED_REQUEST_AT - now
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        _NEXT_ALLOWED_REQUEST_AT = time.monotonic() + _MIN_REQUEST_INTERVAL_SECONDS


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".porntrex.com")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _browser_headers(referer: str | None = None) -> dict[str, str]:
    """Full browser-like headers on every PornTrex request (stable User-Agent)."""
    headers = dict(_DEFAULT_HEADERS)
    headers["User-Agent"] = get_random_user_agent()
    ref = referer or BASE_SITE
    headers["Referer"] = ref
    try:
        ref_host = (urlparse(ref).netloc or "").lower()
        headers["Sec-Fetch-Site"] = "same-origin" if "porntrex.com" in ref_host else "none"
    except Exception:
        headers["Sec-Fetch-Site"] = "none"
    return headers


def _sanitize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.scheme:
        parsed = urlparse(f"https://{url.lstrip('/')}")
    path = parsed.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunparse((parsed.scheme or "https", parsed.netloc, path, parsed.params, parsed.query, parsed.fragment))


async def _fetch_with_curl_cffi(url: str, *, headers: dict[str, str]) -> Optional[str]:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(_sanitize_url(url))
                if resp.status_code == 200 and resp.text:
                    return resp.text
        except Exception:
            continue
    return None


async def _fetch_with_httpx(url: str, *, headers: dict[str, str]) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        # Keep this relatively tight; PornTrex sometimes stalls long enough to
        # create a backlog under load. We'll retry at a higher level.
        timeout=httpx.Timeout(20.0, connect=12.0),
        headers=headers,
    ) as client:
        resp = await client.get(_sanitize_url(url))
        resp.raise_for_status()
        return resp.text


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    clean_url = _sanitize_url(url)
    headers = _browser_headers(referer)
    # PornTrex intermittently stalls TLS/connection establishment on some networks.
    # Keep timeouts reasonable, but not so strict that transient stalls dominate.
    timeout = aiohttp.ClientTimeout(total=35, connect=15, sock_read=25)
    pool_err: Exception | None = None

    await _throttle_requests()

    try:
        # Prefer httpx first: it has been more reliable than the pooled aiohttp path
        # in the presence of intermittent connect/TLS stalls.
        return await _fetch_with_httpx(clean_url, headers=headers)
    except Exception as httpx_err:
        logger.warning("PornTrex httpx fetch failed for %s: %s", clean_url, httpx_err)

    try:
        text = await asyncio.wait_for(_fetch_with_curl_cffi(clean_url, headers=headers), timeout=15)
    except Exception:
        text = None
    if text:
        return text

    try:
        return await pool_fetch_html(clean_url, headers=headers, timeout=timeout, retries=2)
    except Exception as exc:
        pool_err = exc
        logger.warning("PornTrex pool fetch failed for %s: %s", clean_url, exc)
        raise pool_err


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
        " / Embed Player",
        " - PornTrex",
        " | PornTrex",
        " - porntrex.com",
        " | porntrex.com",
        " - PornTrex.com",
        " | PornTrex.com",
        " - Best 4k Porn Site",
        " - Best 4k Porn Site - PornTrex",
        " - Best Free HD Porn Videos",
        " - Best Free HD Porn Videos - Best 4k Porn Site",
        " - Best Free HD Porn Videos - Best 4k Porn Site - PornTrex",
    ):
        if t.lower().endswith(suffix.lower()):
            t = t[: -len(suffix)].strip()
    if t.lower().startswith("best free hd porn videos"):
        return None
    return t or None


def _clean_list_title(title: str | None) -> Optional[str]:
    t = _clean_title(title)
    if not t:
        return None
    t = re.sub(
        r"\s+\d{1,2}:\d{2}(?::\d{2})?\s+\d{1,3}%\s+\d[\d\.\s]*[kKmMbB]?\s*$",
        "",
        t,
    ).strip()
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
    m = re.search(
        r"(\d[\d\s,\.]*)\s*views?\b",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"\bviews?\s*[:\-]?\s*(\d[\d\s,\.]*\s*[KMBkmb]?)\b",
            text,
            flags=re.IGNORECASE,
        )
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
        if not url or url.startswith("data:"):
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return urljoin(BASE_SITE, url)
        return url
    return None


def _extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"/(?:video|embed)/(\d+)(?:/|$)", url or "", flags=re.IGNORECASE)
    return m.group(1) if m else None


def _canonical_video_url(video_id: str, slug: str | None = None) -> str:
    if slug:
        slug = slug.strip("/")
        return f"https://www.porntrex.com/video/{video_id}/{slug}/"
    return f"https://www.porntrex.com/video/{video_id}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://www.porntrex.com{href}"

    parsed = urlparse(href)
    host = (parsed.netloc or "").lower()
    if SITE_HOST not in host:
        return None
    if parsed.query:
        return None

    path = parsed.path or ""
    embed_m = _EMBED_PATH_RE.match(path)
    if embed_m:
        return _canonical_video_url(embed_m.group(1))

    if not _VIDEO_PATH_RE.match(path):
        return None

    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 3 or parts[0].lower() != "video":
        return None
    vid, slug = parts[1], parts[2]
    return _canonical_video_url(vid, slug)


def _embed_page_url(video_id: str) -> str:
    return f"https://www.porntrex.com/embed/{video_id}/"


def _embed_video_data(video_id: str) -> dict[str, Any]:
    """Embed-only playback: built from video id, no page scrape for stream URLs."""
    embed_url = _embed_page_url(video_id)
    stream = {"url": embed_url, "quality": "porntrex", "format": "embed"}
    return {
        "streams": [stream],
        "hls": None,
        "default": embed_url,
        "has_video": True,
    }


def _list_card_container(item: Any) -> Any:
    """Find the nearest wrapper with this card's duration/views (avoid section-wide parents)."""
    best = item
    for parent in list(getattr(item, "parents", []))[:6]:
        if getattr(parent, "name", None) not in ("div", "li", "article"):
            continue
        if parent.select(".video-item[data-item-id], .thumb-item[data-item-id]"):
            siblings = parent.select(".video-item[data-item-id], .thumb-item[data-item-id]")
            if len(siblings) > 1:
                break
        text = parent.get_text(" ", strip=True) if hasattr(parent, "get_text") else ""
        if _extract_duration(text) or re.search(r"\bviews?\b", text, re.IGNORECASE):
            best = parent
    return best


def _parse_list_video_item(item: Any) -> Optional[dict[str, Any]]:
    vid = (item.get("data-item-id") or "").strip()
    link = item.select_one('a[href*="/video/"]') if hasattr(item, "select_one") else None
    if link is None:
        return None

    href = _normalize_video_href(link.get("href") or "")
    if not href and vid:
        slug = ""
        path_parts = (link.get("href") or "").strip("/").split("/")
        if len(path_parts) >= 3 and path_parts[-2] == vid:
            slug = path_parts[-1]
        href = _canonical_video_url(vid, slug or None)
    if not href:
        return None

    img = link.find("img") or item.select_one("img")
    thumb = _best_image_url(img)
    title = _clean_list_title(
        _first_non_empty(
            link.get("title"),
            img.get("alt") if img else None,
            link.get_text(" ", strip=True),
        )
    )

    container = _list_card_container(item)
    ctext = container.get_text(" ", strip=True) if hasattr(container, "get_text") else ""
    duration = None
    views = None
    for sel in (".duration", ".time", ".video-duration", ".thumb-duration", ".item-duration"):
        el = (item.select_one(sel) if hasattr(item, "select_one") else None) or (
            container.select_one(sel) if hasattr(container, "select_one") else None
        )
        if el and not duration:
            duration = _extract_duration(el.get_text(" ", strip=True))
    if not duration:
        duration = _extract_duration(ctext)
    views_el = (
        (item.select_one(".views") if hasattr(item, "select_one") else None)
        or (container.select_one(".views") if hasattr(container, "select_one") else None)
    )
    if views_el:
        views = _extract_views(views_el.get_text(" ", strip=True))
    if not views:
        views = _extract_views(ctext)

    return {
        "url": href,
        "title": title or "Unknown Video",
        "thumbnail_url": thumb,
        "duration": duration,
        "views": views,
        "uploader_name": None,
    }


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_video_href(url) or url

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
        _meta(soup, name="description"),
    )
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    text_blob = soup.get_text(" ", strip=True)
    views_el = soup.select_one(".views, .video-info .views")
    views_text = views_el.get_text(" ", strip=True) if views_el else None
    duration = _extract_duration(text_blob)
    views = _extract_views(views_text) or _extract_views(text_blob)

    tags: list[str] = []
    for el in soup.select(".tags a, a[href*='/tags/'], a[href*='/category/']"):
        tag = el.get_text(" ", strip=True)
        if tag and tag not in tags and len(tag) < 80:
            tags.append(tag)
    tags = list(dict.fromkeys(tags))

    uploader = None
    up = soup.select_one('a[href*="/models/"], a[href*="/channels/"], a[href*="/members/"]')
    if up:
        uploader = up.get_text(" ", strip=True) or None

    video_id = _extract_video_id(canon) or _extract_video_id(url)
    video = _embed_video_data(video_id) if video_id else {
        "streams": [],
        "hls": None,
        "default": None,
        "has_video": False,
    }

    return {
        "url": canon,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": _meta(soup, prop="article:section"),
        "tags": tags or None,
        "upload_date": _first_non_empty(
            _meta(soup, prop="article:published_time"),
            _meta(soup, prop="article:modified_time"),
        ),
        "video": video,
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    video_id = _extract_video_id(url)
    canon = _normalize_video_href(url)
    if not video_id:
        raise ValueError(f"Unsupported PornTrex URL: {url}")
    if not canon:
        canon = _canonical_video_url(video_id)

    data: dict[str, Any] = {
        "url": canon,
        "title": "Unknown Video",
        "description": None,
        "thumbnail_url": None,
        "duration": None,
        "views": None,
        "uploader_name": None,
        "category": None,
        "tags": None,
        "upload_date": None,
        "video": _embed_video_data(video_id),
        "related_videos": [],
        "preview_url": None,
    }

    try:
        html = await fetch_page(canon, referer=canon)
        parsed = parse_video_page(html, canon)
        for key in (
            "title",
            "description",
            "thumbnail_url",
            "duration",
            "views",
            "uploader_name",
            "category",
            "tags",
            "upload_date",
        ):
            val = parsed.get(key)
            if val:
                data[key] = val
    except Exception as exc:
        logger.warning("PornTrex metadata fetch failed for %s: %s", canon, exc)

    if not data.get("title") or data.get("title") == "Unknown Video":
        slug_title = _clean_title(
            (urlparse(canon).path or "").strip("/").split("/")[-1].replace("-", " ")
        )
        if slug_title:
            data["title"] = slug_title

    return data


_PAGINATED_SECTIONS = frozenset({"latest-updates", "top-rated", "most-popular"})
_LIST_FETCH_ATTEMPTS = 3


def _normalize_list_path(path: str) -> str:
    """Map legacy/wrong PornTrex list paths to URLs that exist on the site."""
    p = (path or "").strip("/")
    if not p:
        return ""
    if p == "most-viewed" or p.startswith("most-viewed/"):
        p = "most-popular" + p[len("most-viewed") :]
    if p == "category" or p.startswith("category/"):
        p = "categories" + p[len("category") :]
    parts = p.split("/")
    if parts and parts[-1].isdigit():
        if parts[0] == "categories" and len(parts) >= 3:
            parts = parts[:-1]
        elif len(parts) >= 2 and parts[-2] in _PAGINATED_SECTIONS:
            parts = parts[:-1]
        elif len(parts) == 2 and parts[0] in _PAGINATED_SECTIONS:
            parts = parts[:-1]
    return "/".join(parts)


def list_cache_key(base_url: str, page: int, limit: int) -> str:
    """Stable list cache key (canonical URL + page + limit)."""
    canon = _normalize_list_base_url(base_url)
    return f"list:porntrex:{canon}:p{max(1, int(page))}:l{max(1, int(limit))}"


def scrape_cache_key(url: str) -> str:
    """Stable scrape cache key (canonical video URL)."""
    canon = _normalize_video_href(url) or _sanitize_url(url)
    return f"scrape:porntrex:{canon}"


def _normalize_list_base_url(base_url: str) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(_sanitize_url(raw))
    path = _normalize_list_path((parsed.path or "/").strip("/"))
    list_path = f"/{path}/" if path else "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", list_path, "", "", ""))


def _build_list_page_url(base_url: str, page: int) -> str:
    base = _normalize_list_base_url(base_url)
    parsed = urlparse(base)
    page_num = max(1, int(page) if page else 1)
    path = (parsed.path or "/").strip("/")
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if page_num <= 1:
        list_path = f"/{path}/" if path else "/"
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", list_path, "", urlencode(query_items), "")
        )

    if path and re.search(r"/\d+$", f"/{path}"):
        path = re.sub(r"/\d+$", "", path)

    if "page" in query_items:
        query_items["page"] = str(page_num)
        list_path = f"/{path}/" if path else "/"
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", list_path, "", urlencode(query_items), "")
        )

    list_path = f"/{path}/{page_num}/" if path else f"/{page_num}/"
    return urlunparse(
        (parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", list_path, "", urlencode(query_items), "")
    )


def _list_page_candidates(base_url: str, page: int) -> list[str]:
    primary = _build_list_page_url(base_url, page)
    candidates = [primary]
    parsed = urlparse(_normalize_list_base_url(base_url))
    path = (parsed.path or "/").strip("/")
    root = f"{parsed.scheme or 'https'}://{parsed.netloc or f'www.{SITE_HOST}'}"

    # PornTrex list routes can shift between `/latest/` and `/latest-updates/`.
    if path == "latest":
        route_variants = ("latest", "latest-updates")
    elif path == "latest-updates":
        route_variants = ("latest-updates", "latest")
    else:
        route_variants = (path,)

    for route in route_variants:
        if not route:
            continue
        if page <= 1:
            alt = f"{root}/{route}/"
            if alt not in candidates:
                candidates.append(_sanitize_url(alt))
        else:
            for alt in (f"{root}/{route}/{page}/", f"{root}/{route}/?page={page}"):
                if alt not in candidates:
                    candidates.append(_sanitize_url(alt))

    if page <= 1 and path in ("", "latest", "latest-updates"):
        for alt in (BASE_SITE, f"{BASE_SITE}latest/", f"{BASE_SITE}latest-updates/"):
            if alt not in candidates:
                candidates.append(_sanitize_url(alt))
    return candidates


def _is_list_page_html(html: str) -> bool:
    if not html or len(html) < 5000:
        return False
    if "adult website" in html.lower() and "data-item-id" not in html:
        return False
    return bool(
        re.search(r'data-item-id=["\']\d+["\']', html)
        or re.search(r'class=["\'][^"\']*video-item[^"\']*["\']', html, re.IGNORECASE)
        or re.search(r'href=["\']/(?:video|embed)/\d+/', html, re.IGNORECASE)
    )


def _parse_list_html(html: str, *, limit: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    effective_limit = max(1, int(limit)) if limit else 100

    def _upsert(entry: dict[str, Any]) -> None:
        href = entry.get("url")
        if not href or href in seen:
            return
        seen.add(href)
        items.append(entry)

    for card in soup.select(
        ".video-item[data-item-id], .thumb-item[data-item-id], [data-item-id].video-item"
    ):
        if len(items) >= effective_limit:
            break
        parsed = _parse_list_video_item(card)
        if parsed:
            _upsert(parsed)

    if len(items) < effective_limit:
        for a in soup.select("a[href*='/video/']"):
            if len(items) >= effective_limit:
                break
            href = _normalize_video_href(a.get("href") or "")
            if not href:
                continue

            if href in seen:
                for i, existing in enumerate(items):
                    if existing.get("url") != href:
                        continue
                    container = _list_card_container(a.find_parent(["article", "li", "div"]) or a)
                    img = a.find("img") or (container.find("img") if container else None)
                    title = _clean_list_title(
                        _first_non_empty(
                            a.get("title"),
                            img.get("alt") if img else None,
                            a.get_text(" ", strip=True),
                        )
                    )
                    if title and title != "Unknown Video":
                        existing["title"] = title
                    thumb = _best_image_url(img)
                    if thumb:
                        existing["thumbnail_url"] = thumb
                    ctext = container.get_text(" ", strip=True) if container else ""
                    existing["duration"] = existing.get("duration") or _extract_duration(ctext)
                    existing["views"] = existing.get("views") or _extract_views(ctext)
                continue

            container = _list_card_container(a.find_parent(["article", "li", "div"]) or a)
            img = a.find("img") or (container.find("img") if container else None)
            thumb = _best_image_url(img)
            title = _clean_list_title(
                _first_non_empty(
                    a.get("title"),
                    img.get("alt") if img else None,
                    a.get_text(" ", strip=True),
                )
            ) or "Unknown Video"
            ctext = container.get_text(" ", strip=True) if container else ""
            duration = _extract_duration(ctext)
            views_el = container.select_one(".views") if container else None
            views_text = views_el.get_text(" ", strip=True) if views_el else None
            views = _extract_views(views_text) or _extract_views(ctext)

            _upsert(
                {
                    "url": href,
                    "title": title,
                    "thumbnail_url": thumb,
                    "duration": duration,
                    "views": views,
                    "uploader_name": None,
                }
            )

    if len(items) < effective_limit:
        for vid, slug in _VIDEO_HREF_RE.findall(str(soup)):
            if len(items) >= effective_limit:
                break
            href = _canonical_video_url(vid, slug)
            if href in seen:
                continue
            _upsert(
                {
                    "url": href,
                    "title": "Unknown Video",
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                }
            )

    return items[:effective_limit]


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    referer = _normalize_list_base_url(base_url)
    effective_limit = max(1, int(limit)) if limit else 100
    last_error: Exception | None = None
    candidates = _list_page_candidates(base_url, page)

    for attempt in range(1, _LIST_FETCH_ATTEMPTS + 1):
        for page_url in candidates:
            try:
                html = await fetch_page(page_url, referer=referer)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "PornTrex list fetch failed for %s (attempt %s/%s): %s",
                    page_url,
                    attempt,
                    _LIST_FETCH_ATTEMPTS,
                    exc,
                )
                continue

            if not _is_list_page_html(html):
                logger.warning(
                    "PornTrex list page invalid HTML from %s (attempt %s/%s)",
                    page_url,
                    attempt,
                    _LIST_FETCH_ATTEMPTS,
                )
                continue

            items = _parse_list_html(html, limit=effective_limit)
            if items:
                return items

        if attempt < _LIST_FETCH_ATTEMPTS:
            # Short jittered backoff to recover from transient anti-bot responses.
            await asyncio.sleep(0.8 + (attempt * 0.6))

    if last_error:
        logger.error("PornTrex list_videos exhausted candidates for %s page %s: %s", base_url, page, last_error)
    return []


async def crawl_videos(
    base_url: str,
    start_page: int = 1,
    max_pages: int = 5,
    per_page_limit: int = 0,
    max_items: int = 500,
) -> list[dict[str, Any]]:
    if start_page < 1:
        start_page = 1
    if max_pages < 1:
        max_pages = 1
    if per_page_limit < 0:
        per_page_limit = 0
    if max_items < 1:
        max_items = 1

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_limit = per_page_limit if per_page_limit > 0 else 100

    for page in range(start_page, start_page + max_pages):
        page_items = await list_videos(base_url=base_url, page=page, limit=page_limit)
        if not page_items:
            break
        for it in page_items:
            url = str(it.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            results.append(it)
            if len(results) >= max_items:
                return results
    return results
