from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Optional
from urllib.parse import unquote, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://www.94mt.cc/"
SITE_HOST = "94mt.cc"
SITE_ALIASES = frozenset({"94mt.cc", "www.94mt.cc", "tianmei.one", "www.tianmei.one"})

_PLAY_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?(?:94mt\.cc|tianmei\.one)/index\.php/vod/play/id/(?P<vid>\d+)/",
    re.IGNORECASE,
)
_DETAIL_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?(?:94mt\.cc|tianmei\.one)/index\.php/vod/detail/id/(?P<vid>\d+)\.html",
    re.IGNORECASE,
)
_PLAY_HREF_RE = re.compile(r"/index\.php/vod/play/id/(?P<vid>\d+)/", re.IGNORECASE)
_TYPE_PAGE_RE = re.compile(
    r"/index\.php/vod/type/id/(?P<type_id>\d+)(?:/page/(?P<page>\d+))?\.html",
    re.IGNORECASE,
)
_PLAYER_JSON_RE = re.compile(r"player_aaaa\s*=\s*(\{)", re.IGNORECASE)
_M3U8_RE = re.compile(r"https?://[^\s\"'\\]+\.m3u8[^\s\"'\\]*", re.IGNORECASE)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in SITE_ALIASES or h.endswith(".94mt.cc")


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
        "Accept-Language": "zh-CN,zh;q=0.9,en-US,en;q=0.8",
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


