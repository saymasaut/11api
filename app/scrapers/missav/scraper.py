from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://missav.ai/"
SITE_HOST = "missav.ai"
DEFAULT_BROWSE_URL = "https://missav.ai/dm265/en"
SITE_ALIASES = frozenset({"missav.ai", "www.missav.ai", "missav.ws", "www.missav.ws"})

_LOCALES = frozenset(
    {
        "en",
        "cn",
        "ja",
        "ko",
        "ms",
        "th",
        "de",
        "fr",
        "vi",
        "id",
        "fil",
        "pt",
        "zh",
    }
)
_RESERVED_SLUGS = frozenset(
    {
        "new",
        "release",
        "dm",
        "site",
        "api",
        "login",
        "register",
        "genres",
        "makers",
        "actresses",
        "saved",
        "search",
        "vip",
        "contact",
        "terms",
        "upload",
    }
)

_DM_PREFIX_RE = re.compile(r"^dm\d+$", re.IGNORECASE)
_VIDEO_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?missav\.(?:ai|ws)/(?:(?P<dm>dm\d+)/)?(?:(?P<locale>[a-z]{2}(?:-[a-z]+)?)/)?(?P<dvd>[a-z0-9][a-z0-9-]*)/?$",
    re.IGNORECASE,
)
_VIDEO_HREF_RE = _VIDEO_PAGE_RE
_EVAL_BLOCK_RE = re.compile(
    r"return p\}\('(.+?)',(\d+),(\d+),'([^']+)'\.split\('\|'\)",
    re.DOTALL,
)
_TEMPLATE_RE = re.compile(r"([a-z])='([^']+)';")
_M3U8_RE = re.compile(r"https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*", re.IGNORECASE)
_DVD_ID_RE = re.compile(r"dvdId:\s*'([^']+)'", re.IGNORECASE)


def _normalize_host(host: str) -> str:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h


def _is_supported_host(host: str) -> bool:
    h = _normalize_host(host)
    return h in {"missav.ai", "missav.ws"}


def can_handle(host: str) -> bool:
    h = _normalize_host(host)
    return h in SITE_ALIASES or h.endswith(".missav.ai") or h.endswith(".missav.ws")


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
    for suffix in (" - MissAV", " | MissAV", " :: MissAV"):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    return t or None


def _format_duration_seconds(raw: str | None) -> Optional[str]:
    if not raw:
        return None
    txt = str(raw).strip()
    if txt.isdigit():
        total = int(txt)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", txt)
    return m.group(0) if m else None


def _is_video_slug(slug: str) -> bool:
    s = (slug or "").lower().strip("/")
    if not s or s in _RESERVED_SLUGS:
        return False
    if _DM_PREFIX_RE.match(s):
        return False
    if "/" in s:
        return False
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", s):
        return False
    # Browse slugs are short (fc2, siro); videos use hyphenated codes (fc2-ppv-1144330).
    return "-" in s


