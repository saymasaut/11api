from __future__ import annotations

import html as html_lib
import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://desithothub.com/"
SITE_HOST = "desithothub.com"
SITE_ALIASES = frozenset({"desithothub.com", "www.desithothub.com"})

_RESERVED_SLUGS = frozenset(
    {
        "categories",
        "popular",
        "newest",
        "tags",
        "favourites",
        "page",
        "dmca",
        "contact",
        "privacy-policy",
        "terms-of-service",
        "report-content",
        "faq",
        "18-usc-2257",
        "login",
        "register",
        "wp-content",
        "wp-admin",
    }
)

_EMBED_HOST_MARKERS = (
    "sendvid.com",
    "streamtape.com",
    "lulustream.com",
    "luluvid.com",
    "vidara.to",
    "vikingfile.com",
    "vinovo.to",
    "gofile.io",
    "upfiles.com",
)

_POST_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?desithothub\.com/(?P<slug>[a-z0-9][a-z0-9-]*)/?$",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".desithothub.com")


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
        " - DesiThotHub - Desi Nude Hub",
        " | DesiThotHub - Desi Nude Hub",
        " - DesiThotHub",
        " | DesiThotHub",
        " - desithothub.com",
        " | desithothub.com",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
        elif t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    raw = str(text).strip().replace(",", "")
    if raw.isdigit():
        return raw
    digits = re.sub(r"[^\d]", "", raw)
    return digits or None


def _is_reserved_path(path: str) -> bool:
    parts = [p for p in (path or "").strip("/").split("/") if p]
    if not parts:
        return False
    if len(parts) == 1 and parts[0].lower() in _RESERVED_SLUGS:
        return True
    if parts[0].lower() in _RESERVED_SLUGS:
        return True
    if len(parts) >= 2 and parts[0].lower() in ("category", "categories", "tag", "tags"):
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
    href = href.split("#", 1)[0].split("?", 1)[0]
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower().replace("www.", "")
    if host != SITE_HOST:
        return None
    if _is_reserved_path(parsed.path or ""):
        return None
    if any(x in (parsed.path or "").lower() for x in ("/wp-content/", "/wp-admin/")):
        return None
    parts = [p for p in (parsed.path or "").strip("/").split("/") if p]
    if len(parts) != 1:
        return None
    canon = href if href.endswith("/") else href + "/"
    m = _POST_PAGE_RE.match(canon)
    if not m:
        return None
    slug = (m.group("slug") or "").lower()
    if slug in _RESERVED_SLUGS:
        return None
    return canon


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


def _decode_media_url(url: str) -> str:
    return html_lib.unescape((url or "").strip())


