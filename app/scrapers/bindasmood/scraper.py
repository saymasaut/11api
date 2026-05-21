from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://bindasmood.com/"
SITE_HOST = "bindasmood.com"
SITE_ALIASES = frozenset({"bindasmood.com", "www.bindasmood.com"})

_RESERVED_SLUGS = frozenset(
    {
        "categories",
        "tags",
        "actors",
        "category",
        "tag",
        "actor",
        "page",
        "contact",
        "login",
        "register",
        "sample-page",
        "wp-content",
        "wp-json",
        "wp-admin",
    }
)

_POST_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?bindasmood\.com/(?P<slug>[a-z0-9][a-z0-9-]*)/?$",
    re.IGNORECASE,
)
_MP4_RE = re.compile(
    r"https?://[^\s\"'<>]+\.mp4(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)
_M3U8_RE = re.compile(
    r"https?://[^\s\"'<>]+\.m3u8(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".bindasmood.com")


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
        "Accept-Language": "en-US,en;q=0.9",
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
        " - BindasMood.com",
        " | BindasMood.com",
        " - BindasMood",
        " | BindasMood",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    return t or None


def _normalize_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return digits or None


def _is_reserved_path(path: str) -> bool:
    parts = [p for p in (path or "").strip("/").split("/") if p]
    if not parts:
        return False
    if parts[0].lower() in _RESERVED_SLUGS:
        return True
    if len(parts) >= 2 and parts[0].lower() in ("category", "tag", "actor"):
        return True
    return False


def _normalize_post_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{BASE_SITE.rstrip('/')}{href}"
    if not href.startswith("http"):
        return None
    href = href.split("#", 1)[0]
    parsed = urlparse(href.split("?", 1)[0])
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != SITE_HOST:
        return None
    if _is_reserved_path(parsed.path or ""):
        return None
    if any(x in (parsed.path or "").lower() for x in ("/wp-content/", "/wp-json/")):
        return None
    m = _POST_PAGE_RE.match(href if href.endswith("/") else href + "/")
    if not m:
        return None
    slug = (m.group("slug") or "").lower()
    if slug in _RESERVED_SLUGS:
        return None
    return f"https://{SITE_HOST}/{slug}/"


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for key in ("data-src", "data-original", "data-lazy-src", "src", "srcset"):
        v = img.get(key)
        if not v or str(v).startswith("data:"):
            continue
        url = str(v).strip()
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        if url.startswith("//"):
            return f"https:{url}"
        return url
    return None


def _is_preview_url(url: str) -> bool:
    low = (url or "").lower()
    return "preview" in low or "_preview" in low or "/thumb" in low and ".mp4" in low


def _streams_from_html(html: str) -> dict[str, Any]:
    html_norm = html.replace("\\/", "/").replace("\\u0026", "&")
    streams: list[dict[str, str]] = []
    seen: set[str] = set()
    hls_url: Optional[str] = None

    for pat, fmt in ((_M3U8_RE, "hls"), (_MP4_RE, "mp4")):
        for raw in pat.findall(html_norm):
            url = raw.strip().rstrip("/")
            if not url.startswith("http") or url in seen or _is_preview_url(url):
                continue
            seen.add(url)
            entry = {"url": url, "quality": "adaptive", "format": fmt}
            streams.append(entry)
            if fmt == "hls" and not hls_url:
                hls_url = url

    default = hls_url or (streams[0]["url"] if streams else None)
    return {
        "streams": streams,
        "hls": hls_url,
        "default": default,
        "has_video": bool(streams),
    }


async def _streams_for_page(html: str, page_url: str) -> dict[str, Any]:
    video_data = _streams_from_html(html)
    if video_data.get("has_video"):
        return video_data

    soup = BeautifulSoup(html, "lxml")
    iframe = soup.select_one(
        'iframe[src*="clean-tube-player"], iframe[src*="player-x.php"], .video-player iframe'
    )
    if iframe and iframe.get("src"):
        src = str(iframe.get("src")).strip()
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = f"{BASE_SITE.rstrip('/')}{src}"
        if src.startswith("http"):
            try:
                player_html = await fetch_page(src, referer=page_url)
                video_data = _streams_from_html(player_html)
                if video_data.get("streams"):
                    return video_data
            except Exception:
                pass
            streams = [{"url": src, "quality": "player", "format": "embed"}]
            return {
                "streams": streams,
                "hls": None,
                "default": src,
                "has_video": True,
            }

    return video_data


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("article.thumb-block, .thumb-block.video-preview-item"):
        if len(items) >= limit:
            break
        link = block.select_one("a[href]")
        if not link:
            continue
        url = _normalize_post_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title = None
        title_el = block.select_one("span.title a")
        if title_el:
            title = title_el.get_text(" ", strip=True)
        if not title:
            for sel in (".entry-header a", "h2 a", "h3 a"):
                el = block.select_one(sel)
                if el:
                    title = el.get_text(" ", strip=True)
                    break
        img = block.select_one("img")
        dur_el = block.select_one("span.duration")
        views_el = block.select_one("span.views")

        items.append(
            {
                "url": url,
                "title": _clean_title(
                    _first_non_empty(title, link.get("title"), img.get("alt") if img else None)
                )
                or "Unknown Video",
                "thumbnail_url": _best_image_url(img),
                "duration": dur_el.get_text(strip=True) if dur_el else None,
                "views": _normalize_views(views_el.get_text() if views_el else None),
                "uploader_name": None,
                "tags": None,
            }
        )

    if len(items) < limit:
        for a in soup.select("a[href]"):
            if len(items) >= limit:
                break
            url = _normalize_post_href(a.get("href") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            img = a.find("img")
            items.append(
                {
                    "url": url,
                    "title": _clean_title(a.get_text(strip=True) or a.get("title")) or "Unknown Video",
                    "thumbnail_url": _best_image_url(img),
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

    if page_num <= 1:
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or SITE_HOST, parsed.path or "/", "", parsed.query, "")
        )

    path = (parsed.path or "/").rstrip("/") or ""
    if re.search(r"/page/\d+$", path, re.I):
        path = re.sub(r"/page/\d+$", "", path, flags=re.I) or ""

    if path and path != "/":
        new_path = f"{path}/page/{page_num}"
    else:
        new_path = f"/page/{page_num}"

    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or SITE_HOST,
            new_path,
            "",
            urlencode(q) if q else "",
            "",
        )
    )


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    canon = _normalize_post_href(url) or url

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _meta(soup, name="twitter:image"),
        _best_image_url(soup.select_one("article img, .post-thumbnail img, img")),
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    tags: list[str] = []
    for a in soup.select('a[rel="tag"], a[href*="/category/"], a[href*="/tag/"]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags and len(tag) < 50:
            tags.append(tag)

    related = _parse_list_items(soup, limit=30)
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
        "duration": None,
        "views": None,
        "uploader_name": None,
        "category": None,
        "tags": tags or None,
        "upload_date": None,
        "video": {
            k: v
            for k, v in video_data.items()
            if k in ("streams", "hls", "default", "has_video")
        },
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    canon = _normalize_post_href(url)
    if not canon:
        raise ValueError(f"Unsupported BindasMood URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    video_data = await _streams_for_page(html, canon)
    return parse_video_page(html, canon, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