def _parse_video_path_parts(parts: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Return (locale, dvd_id) from URL path segments."""
    if not parts:
        return None, None
    i = 0
    if _DM_PREFIX_RE.match(parts[0]):
        i = 1
    if i >= len(parts):
        return None, None
    locale: Optional[str] = None
    if parts[i].lower() in _LOCALES:
        locale = parts[i].lower().split("-")[0]
        i += 1
    if i >= len(parts):
        return locale, None
    dvd = parts[i].lower()
    if len(parts) > i + 1:
        return locale, None
    if not _is_video_slug(dvd):
        return locale, None
    return locale or "en", dvd


def _extract_dvd_id(url: str) -> Optional[str]:
    raw = (url or "").strip().split("#", 1)[0].split("?", 1)[0]
    m = _VIDEO_PAGE_RE.match(raw if raw.endswith("/") else raw + "/")
    if m:
        dvd = (m.group("dvd") or "").lower()
        locale = (m.group("locale") or "").lower()
        if locale and locale.split("-")[0] not in _LOCALES and locale not in _LOCALES:
            if _is_video_slug(locale):
                return locale
            return None
        return dvd if _is_video_slug(dvd) else None
    parsed = urlparse(raw)
    if not _is_supported_host(parsed.netloc):
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    _, dvd = _parse_video_path_parts(parts)
    return dvd


def _locale_from_url(url: str) -> str:
    parsed = urlparse((url or "").strip().split("?", 1)[0])
    parts = [p for p in (parsed.path or "").split("/") if p]
    locale, _ = _parse_video_path_parts(parts)
    return locale or "en"


def _canonical_video_url(dvd_id: str, *, locale: str = "en") -> str:
    dvd = (dvd_id or "").lower().strip("/")
    loc = (locale or "en").lower().split("-")[0]
    if loc not in _LOCALES:
        loc = "en"
    return f"https://{SITE_HOST}/{loc}/{dvd}"


def _normalize_browse_url(url: str) -> str:
    """Insert locale into dm-prefixed browse paths (dm539/new -> dm539/en/new)."""
    raw = (url or "").strip()
    if not raw:
        return DEFAULT_BROWSE_URL
    if not raw.startswith("http"):
        raw = f"{BASE_SITE.rstrip('/')}/{raw.lstrip('/')}"
    parsed = urlparse(raw)
    if not _is_supported_host(parsed.netloc):
        return raw
    parts = [p for p in (parsed.path or "").split("/") if p]
    if not parts:
        return DEFAULT_BROWSE_URL
    if _DM_PREFIX_RE.match(parts[0]) and len(parts) == 1:
        return DEFAULT_BROWSE_URL
    if _DM_PREFIX_RE.match(parts[0]):
        i = 1
        if i < len(parts) and parts[i].lower().split("-")[0] not in _LOCALES:
            parts.insert(i, "en")
    netloc = SITE_HOST if _is_supported_host(parsed.netloc) else parsed.netloc
    path = "/" + "/".join(parts)
    return urlunparse((parsed.scheme or "https", netloc, path, "", parsed.query, ""))


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href or href == "#":
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"{BASE_SITE.rstrip('/')}{href}"
    m = _VIDEO_HREF_RE.match(href if href.endswith("/") else href + "/")
    if m:
        dvd = (m.group("dvd") or "").lower()
        if not _is_video_slug(dvd):
            return None
        loc = (m.group("locale") or "en").lower().split("-")[0]
        return _canonical_video_url(dvd, locale=loc or "en")
    parsed = urlparse(href)
    if not _is_supported_host(parsed.netloc):
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    locale, dvd = _parse_video_path_parts(parts)
    if not dvd:
        return None
    return _canonical_video_url(dvd, locale=locale or "en")


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


def _decode_template(tmpl: str, parts: list[str], *, dvd_id: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(tmpl):
        ch = tmpl[i]
        if ch.isdigit():
            j = i
            while j < len(tmpl) and tmpl[j].isdigit():
                j += 1
            idx = int(tmpl[i:j])
            if 0 <= idx < len(parts):
                out.append(parts[idx])
            i = j
        elif ch == "d":
            out.append(dvd_id)
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _streams_from_player(html: str, *, dvd_id: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    hls_url: Optional[str] = None

    m = _EVAL_BLOCK_RE.search(html)
    if m:
        blob = m.group(1).replace("\\'", "'").replace("\'", "'")
        parts = m.group(4).split("|")
        decoded: dict[str, str] = {}
        for key, tmpl in _TEMPLATE_RE.findall(blob):
            decoded[key] = _decode_template(tmpl, parts, dvd_id=dvd_id)
        master = decoded.get("e") or decoded.get("b") or decoded.get("c")
        if master and master.startswith("http"):
            hls_url = master
            streams.append({"url": master, "quality": "adaptive", "format": "hls"})
        for key, url in decoded.items():
            if key == "e" or not url.startswith("http") or ".m3u8" not in url:
                continue
            if url not in {s["url"] for s in streams}:
                streams.append({"url": url, "quality": key, "format": "hls"})

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


def _thumbnail_video_href(block: Any) -> Optional[str]:
    for link in block.select("a[href]"):
        url = _normalize_video_href(link.get("href") or "")
        if url:
            return url
    return None


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("div.thumbnail"):
        if len(items) >= limit:
            break
        url = _thumbnail_video_href(block)
        if not url:
            continue
        if not url or url in seen:
            continue
        seen.add(url)

        title_el = block.select_one(".text-sm a") or block.select_one("a[alt]")
        alt_link = block.select_one("a[alt]")
        img = block.select_one("img[data-src], img[alt]")
        title = _clean_title(
            _first_non_empty(
                title_el.get_text(" ", strip=True) if title_el else None,
                str(alt_link.get("alt") or "").strip() if alt_link else None,
                img.get("alt") if img else None,
            )
        ) or "Unknown Video"

        duration = None
        dur_el = block.select_one("span.absolute.bottom-1.right-1")
        if dur_el:
            duration = _format_duration_seconds(dur_el.get_text(strip=True))

        thumb = _best_image_url(img)
        dvd = _extract_dvd_id(url) or ""
        if not thumb and dvd:
            thumb = f"https://fourhoi.com/{dvd}/cover-t.jpg"

        items.append(
            {
                "url": url,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": None,
                "uploader_name": None,
                "tags": None,
            }
        )

    if len(items) < limit:
        for a in soup.select('a[href*="missav.ai/"], a[href^="/en/"], a[href^="/cn/"]'):
            if len(items) >= limit:
                break
            url = _normalize_video_href(a.get("href") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            title = _clean_title(a.get_text(strip=True) or str(a.get("alt") or "")) or "Unknown Video"
            items.append(
                {
                    "url": url,
                    "title": title,
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "tags": None,
                }
            )

    return items[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = _normalize_browse_url(base_url or DEFAULT_BROWSE_URL)
    if not raw.startswith("http"):
        raw = f"{BASE_SITE.rstrip('/')}/{raw.lstrip('/')}"
    parsed = urlparse(raw)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    page_num = max(1, int(page) if page else 1)
    if page_num <= 1:
        q.pop("page", None)
    else:
        q["page"] = str(page_num)
    query = urlencode(q) if q else ""
    netloc = parsed.netloc or SITE_HOST
    if _is_supported_host(netloc):
        # Canonicalize to .ai so ws/ai inputs share the same stable upstream.
        netloc = SITE_HOST

    return urlunparse(
        (
            parsed.scheme or "https",
            netloc,
            parsed.path or "/",
            "",
            query,
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
    dvd_id = _extract_dvd_id(url) or ""
    page_url = _canonical_video_url(dvd_id) if dvd_id else url

    title = _clean_title(
        _first_non_empty(
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _best_image_url(soup.select_one("img")),
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"
    if not thumbnail and dvd_id:
        thumbnail = f"https://fourhoi.com/{dvd_id}/cover-n.jpg"

    duration = _format_duration_seconds(_meta(soup, prop="og:video:duration"))
    upload_date = _meta(soup, prop="og:video:release_date")

    tags: list[str] = []
    for a in soup.select('a[href*="/genres/"], a[href*="/actresses/"], a[href*="/makers/"]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags and len(tag) < 60:
            tags.append(tag)

    uploader = None
    actress_links = soup.select('a[href*="/actresses/"]')
    if actress_links:
        uploader = actress_links[0].get_text(strip=True) or None

    related = _parse_list_items(soup, limit=40)
    related = [r for r in related if r.get("url") != page_url]

    video_data = video or _streams_from_player(html, dvd_id=dvd_id)
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
        "description": _meta(soup, prop="og:description"),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": None,
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
    dvd_id = _extract_dvd_id(url)
    if not dvd_id:
        raise ValueError(f"Unsupported MissAV URL: {url}")

    locale = _locale_from_url(url)
    fetch_urls = [
        _canonical_video_url(dvd_id, locale=locale),
        (url or "").strip().split("#", 1)[0].split("?", 1)[0],
        f"https://{SITE_HOST}/{dvd_id}",
    ]
    seen_fetch: set[str] = set()
    html: str | None = None
    page_url = fetch_urls[0]
    for candidate in fetch_urls:
        if not candidate or candidate in seen_fetch:
            continue
        seen_fetch.add(candidate)
        try:
            html = await fetch_page(candidate, referer=BASE_SITE)
            page_url = candidate
            break
        except Exception:
            continue
    if html is None:
        raise ValueError(f"Failed to fetch MissAV page for {dvd_id}")

    video_data = _streams_from_player(html, dvd_id=dvd_id)
    return parse_video_page(html, page_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    normalized_base = _normalize_browse_url(base_url or DEFAULT_BROWSE_URL)
    page_url = _build_list_page_url(normalized_base, page)
    try:
        html = await fetch_page(page_url, referer=normalized_base or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
