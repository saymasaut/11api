from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://jable.tv/"
DEFAULT_BROWSE_URL = "https://jable.tv/latest-updates/"
SITE_HOST = "jable.tv"
SITE_ALIASES = frozenset({"jable.tv", "www.jable.tv"})

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US,en;q=0.8",
    "Referer": BASE_SITE,
}

_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?jable\.tv(?:/s\d+)?/videos/(?P<slug>[a-z0-9-]+)/?$",
    re.IGNORECASE,
)
_VIDEO_HREF_RE = re.compile(r"/(?:s\d+/)?videos/([a-z0-9-]+)/?", re.IGNORECASE)
_HLS_URL_RE = re.compile(
    r"""(?:var|const|let)\s+hlsUrl\s*=\s*['"](https?://[^'"]+\.m3u8[^'"]*)['"]""",
    re.IGNORECASE,
)
_M3U8_RE = re.compile(r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*", re.IGNORECASE)
_PATH_PAGE_SUFFIX_RE = re.compile(r"^(.+)/(\d+)$")


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".jable.tv")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _is_cloudflare_challenge(html: str) -> bool:
    if not html:
        return True
    low = html.lower()
    return (
        "just a moment" in low
        or "cf_chl_opt" in low
        or "challenge-platform" in low
        or "enable javascript and cookies" in low
    )


