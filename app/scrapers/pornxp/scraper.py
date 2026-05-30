from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

BASE_SITE = "https://xpxp.eu/"
SITE_HOST = "xpxp.eu"
LEGACY_HOSTS = frozenset({"pornxp.io", "www.pornxp.io", "pornxp.hn", "www.pornxp.hn", "porn-xp.eu", "www.porn-xp.eu"})
_SUPPORTED_HOSTS = frozenset({SITE_HOST, "www.xpxp.eu", *LEGACY_HOSTS})


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in _SUPPORTED_HOSTS or h.endswith(".xpxp.eu")


def _normalize_host(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in LEGACY_HOSTS or host == SITE_HOST:
        return SITE_HOST
    return host or SITE_HOST


def _canonical_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return BASE_SITE
    parsed = urlparse(raw if "://" in raw else urljoin(BASE_SITE, raw.lstrip("/")))
    host = _normalize_host(raw)
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunparse((parsed.scheme or "https", host, path, "", parsed.query, ""))


def _absolute_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return value
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return urljoin(BASE_SITE, value)
    parsed = urlparse(value)
    if parsed.netloc and _normalize_host(value) == SITE_HOST:
        return value
    if parsed.netloc and (parsed.netloc.lower() in LEGACY_HOSTS):
        return _canonical_url(value)
    return value


async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": BASE_SITE,
    }
    target = _canonical_url(url)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=20.0),
        headers=headers,
    ) as client:
        resp = await client.get(target)
        resp.raise_for_status()
        return resp.text


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def _text(el: Any) -> Optional[str]:
    if el is None:
        return None
    t = getattr(el, "get_text", None)
    if callable(t):
        return t(strip=True) or None
    return None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for k in ("data-src", "data-original", "data-lazy", "src"):
        v = img.get(k)
        if v and str(v).strip():
            return _absolute_url(str(v).strip())
    return None


def _build_list_url(base_url: str, page: int) -> str:
    target = _canonical_url(base_url or BASE_SITE)
    if page <= 1:
        return target
    parsed = urlparse(target)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("p", None)
    query["page"] = str(page)
    return urlunparse(
        (parsed.scheme or "https", parsed.netloc or SITE_HOST, parsed.path, "", urlencode(query), "")
    )


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    page_url = _canonical_url(url)

    title_node = soup.select_one(".player_details h1")
    title = _text(title_node) or _text(soup.find("title")) or "Unknown Video"
    for suffix in (" &ndash; PornXP", " - PornXP", " – PornXP"):
        if title.endswith(suffix):
            title = title[: -len(suffix)]

    desc_node = soup.select_one("#desc")
    description = _text(desc_node)

    video_el = soup.select_one("video#player")
    thumbnail = None
    if video_el:
        poster = video_el.get("poster")
        if poster:
            thumbnail = _absolute_url(str(poster))

    video_url = None
    streams: list[dict[str, str]] = []
    if video_el:
        for source_el in video_el.select("source"):
            src = source_el.get("src")
            if not src:
                continue
            s_url = _absolute_url(str(src))
            q_label = source_el.get("title") or source_el.get("label") or "360p"
            if str(q_label).isdigit():
                q_label = f"{q_label}p"
            streams.append({"url": s_url, "quality": str(q_label)})

        if streams:
            def _qval(s: dict[str, str]) -> int:
                try:
                    return int(str(s["quality"]).replace("p", ""))
                except ValueError:
                    return 0

            streams.sort(key=_qval, reverse=True)
            video_url = streams[0]["url"]

    tags: list[str] = []
    for a in soup.select(".tags a"):
        t = _text(a)
        if t:
            tags.append(t)

    related_videos: list[dict[str, Any]] = []
    for item in soup.select(".item_cont"):
        try:
            link = item.select_one("a[href*='/videos/']")
            if not link:
                continue
            r_url = link.get("href") or ""
            related_videos.append(
                {
                    "url": _canonical_url(_absolute_url(r_url)),
                    "title": _text(item.select_one(".item_title")),
                    "thumbnail_url": _best_image_url(item.select_one(".item_img")),
                    "duration": _text(item.select_one(".item_dur")),
                }
            )
            if len(related_videos) >= 10:
                break
        except Exception:
            continue

    return {
        "url": page_url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "tags": tags,
        "related_videos": related_videos,
        "video": {
            "default": video_url,
            "has_video": video_url is not None,
            "streams": streams,
        },
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return parse_page(html, url)


def get_categories() -> list[dict[str, Any]]:
    file_path = os.path.join(os.path.dirname(__file__), "categories.json")
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        cat_url = _canonical_url(str(item.get("url") or ""))
        if not name or not cat_url:
            continue
        entry = {"name": name, "url": cat_url}
        if item.get("video_count") is not None:
            entry["video_count"] = item.get("video_count")
        out.append(entry)
    return out


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    target_url = _build_list_url(base_url, page)
    html = await fetch_html(target_url)
    soup = BeautifulSoup(html, "lxml")

    items: list[dict[str, Any]] = []
    for cont in soup.select(".item_cont"):
        if len(items) >= limit:
            break
        try:
            item = cont.select_one(".item")
            link = cont.select_one("a[href*='/videos/']")
            if not link:
                continue

            href = link.get("href") or ""
            abs_url = _canonical_url(_absolute_url(href))

            title = _text(cont.select_one(".item_title"))
            duration = _text(cont.select_one(".item_dur"))
            thumb = _best_image_url(cont.select_one(".item_img"))

            preview_url = item.get("data-preview") if item else None
            if preview_url:
                preview_url = _absolute_url(str(preview_url))

            items.append(
                {
                    "url": abs_url,
                    "title": title or "Unknown Video",
                    "thumbnail_url": thumb,
                    "duration": duration,
                    "preview_url": preview_url,
                }
            )
        except Exception:
            continue

    return items