def _quality_from_label(label: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    return clean or "embed"


def _normalize_embed_url(url: str) -> Optional[str]:
    raw = _decode_media_url(url)
    if not raw.startswith("http"):
        if raw.startswith("//"):
            raw = f"https:{raw}"
        else:
            return None
    return raw


def _to_embed_url(url: str) -> Optional[str]:
    """Convert provider watch/share URLs to embed-friendly URLs."""
    raw = _normalize_embed_url(url)
    if not raw:
        return None
    low = raw.lower()
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower().replace("www.", "")
    path = parsed.path or ""

    if "sendvid.com" in host:
        if "/embed/" in low:
            return raw
        parts = [p for p in path.strip("/").split("/") if p]
        if parts:
            return f"https://sendvid.com/embed/{parts[-1]}"

    if "streamtape.com" in host:
        if "/e/" in low:
            return re.sub(r"\.mp4(?:\?.*)?$", "", raw, flags=re.I)
        m = re.search(r"/v/([^/]+)", path, re.I)
        if m:
            return f"https://streamtape.com/e/{m.group(1)}/"

    if "lulustream.com" in host or "luluvid.com" in host:
        if "/e/" in low or "/embed/" in low:
            return raw
        parts = [p for p in path.strip("/").split("/") if p]
        if parts:
            return f"https://{host}/e/{parts[-1]}"

    if "vinovo.to" in host:
        if "/embed" in low or "/e/" in low:
            return raw
        m = re.search(r"/d/([^/]+)", path, re.I)
        if m:
            return f"https://vinovo.to/embed/{m.group(1)}"

    if "vidara.to" in host:
        if "/e/" in low or "/embed/" in low:
            return raw
        m = re.search(r"/v/([^/]+)", path, re.I)
        if m:
            return f"https://vidara.to/e/{m.group(1)}"

    # GoFile, VikingFile, Upfiles: no reliable public embed path — use page URL in WebView.
    return raw


def _embed_streams_from_page(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_lib.unescape(html), "lxml")
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    server_buttons = soup.select("button.srv-drop-item")
    video_units = soup.select("div.video-unit")

    for idx, btn in enumerate(server_buttons):
        label = btn.get_text(strip=True) or f"Server {idx + 1}"
        unit = video_units[idx] if idx < len(video_units) else None
        embed_url: Optional[str] = None

        if unit:
            iframe = unit.select_one("iframe.vid-max-iframe[src], iframe[src]")
            if iframe and iframe.get("src"):
                embed_url = _to_embed_url(str(iframe.get("src")))
            else:
                link = unit.select_one("a.vid-maxwrap[href]")
                if link and link.get("href"):
                    embed_url = _to_embed_url(str(link.get("href")))

        if not embed_url:
            continue
        if embed_url in seen:
            continue
        seen.add(embed_url)
        streams.append({"url": embed_url, "quality": _quality_from_label(label), "format": "embed"})

    if not streams:
        for iframe in soup.select("iframe.vid-max-iframe[src], .video-player iframe[src]"):
            src = _normalize_embed_url(str(iframe.get("src") or ""))
            if not src or src in seen:
                continue
            if not any(m in src.lower() for m in _EMBED_HOST_MARKERS):
                continue
            embed_url = _to_embed_url(src) or src
            seen.add(embed_url)
            streams.append({"url": embed_url, "quality": "sendvid", "format": "embed"})

    if not streams:
        for prop in ("og:video:url", "og:video", "og:video:secure_url"):
            meta_url = _normalize_embed_url(_meta(soup, prop=prop) or "")
            if not meta_url or meta_url in seen:
                continue
            if not any(m in meta_url.lower() for m in _EMBED_HOST_MARKERS):
                continue
            embed_url = _to_embed_url(meta_url) or meta_url
            seen.add(embed_url)
            streams.append(
                {"url": embed_url, "quality": _quality_from_label("Sendvid"), "format": "embed"},
            )

    default = None
    for pref in ("sendvid", "streamtape", "lulustream", "luluvid", "vinovo", "vidara", "gofile"):
        match = next((s for s in streams if pref in (s.get("quality") or "").lower()), None)
        if match:
            default = match.get("url")
            break
    if not default and streams:
        default = streams[0]["url"]

    return {
        "streams": streams,
        "hls": None,
        "default": default,
        "has_video": bool(streams),
    }


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("div.thumb"):
        if len(items) >= limit:
            break
        link = block.select_one("a.card[href], a[href].card")
        if not link:
            continue
        url = _normalize_post_href(link.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)

        title_el = block.select_one("h2.card-title, .card-title")
        img = block.select_one("img")
        time_el = block.select_one("span.time-ago, .time-ago")
        views_el = block.select_one("span.views, .views")

        items.append(
            {
                "url": url,
                "title": _clean_title(
                    _first_non_empty(
                        title_el.get_text(" ", strip=True) if title_el else None,
                        title_el.get("title") if title_el else None,
                        link.get("title"),
                        img.get("alt") if img else None,
                    )
                )
                or "Unknown Video",
                "thumbnail_url": _best_image_url(img),
                "duration": None,
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
            soup.select_one("h2").get_text(" ", strip=True) if soup.select_one("h2") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _meta(soup, name="twitter:image"),
        _best_image_url(soup.select_one("video, img.wp-post-image, img")),
    )
    if thumbnail and str(thumbnail).startswith("//"):
        thumbnail = f"https:{thumbnail}"

    duration = None
    dur_el = soup.select_one("span.duration, .duration, time")
    if dur_el:
        duration = dur_el.get_text(strip=True) or None
    if not duration:
        m = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b", soup.get_text(" ", strip=True))
        if m:
            duration = m.group(1)

    views = None
    views_el = soup.select_one("span.views, .views")
    if views_el:
        views = _normalize_views(views_el.get_text())

    tags: list[str] = []
    for a in soup.select('a[rel="tag"], a[href*="/categories/"], a[href*="/tags/"]'):
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
        "duration": duration,
        "views": views,
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
        raise ValueError(f"Unsupported DesiThotHub URL: {url}")

    html = await fetch_page(canon, referer=BASE_SITE)
    video_data = _embed_streams_from_page(html)
    return parse_video_page(html, canon, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
