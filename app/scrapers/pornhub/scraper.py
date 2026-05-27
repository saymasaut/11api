from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup


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


async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Cookie": "platform=pc" # Critical for consistent desktop HTML structure
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=20.0),
        headers=headers,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _extract_video_streams(html: str) -> dict[str, Any]:
    streams = []
    hls_url = None
    
    def _add_stream(video_url: str, fmt: Optional[str], quality: Any) -> None:
        nonlocal hls_url
        if not video_url:
            return

        fmt_l = (fmt or "").lower().strip()
        if not fmt_l:
            if ".m3u8" in video_url.lower():
                fmt_l = "hls"
            elif ".mp4" in video_url.lower():
                fmt_l = "mp4"

        # Normalize/derive quality
        if isinstance(quality, list):
            quality = str(quality[0]) if quality else None
        if quality is not None:
            quality = str(quality).strip()
        if not quality:
            # Try finding /1080P/ or similar patterns
            m_q = re.search(r'/(\d{3,4})[pP]?/', video_url)
            if not m_q:
                m_q = re.search(r'(\d{3,4})[pP]_', video_url)
            quality = f"{m_q.group(1)}p" if m_q else ("adaptive" if fmt_l == "hls" else "unknown")
        elif quality.isdigit():
            quality = f"{quality}p"

        if fmt_l == "hls":
            hls_url = video_url
            streams.append({"quality": quality or "adaptive", "url": video_url, "format": "hls"})
        elif fmt_l == "mp4":
            streams.append({"quality": quality or "unknown", "url": video_url, "format": "mp4"})

    def _parse_media_definitions(media_defs: Any) -> None:
        if not isinstance(media_defs, list):
            return
        for md in media_defs:
            if not isinstance(md, dict):
                continue
            video_url = md.get("videoUrl") or md.get("video_url") or md.get("url")
            fmt = md.get("format") or md.get("type")
            quality = md.get("quality") or md.get("height") or md.get("resolution")
            _add_stream(video_url=str(video_url).strip() if video_url else "", fmt=str(fmt) if fmt else None, quality=quality)

    # Strategy 1: flashvars patterns (Pornhub has multiple variants)
    # - var flashvars_123 = {...};
    # - var flashvars = {...};
    for pat in (
        r'var\s+flashvars_\d+\s*=\s*(\{.*?\});',
        r'var\s+flashvars\s*=\s*(\{.*?\});',
    ):
        m = re.search(pat, html, re.DOTALL)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
            _parse_media_definitions(data.get("mediaDefinitions", []))
        except Exception:
            pass

    # Strategy 2: raw mediaDefinitions array embedded in scripts
    # - mediaDefinitions: [...]
    # - "mediaDefinitions":[...]
    if not streams:
        m = re.search(r'mediaDefinitions"\s*:\s*(\[[\s\S]*?\])', html, re.DOTALL)
        if not m:
            m = re.search(r'mediaDefinitions\s*:\s*(\[[\s\S]*?\])', html, re.DOTALL)
        if m:
            try:
                media_defs = json.loads(m.group(1))
                _parse_media_definitions(media_defs)
            except Exception:
                pass
            
    # Determine default
    default_url = None
    if hls_url:
        default_url = hls_url
    elif streams:
        default_url = streams[0]["url"]

    return {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0
    }

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
    return parse_page(html, url)

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