def _host_matches(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host in SITE_ALIASES or host.endswith(".94mt.cc")


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    for suffix in (
        " - 天美影院免费在线观看",
        " - 天美影院",
        " | 天美影院",
        " - 天美传媒在线观看",
    ):
        if suffix in t:
            t = t.split(suffix, 1)[0].strip()
    if t.startswith("在线播放 - "):
        t = t[len("在线播放 - ") :].strip()
    return t or None


def _extract_video_id(url: str) -> Optional[str]:
    raw = (url or "").strip()
    for pattern in (_PLAY_PAGE_RE, _DETAIL_PAGE_RE):
        m = pattern.match(raw)
        if m:
            return m.group("vid")
    parsed = urlparse(raw)
    if not _host_matches(raw):
        return None
    m = _PLAY_HREF_RE.search(parsed.path or "")
    return m.group("vid") if m else None


def _canonical_play_url(video_id: str) -> str:
    return f"https://www.{SITE_HOST}/index.php/vod/play/id/{video_id}/sid/1/nid/1.html"


def _normalize_play_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("/"):
        href = urljoin(BASE_SITE, href)
    m = _PLAY_HREF_RE.search(href)
    if not m:
        return None
    return _canonical_play_url(m.group("vid"))


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
        if url.startswith("/"):
            return urljoin(BASE_SITE, url)
        return url
    return None


def _extract_player_json(html: str) -> Optional[dict[str, Any]]:
    m = _PLAYER_JSON_RE.search(html)
    if not m:
        return None
    start = m.start(1) - 1
    depth = 0
    for i in range(start, len(html)):
        ch = html[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _decode_encrypt2_url(encoded: str) -> Optional[str]:
    if not encoded:
        return None
    try:
        pad = "=" * ((4 - len(encoded) % 4) % 4)
        raw = base64.b64decode(encoded + pad).decode("utf-8", errors="ignore")
        return unquote(raw).strip()
    except Exception:
        return None


def _resolve_stream_url(url_field: str, encrypt_val: Optional[str]) -> Optional[str]:
    if not url_field:
        return None
    enc = str(encrypt_val) if encrypt_val is not None else "0"
    if enc == "2":
        return _decode_encrypt2_url(str(url_field))
    return str(url_field).strip().replace("\\/", "/")


def _streams_from_player(html: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    hls_url: Optional[str] = None
    vod_name: Optional[str] = None
    data = _extract_player_json(html)
    url_field: Optional[str] = None
    encrypt_val: Optional[str] = None
    if data:
        url_field = data.get("url")
        encrypt_val = str(data.get("encrypt")) if data.get("encrypt") is not None else None
        vod = data.get("vod_data") or {}
        if isinstance(vod, dict):
            vod_name = vod.get("vod_name")
    else:
        chunk = html[html.find("player_aaaa") : html.find("player_aaaa") + 12000] if "player_aaaa" in html else ""
        enc_m = re.search(r'"encrypt"\s*:\s*(\d+)', chunk)
        url_m = re.search(r'"url"\s*:\s*"([^"]*)"', chunk)
        if enc_m:
            encrypt_val = enc_m.group(1)
        if url_m:
            url_field = url_m.group(1)
        name_m = re.search(r'"vod_name"\s*:\s*"([^"]*)"', chunk)
        if name_m:
            vod_name = name_m.group(1).encode().decode("unicode_escape", errors="ignore")

    decoded = _resolve_stream_url(str(url_field or ""), encrypt_val)
    if decoded and decoded.startswith("http"):
        if ".m3u8" in decoded:
            hls_url = decoded
            streams.append({"url": decoded, "quality": "adaptive", "format": "hls"})
        elif decoded.endswith(".mp4"):
            streams.append({"url": decoded, "quality": "adaptive", "format": "mp4"})

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
        "vod_name": vod_name,
    }


def _parse_list_items(soup: BeautifulSoup, *, limit: int) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}

    def add_item(*, url: str, title: str, thumb: Optional[str], upload_date: Optional[str]) -> None:
        vid = _extract_video_id(url) or ""
        if len(by_id) >= limit or not url or not vid or vid in by_id:
            return
        prev = by_id.get(vid)
        cleaned = _clean_title(title) or "Unknown Video"
        if prev:
            if len(cleaned) > len(prev.get("title") or ""):
                prev["title"] = cleaned
            if not prev.get("thumbnail_url") and thumb:
                prev["thumbnail_url"] = thumb
            if not prev.get("upload_date") and upload_date:
                prev["upload_date"] = upload_date
            return
        by_id[vid] = {
            "url": url,
            "title": cleaned,
            "thumbnail_url": thumb,
            "duration": None,
            "views": None,
            "uploader_name": None,
            "tags": None,
            "upload_date": upload_date,
        }

    for block in soup.select("div.box-item"):
        if len(by_id) >= limit:
            break
        link = block.select_one('a.item-link[href*="vod/play"], a.movie-name[href*="vod/play"]')
        title_el = block.select_one("a.movie-name")
        img = block.select_one("img")
        upload_date = None
        date_el = block.select_one("em span")
        if date_el:
            upload_date = date_el.get_text(strip=True) or None
        url = _normalize_play_href((link or title_el or {}).get("href") or "")
        if not url:
            continue
        title = _first_non_empty(
            title_el.get("title") if title_el else None,
            title_el.get_text(strip=True) if title_el else None,
            link.get("title") if link else None,
            img.get("alt") if img else None,
        ) or ""
        add_item(url=url, title=title, thumb=_best_image_url(img), upload_date=upload_date)

    if len(by_id) < limit:
        for a in soup.select('a[href*="/index.php/vod/play/id/"]'):
            if len(by_id) >= limit:
                break
            url = _normalize_play_href(a.get("href") or "")
            if not url:
                continue
            img = a.find("img")
            add_item(
                url=url,
                title=_first_non_empty(a.get("title"), a.get_text(strip=True), img.get("alt") if img else None) or "",
                thumb=_best_image_url(img),
                upload_date=None,
            )

    return list(by_id.values())[:limit]


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = urljoin(BASE_SITE, raw.lstrip("/"))
    parsed = urlparse(raw)
    path = parsed.path or "/"
    page_num = max(1, int(page) if page else 1)

    m = _TYPE_PAGE_RE.search(path)
    if m:
        type_id = m.group("type_id")
        if page_num <= 1:
            new_path = f"/index.php/vod/type/id/{type_id}.html"
        else:
            new_path = f"/index.php/vod/type/id/{type_id}/page/{page_num}.html"
    elif page_num <= 1:
        new_path = path if path.endswith(".html") or path == "/" else path.rstrip("/") + "/"
    else:
        if path in ("/", ""):
            new_path = f"/index.php/vod/type/id/1/page/{page_num}.html"
        else:
            base_path = path.rstrip("/")
            if base_path.endswith(".html"):
                base_path = base_path[: -len(".html")]
            new_path = f"{base_path}/page/{page_num}.html"

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


def parse_video_page(
    html: str, url: str, *, video: dict[str, Any] | None = None
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    video_id = _extract_video_id(url) or ""
    page_url = _canonical_play_url(video_id) if video_id else url

    player_info = video or _streams_from_player(html)
    vod_name = player_info.pop("vod_name", None) if isinstance(player_info, dict) else None

    title = _clean_title(
        _first_non_empty(
            vod_name if isinstance(vod_name, str) else None,
            _meta(soup, prop="og:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    thumbnail = _first_non_empty(
        _meta(soup, prop="og:image"),
        _best_image_url(soup.select_one("img")),
    )
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    tags: list[str] = []
    for a in soup.select('a[href*="/index.php/vod/type/id/"]'):
        tag = a.get_text(strip=True)
        if tag and tag not in tags and len(tag) < 40:
            tags.append(tag)

    uploader = None
    if data := _extract_player_json(html):
        vod = data.get("vod_data") or {}
        if isinstance(vod, dict):
            uploader = vod.get("vod_actor") or None

    related = _parse_list_items(soup, limit=30)
    related = [r for r in related if r.get("url") != page_url]

    video_data = {
        k: v for k, v in (player_info or {}).items() if k in ("streams", "hls", "default", "has_video")
    }
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
        "views": None,
        "uploader_name": uploader,
        "category": None,
        "tags": tags or None,
        "upload_date": None,
        "video": video_data,
        "related_videos": related,
    }


async def scrape(url: str) -> dict[str, Any]:
    video_id = _extract_video_id(url)
    if not video_id:
        raise ValueError(f"Unsupported Tianmei URL: {url}")

    play_url = _canonical_play_url(video_id)
    html = await fetch_page(play_url, referer=BASE_SITE)
    video_data = _streams_from_player(html)
    return parse_video_page(html, play_url, video=video_data)


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    return _parse_list_items(soup, limit=limit)
