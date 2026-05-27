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
    
    # Strategy: Look for flashvars or mediaDefinitions
    # Common PH pattern: var flashvars_123 = {...}
    m = re.search(r'var\s+flashvars_\d+\s*=\s*(\{.*?\});', html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            media_defs = data.get("mediaDefinitions", [])
            for md in media_defs:
                video_url = md.get("videoUrl")
                if not video_url: continue
                
                fmt = md.get("format") # mp4, hls
                quality = md.get("quality") # 720p, 1080p, etc
                
                # Handle list types safely
                if isinstance(quality, list): 
                    if quality:
                        quality = str(quality[0])
                    else:
                        quality = None
                
                # Fallback to height if quality is missing
                if not quality and md.get("height"):
                     quality = str(md.get("height"))

                if fmt == "hls" or ".m3u8" in video_url:
                    # Extract quality from URL if strictly "hls" or "adaptive" in metadata
                    parsed_quality = "adaptive"
                    
                    if quality:
                        if isinstance(quality, str) and quality.isdigit():
                             parsed_quality = f"{quality}p"
                        else:
                             parsed_quality = str(quality)
                    
                    # Try regex on URL if quality is not specific
                    if not parsed_quality or parsed_quality == "adaptive":
                        # Try finding /1080P/ or similar patterns
                        m_q = re.search(r'/(\d{3,4})[pP]?/', video_url)
                        if not m_q:
                             m_q = re.search(r'(\d{3,4})[pP]_', video_url)
                             
                        if m_q:
                            parsed_quality = f"{m_q.group(1)}p"

                    hls_url = video_url
                    streams.append({
                        "quality": parsed_quality,
                        "url": video_url,
                        "format": "hls"
                    })
                elif fmt == "mp4":
                     streams.append({
                        "quality": str(quality) if quality else "unknown",
                        "url": video_url,
                        "format": "mp4"
                    })
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