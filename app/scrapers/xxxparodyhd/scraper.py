from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from app.core import pool


def can_handle(host: str) -> bool:
    h = host.lower()
    # Listing pages use xxxparodyhd.net; embed players often use urshort.live (and similar).
    return "xxxparodyhd.net" in h or "urshort.live" in h


async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    session = await pool.get_session()
    async with session.get(url, headers=headers, allow_redirects=True) as resp:
        resp.raise_for_status()
        
        # Check if a paginated request was redirected to the base URL (meaning no more pages)
        if "/page/" in url and "/page/" not in str(resp.url):
            return ""
            
        return await resp.text()


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
            url = str(v).strip()
            if url.startswith("//"):
                return f"https:{url}"
            return url
    return None


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    # Title
    title_node = soup.select_one("h1.entry-title, h1.post-title, h1")
    title = _text(title_node) or _text(soup.find("title")) or "Unknown Video"

    # Clean title
    for suffix in (" - XXXParodyHD", " – XXXParodyHD", " | XXXParodyHD"):
        if title.endswith(suffix):
            title = title[: -len(suffix)]

    # Description / meta
    desc_node = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
    description = desc_node.get("content") if desc_node else None

    # Thumbnail (og:image)
    og_img = soup.find("meta", property="og:image")
    thumbnail = og_img.get("content") if og_img else None

    # Extract embedded player URLs (iframes)
    embed_urls = []
    # Look for tab content with player links
    for a_tag in soup.select(".su-spoiler-content a[href], .entry-content a[href], .Rtable1 a[href]"):
        href = a_tag.get("href", "")
        text = _text(a_tag) or ""
        # Common embed hosts
        embed_hosts = ["dood", "doply", "vidnest", "player4me", "upns", "voe.sx",
                       "embedseek", "seekplayer", "mixdrop", "easyvidplayer", "rpmplay",
                       "luluvid", "streamtape", "frdl", "hxfile", "xshotcok", "urshort"]
        if any(h in href.lower() for h in embed_hosts):
            embed_urls.append({
                "url": href,
                "label": text or "Player",
            })

    # Also check for iframes
    for iframe in soup.select("iframe[src]"):
        src = iframe.get("src", "")
        if src and ("http" in src or src.startswith("//")):
            if src.startswith("//"):
                src = f"https:{src}"
            embed_urls.append({
                "url": src,
                "label": "Embedded Player",
            })

    # Build video data
    # XXXParodyHD doesn't host videos directly - it links to external embed players
    video_url = None
    streams = []
    if embed_urls:
        # Prioritize Streamtape as default if available
        streamtape_stream = next(
            (
                e
                for e in embed_urls
                if "streamtape" in e["url"].lower() or "streamtape" in e.get("label", "").lower()
            ),
            None,
        )
        video_url = streamtape_stream["url"] if streamtape_stream else embed_urls[0]["url"]
        
        for idx, embed in enumerate(embed_urls):
            streams.append({
                "url": embed["url"],
                "quality": embed.get("label", f"Player {idx + 1}"),
                "format": "embed",
            })
        if video_url:
            for s in streams:
                if s.get("url") == video_url:
                    s["format"] = "default"
                    break

    # Tags / Genres
    tags = []
    for a in soup.select('.entry-terms a[href*="/genre/"], .tag-links a, a[rel="tag"]'):
        t = _text(a)
        if t:
            tags.append(t)

    # Category info
    category_links = soup.select('.entry-terms a[href*="/category/"]')
    categories = [_text(cl) for cl in category_links if _text(cl)]

    # Studio / Director
    studio_links = soup.select('.entry-terms a[href*="/director/"]')
    studio = _text(studio_links[0]) if studio_links else None

    # Cast
    cast_links = soup.select('.entry-terms a[href*="/cast/"]')
    cast = [_text(cl) for cl in cast_links if _text(cl)]

    # Year
    year_links = soup.select('.entry-terms a[href*="/release-year/"]')
    year = _text(year_links[0]) if year_links else None

    # Views
    views = None
    views_match = re.search(r'Views:\s*([\d,]+)', html)
    if views_match:
        views = views_match.group(1)

    # Related Videos
    related_videos = []
    # Related are typically listed below the main content
    for item in soup.select(".post-thumbnail, .item-list .post"):
        try:
            link = item.select_one("a[href]")
            if not link:
                continue
            r_url = link.get("href", "")
            if not r_url or "xxxparodyhd.net" not in r_url:
                continue

            r_title = link.get("title") or _text(link)
            img = item.select_one("img")
            r_thumb = _best_image_url(img)
            r_dur = None
            dur_el = item.select_one(".duration, .runtime")
            if dur_el:
                r_dur = _text(dur_el)

            related_videos.append({
                "url": r_url,
                "title": r_title,
                "thumbnail_url": r_thumb,
                "duration": r_dur,
            })
            if len(related_videos) >= 10:
                break
        except Exception:
            continue

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "tags": tags,
        "categories": categories,
        "studio": studio,
        "cast": cast,
        "year": year,
        "views": views,
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
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    target_url = base_url.rstrip("/")

    if page > 1:
        if "?" in target_url:
            base, query = target_url.split("?", 1)
            target_url = f"{base.rstrip('/')}/page/{page}/?{query}"
        else:
            target_url = f"{target_url}/page/{page}/"
    elif not target_url.endswith("/") and "?" not in target_url:
        target_url += "/"

    try:
        html = await fetch_html(target_url)
    except Exception as e:
        # ClientResponseError from aiohttp raise_for_status() exposes .status
        if getattr(e, "status", None) == 404:
            return []
        raise

    if not html:
        return []
        
    soup = BeautifulSoup(html, "lxml")

    items: list[dict[str, Any]] = []

    # XXXParodyHD uses .ml-item containers (WP-Movie theme)
    for card in soup.select(".ml-item"):
        try:
            # Main link: a.ml-mask
            link = card.select_one("a.ml-mask")
            if not link:
                link = card.select_one("a[href]")
            if not link:
                continue

            href = link.get("href", "") or link.get("data-href", "")
            if not href:
                continue

            # Skip non-video links
            skip_patterns = ["/category/", "/genre/", "/director/", "/cast/",
                             "/release-year/", "/page/", "#", "/tag/"]
            if any(p in href for p in skip_patterns):
                continue

            # Title from .mli-info h2, or link title/oldtitle attribute
            title_el = card.select_one(".mli-info h2")
            title = _text(title_el) if title_el else (
                link.get("oldtitle") or link.get("title") or _text(link)
            )
            if not title:
                continue

            # Thumbnail
            img = card.select_one("img")
            thumb = _best_image_url(img)

            # Duration from .mli-info1
            dur_el = card.select_one(".mli-info1")
            duration = _text(dur_el) if dur_el else None

            # Year from hidden_tip div
            year = None
            tip = card.select_one("#hidden_tip")
            if tip:
                year_link = tip.select_one("a[href*='/release-year/']")
                if year_link:
                    year = _text(year_link)

            items.append({
                "url": href,
                "title": title or "Unknown Video",
                "thumbnail_url": thumb,
                "duration": duration,
                "upload_time": year,
            })
        except Exception:
            continue

    return items

