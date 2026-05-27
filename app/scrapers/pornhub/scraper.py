from __future__ import annotations

import json
import re
import logging
from typing import Any, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

logger = logging.getLogger(__name__)


def can_handle(host: str) -> bool:
    return "pornhub.com" in host.lower()

def _best_image_url(img: Any) -> Optional[str]:
    """Extract the best image URL from an img element, checking multiple lazy-load attributes."""
    if img is None:
        return None
    
    # Check common lazy-loading attributes in order of preference
    # Pornhub uses: data-mediumthumb, data-thumb_url, src
    # Also check generic lazy-load attributes
    video_fallback = None  # Store first video URL as fallback
    
    for attr in ("data-mediumthumb", "data-thumb_url", "data-src", "data-original", "data-lazy", "data-image", "src", "data-mediabook"):
        value = img.get(attr)
        if not value:
            continue
        url = str(value).strip()
        if not url or "data:image" in url:
            continue
        
        # Check if it's a video file
        url_lower = url.lower()
        is_video = any(url_lower.endswith(ext) or f'{ext}/' in url_lower or f'{ext}?' in url_lower 
                      for ext in ('.mp4', '.webm', '.m3u8', '.ts'))
        
        if is_video:
            # Save as fallback but keep looking for image
            if not video_fallback:
                video_fallback = url
            continue
        
        # Found a valid image URL!
        return url
    
    # If no image URL found, use video as fallback (better than null)
    return video_fallback

def get_categories() -> list[dict]:
    import os
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


_BASE_URL = "https://www.pornhub.com"
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{_BASE_URL}/",
    # Desktop layout + reduce age-gate variance
    "Cookie": "platform=pc; age_verified=1",
}


def _parse_balanced_json(html: str, start: int) -> Any | None:
    """Parse a JSON object/array starting at `start` (must point to { or [)."""
    if start >= len(html):
        return None
    opener = html[start]
    if opener not in "{[":
        return None
    closer = "}" if opener == "{" else "]"

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
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


async def fetch_html(url: str) -> str:
    # Use shared connection pool (retries / UA rotation), but keep PH-specific headers.
    return await pool_fetch_html(url, headers=_DEFAULT_HEADERS)


def _extract_media_definitions(html: str) -> list[dict[str, Any]]:
    """
    Pornhub is on the Aylo network — mediaDefinitions often live under:
    - var flashvars_<id> = {...}
    - var page_params = { video_player_setup: { playervars: { mediaDefinitions: [...] } } }
    - a raw `mediaDefinitions: [...]` block
    """
    # 1) flashvars_<id>
    m = re.search(r"\bflashvars_\d+\s*=\s*\{", html)
    if m:
        obj = _parse_balanced_json(html, m.end() - 1)
        if isinstance(obj, dict):
            media_defs = obj.get("mediaDefinitions")
            if isinstance(media_defs, list):
                return [md for md in media_defs if isinstance(md, dict)]

    # 2) page_params
    m = re.search(r"\bpage_params\s*=\s*\{", html)
    if m:
        obj = _parse_balanced_json(html, m.end() - 1)
        if isinstance(obj, dict):
            setup = obj.get("video_player_setup", {})
            if isinstance(setup, dict):
                playervars = setup.get("playervars", {})
                if isinstance(playervars, dict):
                    media_defs = playervars.get("mediaDefinitions")
                    if isinstance(media_defs, list):
                        return [md for md in media_defs if isinstance(md, dict)]

    # 3) raw mediaDefinitions array
    m = re.search(r'\bmediaDefinitions\b["\']?\s*:\s*\[', html)
    if m:
        parsed = _parse_balanced_json(html, m.end() - 1)
        if isinstance(parsed, list):
            return [md for md in parsed if isinstance(md, dict)]

    return []


def _is_proxy_media_url(url: str) -> bool:
    # Common for Aylo properties: /media/... ?s=<base64/JSON token>
    return "/media/" in url and "?s=" in url