async def _fetch_with_curl_cffi(url: str, *, referer: str | None = None) -> str | None:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None

    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer or BASE_SITE

    for imp in ("chrome120", "chrome110", "safari15_3"):
        try:
            async with AsyncSession(impersonate=imp, headers=headers, timeout=45.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                text = resp.text
                if _is_cloudflare_challenge(text):
                    continue
                return text
        except Exception:
            continue
    return None


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    text = await _fetch_with_curl_cffi(url, referer=referer)
    if text:
        return text

    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = referer or BASE_SITE
    html = await pool_fetch_html(url, headers=headers)
    if _is_cloudflare_challenge(html):
        raise ValueError(f"Blocked by Cloudflare: {url}")
    return html


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
        " - Jable.TV | 免費高清AV在線看 | J片 AV看到飽",
        " | Jable.TV",
        " - Jable.TV",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    return t or None


def _normalize_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return digits or None


def _extract_slug(url: str) -> Optional[str]:
    raw = (url or "").strip().split("#", 1)[0].split("?", 1)[0]
    m = _VIDEO_PAGE_RE.match(raw if raw.endswith("/") else raw + "/")
    if m:
        return m.group("slug").lower()
    parsed = urlparse(raw)
    if (parsed.netloc or "").lower().replace("www.", "") != SITE_HOST:
        return None
    m2 = _VIDEO_HREF_RE.search(parsed.path or "")
    return m2.group(1).lower() if m2 else None


def _canonical_video_url(slug: str) -> str:
    return f"https://{SITE_HOST}/videos/{slug.lower().strip('/')}/"


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    slug = _extract_slug(href)
    return _canonical_video_url(slug) if slug else None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "src"):
        v = img.get(key)
        if not v or str(v).startswith("data:"):
            continue
        url = str(v).strip()
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _streams_from_html(html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    hls_url: Optional[str] = None

    m = _HLS_URL_RE.search(html)
    if m:
        hls_url = m.group(1).strip()
        streams.append({"url": hls_url, "quality": "adaptive", "format": "hls"})

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

    for block in soup.select("div.video-img-box"):
        if len(items) >= limit:
            break
        link = block.select_one('.img-box a[href*="/videos/"]') or block.select_one(
            'a[href*="/videos/"]'
        )
        if not link:
            continue
        url = _normalize_video_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title_el = block.select_one("h6.title a") or block.select_one(".title a")
        img = block.select_one("img")
        title = _clean_title(
            _first_non_empty(
                title_el.get_text(" ", strip=True) if title_el else None,
                img.get("alt") if img and img.get("alt") else None,
            )
        ) or "Unknown Video"

        duration = None
        dur_el = block.select_one("span.label")
        if dur_el:
            duration = dur_el.get_text(strip=True) or None

        views = None
        sub = block.select_one("p.sub-title")
        if sub:
            views = _normalize_views(sub.get_text(" ", strip=True))

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": _best_image_url(img),
                "duration": duration,
                "views": views,
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

    m = _PATH_PAGE_SUFFIX_RE.match(path)
    if m:
        base_path = m.group(1) or "/"
    else:
        base_path = path

    if page_num <= 1:
        new_path = f"{base_path}/" if base_path != "/" else "/"
    else:
        new_path = f"{base_path}/{page_num}/"

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            new_path,
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
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    slug = _extract_slug(url) or ""
    page_url = _canonical_video_url(slug) if slug else url

    title = _clean_title(
        _first_non_empty(
            soup.select_one("section.video-info h4").get_text(" ", strip=True)
            if soup.select_one("section.video-info h4")
            else None,
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        (soup.select_one("video#player") or {}).get("poster")
        if soup.select_one("video#player")
        else None,
    )
    if thumbnail and str(thumbnail).startswith("//"):
        thumbnail = f"https:{thumbnail}"

    views = None
    info_h6 = soup.select_one("section.video-info h6")
    if info_h6:
        spans = info_h6.select("span.mr-3")
        for sp in spans:
            txt = sp.get_text(strip=True)
            if txt and re.search(r"\d", txt) and "小時" not in txt and "天" not in txt:
                views = _normalize_views(txt)
                break

    uploader = None
    model = soup.select_one("section.video-info .models a.model")
    if model:
        uploader = model.get("title") or model.get_text(strip=True) or None

    upload_date = None
    date_el = soup.select_one("section.video-info .header-right span.inactive-color")
    if date_el:
        txt = date_el.get_text(strip=True)
        m = re.search(r"\d{4}-\d{2}-\d{2}", txt)
        upload_date = m.group(0) if m else txt or None

    tags: list[str] = []
    for a in soup.select("section.video-info h5.tags a, section.video-info .tags a"):
        tag = a.get_text(strip=True)
        if tag and tag not in tags and tag != "•":
            tags.append(tag)

    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or _streams_from_html(html)
    if not video_data.get("streams"):
        video_data = {
            "streams": [],
            "hls": None,
            "default": page_url,
            "has_video": False,
        }

    return {
        "url": page_url,
        "title": title,
        "description": _meta(soup, name="description"),
        "thumbnail_url": thumbnail,
        "duration": None,
        "views": views,
        "uploader_name": uploader,
        "category": None,
        "tags": tags or None,
        "upload_date": upload_date,
        "video": {
            k: v
            for k, v in video_data.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    slug = _extract_slug(url)
    if not slug:
        raise ValueError(f"Unsupported Jable URL: {url}")

    page_url = _canonical_video_url(slug)
    raw = (url or "").strip().split("#", 1)[0].split("?", 1)[0]
    raw = raw if raw.endswith("/") else raw + "/"
    fetch_urls = [
        page_url,
        f"https://{SITE_HOST}/s0/videos/{slug}/",
        raw,
    ]
    deduped: list[str] = []
    seen_fetch: set[str] = set()
    for candidate in fetch_urls:
        if candidate and candidate not in seen_fetch:
            seen_fetch.add(candidate)
            deduped.append(candidate)
    fetch_urls = deduped

    html: str | None = None
    used_url = page_url
    for candidate in fetch_urls:
        try:
            html = await fetch_page(candidate, referer=BASE_SITE)
            used_url = candidate
            break
        except Exception:
            continue
    if html is None:
        raise ValueError(f"Failed to fetch Jable page for {slug}")

    video_data = _streams_from_html(html)
    return parse_video_page(html, used_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = (base_url or "").strip() or DEFAULT_BROWSE_URL
    page_url = _build_list_page_url(normalized_base, page)
    try:
        html = await fetch_page(page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
