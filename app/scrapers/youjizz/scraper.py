from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

BASE_SITE = "https://www.youjizz.com/"
SITE_HOST = "youjizz.com"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_SITE,
    "Cookie": "age_verified=1",
}

_VIDEO_PAGE_RE = re.compile(
    r"youjizz\.com/videos/(?:[^/#?]*-)?(?P<id>\d+)\.html",
    re.IGNORECASE,
)
_EMBED_PAGE_RE = re.compile(
    r"youjizz\.com/videos/embed/(?P<id>\d+)",
    re.IGNORECASE,
)
_DATA_ENCODINGS_RE = re.compile(
    r"dataEncodings\s*=\s*(\[)",
    re.IGNORECASE,
)
_ENCODINGS_RE = re.compile(
    r"[Ee]ncodings\s*=\s*(\[.+?\]);\s*\n",
    re.DOTALL,
)
_LIST_HREF_RE = re.compile(
    r'href="(/videos/[^"]+\.html)"',
    re.IGNORECASE,
)
_PAGE_NUM_PATH_RE = re.compile(r"^(.*/)(\d+)(\.html?)$", re.IGNORECASE)
_PAGE_HYPHEN_NUM_RE = re.compile(r"^(.+-)(\d+)(\.html?)$", re.IGNORECASE)
_URL_PATTERN_RE = re.compile(
    r'id=["\']urlPattern["\']\s+value=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return "youjizz.com" in h


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _parse_balanced_json_array(html: str, start: int) -> list[Any] | None:
    if start >= len(html) or html[start] != "[":
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(html[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, list) else None
    return None


def _extract_encodings_json(html: str) -> list[dict[str, Any]]:
    m = _DATA_ENCODINGS_RE.search(html)
    if m:
        arr = _parse_balanced_json_array(html, m.start(1))
        if arr:
            return [x for x in arr if isinstance(x, dict)]

    m = _ENCODINGS_RE.search(html)
    if m:
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, list):
                return [x for x in parsed if isinstance(x, dict)]
        except json.JSONDecodeError:
            pass
    return []


async def fetch_page(url: str) -> str:
    headers = dict(_DEFAULT_HEADERS)
    headers["Referer"] = BASE_SITE
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(45.0, connect=30.0),
        headers=headers,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


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
    for suffix in (" - YouJizz", " | YouJizz", " - youjizz", " | youjizz"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_media_url(url: str) -> str:
    u = (url or "").strip().replace("\\/", "/")
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return urljoin(BASE_SITE, u)
    return u


def _normalize_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return digits or None


def _format_duration(seconds: int | None) -> Optional[str]:
    if not seconds or seconds <= 0:
        return None
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_duration_text(text: str | None) -> Optional[str]:
    if not text:
        return None
    raw = str(text).strip()
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", raw):
        return raw
    return None


def _extract_video_id(url: str) -> Optional[str]:
    raw = (url or "").strip()
    m = _VIDEO_PAGE_RE.search(raw) or _EMBED_PAGE_RE.search(raw)
    return m.group("id") if m else None


def _canonical_watch_url(html: str, url: str, video_id: str) -> str:
    soup = BeautifulSoup(html, "lxml") if html else None
    if soup:
        canon = _meta(soup, prop="og:url")
        if canon and _extract_video_id(canon):
            return canon
    if _VIDEO_PAGE_RE.search(url):
        return url.split("#", 1)[0].split("?", 1)[0]
    return f"https://www.youjizz.com/videos/embed/{video_id}"


def _quality_label(encoding: dict[str, Any], url: str) -> str:
    name = encoding.get("name") or encoding.get("quality")
    if name:
        s = str(name).strip()
        if s.isdigit():
            return f"{s}p"
        if s.lower().endswith("p"):
            return s.lower()
        return s
    qm = re.search(r"(\d{3,4})p", url, re.I)
    return f"{qm.group(1)}p" if qm else "default"


def _streams_from_html(html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen: set[str] = set()

    for enc in _extract_encodings_json(html):
        filename = enc.get("filename")
        if not filename:
            continue
        media_url = _normalize_media_url(str(filename))
        if media_url in seen:
            continue
        seen.add(media_url)
        fmt = "hls" if ".m3u8" in media_url.lower() else "mp4"
        streams.append(
            {
                "url": media_url,
                "quality": _quality_label(enc, media_url),
                "format": fmt,
            }
        )

    if not streams:
        soup = BeautifulSoup(html, "lxml")
        for source in soup.select("video source[src]"):
            src = _normalize_media_url(source.get("src") or "")
            if not src or src in seen:
                continue
            seen.add(src)
            streams.append(
                {
                    "url": src,
                    "quality": _quality_label({}, src),
                    "format": "hls" if ".m3u8" in src.lower() else "mp4",
                }
            )

    def _score(s: dict[str, str]) -> int:
        q = s.get("quality", "")
        digits = "".join(ch for ch in q if ch.isdigit())
        return int(digits) if digits else 0

    streams.sort(key=_score, reverse=True)
    default = streams[0]["url"] if streams else None
    hls = next((s["url"] for s in streams if s.get("format") == "hls"), None)
    return {
        "streams": streams,
        "hls": hls,
        "default": default or hls,
        "has_video": bool(streams),
    }


def _parse_thumb_block(block: Any) -> Optional[dict[str, Any]]:
    video_id = (block.get("data-videoid") or block.get("data-videoId") or "").strip()
    link = block.select_one("a.frame.video[href], .video-title a[href]")
    if not link:
        return None

    href = link.get("href") or ""
    if not href.startswith("http"):
        href = urljoin(BASE_SITE, href)

    vid = video_id or _extract_video_id(href)
    if not vid:
        return None

    title_el = block.select_one(".video-title a")
    img = block.select_one("img[data-original], img[data-src], img")
    dur_el = block.select_one(".time")
    views_el = block.select_one(".format-views")

    title = _clean_title(
        _first_non_empty(
            title_el.get_text(" ", strip=True) if title_el else None,
            link.get("title"),
            img.get("alt") if img and img.get("alt") else None,
        )
    ) or f"Video {vid}"

    thumb = None
    if img:
        thumb = img.get("data-original") or img.get("data-src") or img.get("src")
        if thumb:
            thumb = _normalize_media_url(thumb)

    return {
        "url": href,
        "title": title,
        "thumbnail_url": thumb,
        "duration": _parse_duration_text(dur_el.get_text(strip=True) if dur_el else None),
        "views": _normalize_views(views_el.get_text(strip=True) if views_el else None),
        "uploader_name": None,
        "tags": None,
    }


def _parse_list_items(soup: BeautifulSoup, html: str, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("div.video-thumb[data-videoId], div.video-thumb[data-videoid]"):
        if len(items) >= limit:
            break
        parsed = _parse_thumb_block(block)
        if not parsed or parsed["url"] in seen:
            continue
        seen.add(parsed["url"])
        items.append(parsed)

    if len(items) < limit:
        for href in _LIST_HREF_RE.findall(html):
            if len(items) >= limit:
                break
            full = urljoin(BASE_SITE, href)
            if full in seen or not _extract_video_id(full):
                continue
            seen.add(full)
            items.append(
                {
                    "url": full,
                    "title": "Unknown Video",
                    "thumbnail_url": None,
                    "duration": None,
                    "views": None,
                    "uploader_name": None,
                    "tags": None,
                }
            )

    return items[:limit]


def _page_url_from_pattern(pattern: str, page_num: int) -> Optional[str]:
    """Convert site urlPattern like /categories/milf-(:num).html to a path."""
    if "(:num)" not in pattern:
        return None
    return "/" + pattern.replace("(:num)", str(page_num)).lstrip("/")


def _build_list_page_url(base_url: str, page: int, *, html: str | None = None) -> str:
    raw = (base_url or "").strip() or BASE_SITE
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))

    page_num = max(1, int(page) if page else 1)
    parsed = urlparse(raw)
    path = parsed.path or "/"

    # Random is a single feed without numbered pages.
    if path.rstrip("/") == "/random":
        return urlunparse(
            (parsed.scheme or "https", parsed.netloc or f"www.{SITE_HOST}", "/random", "", "", "")
        )

    if html and page_num > 1:
        pm = _URL_PATTERN_RE.search(html)
        if pm:
            built = _page_url_from_pattern(pm.group(1), page_num)
            if built:
                return urlunparse(
                    (
                        parsed.scheme or "https",
                        parsed.netloc or f"www.{SITE_HOST}",
                        built,
                        "",
                        parsed.query,
                        "",
                    )
                )

    # /most-popular/2.html, /top-rated-week/2.html
    m = _PAGE_NUM_PATH_RE.match(path)
    if m:
        new_path = f"{m.group(1)}{page_num}{m.group(3)}"
    else:
        # /categories/milf-2.html, /search/teen-2.html
        m_hy = _PAGE_HYPHEN_NUM_RE.match(path)
        if m_hy:
            new_path = f"{m_hy.group(1)}{page_num}{m_hy.group(3)}"
        elif path in ("", "/"):
            new_path = f"/most-popular/{page_num}.html"
        elif path.endswith(".html"):
            base_path = re.sub(r"/\d+\.html$", "", path.rstrip("/"))
            new_path = f"{base_path}/{page_num}.html"
        else:
            base_path = path.rstrip("/")
            new_path = f"{base_path}/{page_num}.html"

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or f"www.{SITE_HOST}",
            new_path,
            "",
            parsed.query,
            "",
        )
    )