async def _resolve_proxy_url(proxy_url: str, expected_format: str | None = None) -> list[dict[str, Any]]:
    """
    Resolve a Pornhub proxy URL (e.g., /media/mp4?s=...) to actual CDN streams.
    Returns stream objects with quality, url, format.
    """
    if proxy_url.startswith("/"):
        proxy_url = urljoin(_BASE_URL, proxy_url)

    try:
        headers = {
            "User-Agent": _DEFAULT_HEADERS["User-Agent"],
            "Accept": "application/json",
            "Referer": _DEFAULT_HEADERS["Referer"],
            # Keep desktop cookie to avoid 400 responses on some proxy endpoints
            "Cookie": _DEFAULT_HEADERS["Cookie"],
        }
        async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(proxy_url)
            if resp.status_code != 200:
                logger.info("Pornhub proxy resolve failed: %s -> %s", resp.status_code, proxy_url)
                return []

            data: Any = resp.json()
            if isinstance(data, dict):
                if "videoUrl" in data:
                    data = [data]
                else:
                    return []

            if not isinstance(data, list):
                return []

            streams: list[dict[str, Any]] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                fmt = item.get("format") or expected_format or "mp4"
                if expected_format and fmt != expected_format:
                    continue
                video_url = item.get("videoUrl")
                if not video_url:
                    continue
                if video_url.endswith((".jpg", ".jpeg", ".png")):
                    continue

                quality = item.get("quality")
                if isinstance(quality, int):
                    quality = str(quality)
                elif isinstance(quality, list):
                    quality = str(quality[0]) if quality else None

                stream_fmt = "hls" if fmt == "hls" or ".m3u8" in video_url else "mp4"
                q = "unknown"
                if quality:
                    q = f"{quality}p" if str(quality).isdigit() else str(quality)
                if stream_fmt == "hls" and q in ("unknown", "hls", "adaptive"):
                    q = "adaptive"

                streams.append({"quality": q, "url": video_url, "format": stream_fmt})

            return streams
    except Exception as exc:
        logger.debug("Pornhub proxy resolve exception for %s: %s", proxy_url, exc)

    return []


def _extract_video_streams(html: str) -> dict[str, Any]:
    streams: list[dict[str, Any]] = []
    hls_url: str | None = None

    media_defs = _extract_media_definitions(html)
    for md in media_defs:
        video_url = md.get("videoUrl")
        if not video_url:
            continue
        if isinstance(video_url, str) and video_url.endswith((".jpg", ".jpeg", ".png")):
            continue

        if isinstance(video_url, str) and video_url.startswith("/"):
            video_url = urljoin(_BASE_URL, video_url)

        fmt = md.get("format") or ("hls" if isinstance(video_url, str) and ".m3u8" in video_url else "mp4")
        quality = md.get("quality")
        if isinstance(quality, int):
            quality = str(quality)
        elif isinstance(quality, list):
            quality = str(quality[0]) if quality else None

        # Some pages may use `height` as the numeric quality signal
        if not quality and md.get("height"):
            try:
                quality = str(int(md.get("height")))
            except Exception:
                quality = None

        q = "unknown"
        if quality:
            q = f"{quality}p" if str(quality).isdigit() else str(quality)

        stream_fmt = "hls" if fmt == "hls" or (isinstance(video_url, str) and ".m3u8" in video_url) else "mp4"
        if stream_fmt == "hls":
            if q in ("unknown", "hls"):
                q = "adaptive"
            hls_url = str(video_url)

        streams.append({"quality": q, "url": video_url, "format": stream_fmt})

    default_url = hls_url or (streams[0]["url"] if streams else None)
    return {"streams": streams, "default": default_url, "has_video": bool(streams)}

def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    
    # Title
    title = None
    meta_title = soup.find("meta", property="og:title")
    if meta_title: title = meta_title.get("content")
    if not title:
        t_tag = soup.find("title")
        if t_tag: title = t_tag.get_text(strip=True)
        
    # Cleanup title
    if title:
        title = title.replace(" - Pornhub.com", "")
        
    # Thumbnail
    thumbnail = None
    meta_thumb = soup.find("meta", property="og:image")
    if meta_thumb: thumbnail = meta_thumb.get("content")
    
    # Duration
    duration = None
    # PH duration often in meta property="video:duration" (seconds)
    meta_dur = soup.find("meta", property="video:duration")
    if meta_dur:
        try:
            secs = int(meta_dur.get("content"))
            m, s = divmod(secs, 60)
            h, m = divmod(m, 60)
            if h > 0:
                duration = f"{h}:{m:02d}:{s:02d}"
            else:
                duration = f"{m}:{s:02d}"
        except Exception:
            pass
            
    # Views
    views = None
    # Look for .count
    count_el = soup.select_one(".views .count")
    if count_el:
        views = count_el.get_text(strip=True)
        
    # Uploader
    uploader = None
    user_el = soup.select_one(".userInfo .username, .video-detailed-info .username")
    if user_el:
        uploader = user_el.get_text(strip=True)
        
    # Tags
    tags = []
    for t in soup.select(".tagsWrapper a.tags"):
        txt = t.get_text(strip=True)
        if txt: tags.append(txt)
        
    # Video Streams
    video_data = _extract_video_streams(html)
    
    return {
        "url": url,
        "title": title,
        "description": None, # Optional
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": "Pornhub",
        "tags": tags,
        "video": video_data,
        "related_videos": [], # TODO
        "preview_url": None # TODO
    }

async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    result = parse_page(html, url)

    video_data = result.get("video", {})
    streams: list[dict[str, Any]] = video_data.get("streams", [])

    # Resolve /media/... proxy entries to real CDN URLs (prevents 400 when clients request them directly).
    for stream in streams[:]:
        stream_url = str(stream.get("url", "") or "")
        if not _is_proxy_media_url(stream_url):
            continue

        resolved = await _resolve_proxy_url(stream_url, expected_format=stream.get("format"))
        if resolved:
            streams.remove(stream)
            streams.extend(resolved)

    # Sort by numeric quality descending
    def _qval(s: dict) -> int:
        q = s.get("quality", "")
        digits = "".join(filter(str.isdigit, str(q)))
        return int(digits) if digits else 0

    streams.sort(key=_qval, reverse=True)
    video_data["streams"] = streams
    video_data["has_video"] = bool(streams)

    if streams:
        hls_stream = next((s for s in streams if s.get("format") == "hls"), None)
        video_data["default"] = hls_stream["url"] if hls_stream else streams[0]["url"]
    else:
        video_data["default"] = None

    return result

async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    # PH search/list url: /video?o=new&page=2
    # simple listing: pornhub.com/video?page=2
    
    url = base_url
    
    # Handle homepage URL by defaulting to /video for pagination support
    if url.rstrip("/") in ("https://www.pornhub.com", "http://www.pornhub.com"):
        url = "https://www.pornhub.com/video"
        
    if page > 1:
        if "?" in url:
            url += f"&page={page}"
        else:
            url += f"?page={page}"
        
    try:
        html = await fetch_html(url)
    except Exception:
        # Fallback or return empty if fetch fails (e.g. 403 Forbidden)
        return []

    soup = BeautifulSoup(html, "lxml")
    
    items = []
    # PH video blocks: li.pcVideoListItem
    # PH video blocks: li.pcVideoListItem
    for li in soup.select("li.pcVideoListItem"):
        try:
            if not li.get("data-video-vkey"): continue
            
            link = li.select_one("a")
            if not link: continue
            
            href = link.get("href")
            if not href or "javascript" in href.lower(): continue
            
            if not href.startswith("http"):
                href = "https://www.pornhub.com" + href
                
            title = link.get("title")
            if not title:
                t_el = li.select_one(".title a")
                if t_el: title = t_el.get_text(strip=True)
                
            img_el = li.select_one("img")
            thumb = _best_image_url(img_el)
                
            dur_el = li.select_one(".duration")
            duration = dur_el.get_text(strip=True) if dur_el else None
            
            view_el = li.select_one(".network-view-count") # sometimes different
            views = view_el.get_text(strip=True) if view_el else None
            
            if not views:
                v_var = li.select_one(".views var")
                if v_var: views = v_var.get_text(strip=True)
                
            uploader = None
            u_el = li.select_one(".usernameWrap a")
            if u_el: uploader = u_el.get_text(strip=True)
            
            items.append({
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader,
            })
        except Exception:
            continue
        
    return items