def parse_video_page(html: str, url: str, *, video: dict[str, Any] | None = None) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_id = _extract_video_id(url) or ""
    page_url = _canonical_watch_url(html, url, video_id) if video_id else url

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _meta(soup, prop="og:image")
    if thumbnail:
        thumbnail = _normalize_media_url(thumbnail)

    duration = None
    dur_meta = _meta(soup, prop="og:video:duration")
    if dur_meta and str(dur_meta).isdigit():
        duration = _format_duration(int(dur_meta))
    if not duration:
        runtime_el = soup.select_one(
            '[data-i18n="video.videotime.runtime"] + span, .video-runtime span, .runtime span'
        )
        if runtime_el:
            duration = _parse_duration_text(runtime_el.get_text(strip=True))

    views = None
    vm = re.search(r"([\d,.]+)\s+views", html, re.I)
    if vm:
        views = _normalize_views(vm.group(1))

    uploader = None
    um = re.search(
        r"Uploaded\s+By:.*?<a[^>]*>([^<]+)</a>",
        html,
        re.I | re.DOTALL,
    )
    if um:
        uploader = um.group(1).strip()

    tags: list[str] = []
    kw = _meta(soup, name="keywords")
    if kw:
        tags.extend([t.strip() for t in kw.split(",") if t.strip()])

    video_data = video or _streams_from_html(html)
    related = _parse_list_items(soup, html, limit=24)
    related = [r for r in related if r.get("url") != page_url]

    return {
        "url": page_url,
        "title": title,
        "description": _meta(soup, prop="og:description") or _meta(soup, name="description"),
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
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
    video_id = _extract_video_id(url)
    if not video_id:
        raise ValueError(f"Unsupported YouJizz URL: {url}")

    fetch_url = url if _VIDEO_PAGE_RE.search(url) else f"https://www.youjizz.com/videos/embed/{video_id}"
    html = await fetch_page(fetch_url)
    video_data = _streams_from_html(html)
    return parse_video_page(html, fetch_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_num = max(1, int(page) if page else 1)
    first_url = _build_list_page_url(base_url, 1)
    try:
        first_html = await fetch_page(first_url)
    except Exception:
        return []

    page_url = (
        first_url
        if page_num <= 1
        else _build_list_page_url(base_url, page_num, html=first_html)
    )
    try:
        html = first_html if page_url == first_url else await fetch_page(page_url)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, html, limit=limit)
