
from __future__ import annotations

import json
import re
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup


def can_handle(host: str) -> bool:
    return host.lower().endswith("xhamster.com")

def get_categories() -> list[dict]:
    import os
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    for k in ("data-src", "data-original", "data-lazy", "src"):
        v = img.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return None


def _find_duration_like_text(node: Any) -> Optional[str]:
    try:
        text = node.get_text(" ", strip=True)
    except Exception:
        return None
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    return m.group(0) if m else None


from app.core.pool import pool, fetch_html


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


def _parse_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=False)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue

        if isinstance(parsed, dict):
            out.append(parsed)
        elif isinstance(parsed, list):
            out.extend([x for x in parsed if isinstance(x, dict)])
    return out


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,\n]", value) if x.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_duration(seconds_or_iso: Any) -> Optional[str]:
    if seconds_or_iso is None:
        return None

    if isinstance(seconds_or_iso, (int, float)):
        total = int(seconds_or_iso)
        h = total // 3600
        m = (total % 3600) // 60
        s = total % 60
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    if isinstance(seconds_or_iso, str):
        v = seconds_or_iso.strip()
        m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v)
        if m:
            h = int(m.group(1) or 0)
            mm = int(m.group(2) or 0)
            s = int(m.group(3) or 0)
            if h > 0:
                return f"{h}:{mm:02d}:{s:02d}"
            return f"{mm}:{s:02d}"
        return v or None

    return str(seconds_or_iso).strip() or None


def _format_views_num(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    
    # Remove commas, spaces, and other non-essential chars
    # Keep numbers, dots, and common suffixes
    clean_v = re.sub(r'[^0-9KMB\.]', '', v.upper())
    
    # If it already has a suffix, just return it
    if any(s in clean_v for s in ('K', 'M', 'B')):
        return clean_v
        
    # Otherwise, try to shorten the raw number
    try:
        # Extract only digits for the conversion
        raw_digits = re.sub(r'[^0-9]', '', clean_v)
        if not raw_digits: return clean_v
        
        num = int(raw_digits)
        if num >= 1_000_000_000:
            val = num / 1_000_000_000
            return f"{int(val * 10) / 10:.1f}B".replace(".0", "")
        if num >= 1_000_000:
            val = num / 1_000_000
            return f"{int(val * 10) / 10:.1f}M".replace(".0", "")
        if num >= 1_000:
            val = num / 1_000
            return f"{int(val * 10) / 10:.1f}K".replace(".0", "")
        return str(num)
    except Exception:
        return clean_v


def _extract_views(video_obj: Optional[dict[str, Any]], html: str, soup: BeautifulSoup) -> Optional[str]:
    v_str = None
    if video_obj:
        for key in ("interactionCount", "viewCount", "views"):
            v = video_obj.get(key)
            if v is not None and str(v).strip():
                v_str = str(v).strip()
                break

        if not v_str:
            stats = video_obj.get("interactionStatistic")
            if isinstance(stats, dict):
                v = stats.get("userInteractionCount") or stats.get("interactionCount")
                if v is not None and str(v).strip():
                    v_str = str(v).strip()
            elif isinstance(stats, list):
                for s in stats:
                    if not isinstance(s, dict):
                        continue
                    v = s.get("userInteractionCount") or s.get("interactionCount")
                    if v is not None and str(v).strip():
                        v_str = str(v).strip()
                        break

    if not v_str:
        for pattern in (
            r'"userInteractionCount"\s*:\s*"?([0-9][0-9,\.]*(?:\s*[KMB])?)"?',
            r'"interactionCount"\s*:\s*"?([0-9][0-9,\.]*(?:\s*[KMB])?)"?',
            r'"viewCount"\s*:\s*"?([0-9][0-9,\.]*(?:\s*[KMB])?)"?',
            r'"views"\s*:\s*"?([0-9][0-9,\.]*(?:\s*[KMB])?)"?',
        ):
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                v_str = m.group(1).replace(" ", "").upper()
                v_str = re.sub(r"[^0-9KMB\.]", "", v_str)
                v_str = v_str.rstrip(".")
                break

    if not v_str:
        text = soup.get_text(" ", strip=True)
        m = re.search(r"(\d+(?:\.\d+)?)\s*([KMB])?\s*(?:views|view)\b", text, re.IGNORECASE)
        if m:
            num = m.group(1)
            suffix = (m.group(2) or "").upper()
            v_str = f"{num}{suffix}" if suffix else num
        else:
            m = re.search(r"([0-9][0-9,\.\s]*)\s*(?:views|view)", text, re.IGNORECASE)
            if m:
                v_str = m.group(1).strip().replace(" ", "").replace(",", "")

    # One last attempt: search the entire HTML for window.initials views
    if not v_str:
        m = re.search(r'"views"\s*:\s*(\d+)', html)
        if m:
            v_str = m.group(1)

    return _format_views_num(v_str)


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    og_title = _meta(soup, prop="og:title")
    og_desc = _meta(soup, prop="og:description")
    og_image = _meta(soup, prop="og:image")
    meta_desc = _meta(soup, name="description")

    title = _first_non_empty(og_title, _text(soup.find("title")))
    description = _first_non_empty(og_desc, meta_desc)
    thumbnail = _first_non_empty(og_image)

    json_ld = _parse_json_ld(soup)
    video_obj: Optional[dict[str, Any]] = None
    for obj in json_ld:
        t = obj.get("@type")
        if isinstance(t, list):
            if any(str(x).lower() == "videoobject" for x in t):
                video_obj = obj
                break
        if isinstance(t, str) and t.lower() == "videoobject":
            video_obj = obj
            break

    duration = None
    uploader = None
    category = None
    tags: list[str] = []

    if video_obj:
        title = _first_non_empty(title, video_obj.get("name"))
        description = _first_non_empty(description, video_obj.get("description"))

        thumb = video_obj.get("thumbnailUrl") or video_obj.get("thumbnail")
        if isinstance(thumb, list):
            thumb = next((x for x in thumb if isinstance(x, str) and x.strip()), None)
        thumbnail = _first_non_empty(thumbnail, thumb)

        duration = _normalize_duration(video_obj.get("duration"))

        author = video_obj.get("author")
        if isinstance(author, dict):
            uploader = _first_non_empty(author.get("name"), author.get("alternateName"))
        elif isinstance(author, str):
            uploader = author.strip() or None

        genre = video_obj.get("genre")
        if isinstance(genre, str):
            category = genre.strip() or None
        elif isinstance(genre, list) and genre:
            category = str(genre[0]).strip() or None

        tags = _as_list(video_obj.get("keywords"))

    if not tags:
        for a in soup.select('a[href*="/tags/"]'):
            t = _text(a)
            if t:
                tags.append(t)
    tags = list(dict.fromkeys([t for t in tags if t]))

    if not category:
        for a in soup.select('a[href*="/categories/"]'):
            t = _text(a)
            if t:
                category = t
                break

    if not uploader:
        for a in soup.select('a[href*="/users/"]'):
            t = _text(a)
            if t:
                uploader = t
                break

    views = _extract_views(video_obj, html, soup)

    if not duration:
        m = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b", soup.get_text(" ", strip=True))
        if m:
            duration = m.group(1)

    # ZERO-COST VIDEO EXTRACTION
    video_data = _extract_video_data(html)

    # Related Videos Extraction
    related_videos = []
    # xHamster uses various containers, often 'div.related-videos' or list loops
    # Using a generic selector closer to what list_videos uses
    
    # Try finding the related container
    rel_container = soup.find(class_=re.compile(r"related-videos|upsell-videos"))
    if rel_container:
         # xHamster video cards
         for a in rel_container.find_all("a", class_="video-thumb__image-container"):
             try:
                 href = a.get("href")
                 if not href: continue
                 
                 # Title usually in sibling or specific attr
                 # xHamster structure is complex, often separate title container
                 # Try finding parent card
                 card = a.find_parent(class_="video-thumb")
                 if not card: continue
                 
                 t_el = card.find(class_="video-thumb__info__name")
                 r_title = _text(t_el)
                 
                 # Image
                 r_img = a.find("img") or a.find("noscript").find("img") # xhamster uses lazy load
                 if not r_img: r_img = a.find("img")
                 r_thumb = _best_image_url(r_img)
                 
                 # Duration
                 d_el = card.find(class_=re.compile("duration"))
                 r_dur = _text(d_el)

                 related_videos.append({
                    "url": href,
                    "title": r_title,
                    "thumbnail_url": r_thumb,
                    "duration": r_dur
                 })
                 if len(related_videos) >= 10: break
             except Exception:
                 continue

    # Preview Extraction
    preview_url = None
    # xHamster window.initials often has 'scrubber' or 'preview'
    # It was parsed inside _extract_video_data actually, but let's check if we can get it here.
    # We can invoke extraction again or passing it out is cleaner.
    # For now, let's do a quick regex for the scrubber since we don't return raw json from _extract
    
    # scrubber: { sprite: "..." }
    # or look for "url":".../sprite..."
    
    scrubber_match = re.search(r'["\']scrubber["\']\s*:\s*\{\s*["\']sprite["\']\s*:\s*["\']([^"\']+)["\']', html)
    if scrubber_match:
        preview_url = scrubber_match.group(1).replace("\\/", "/")

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": category,
        "tags": tags,
        "video": video_data,
        "related_videos": related_videos,
        "preview_url": preview_url, # Added preview
    }


def _extract_video_data(html: str) -> dict[str, Any]:
    """
    Extract video streams from xHamster's window.initials JSON
    """
    streams = []
    hls_url = None
    
    try:
        # Regex to find window.initials = { ... }
        match = re.search(r'window\.initials\s*=\s*({.+?});', html, re.DOTALL)
        if match:
            initials_json = match.group(1)
            data = json.loads(initials_json)
            
            # Navigate to video model
            # Structure usually: xplayerSettings -> sources -> standard / hls
            # Or: videoModel -> sources
            
            sources = None
            
            # Try xplayerSettings
            xplayer = data.get("xplayerSettings")
            if xplayer and isinstance(xplayer, dict):
                sources = xplayer.get("sources")
                
            # Try videoModel if xplayer not found
            if not sources:
                video_model = data.get("videoModel")
                if video_model and isinstance(video_model, dict):
                    sources = video_model.get("sources")
            
            if sources:
                # Extract HLS
                hls = sources.get("hls")
                if hls:
                     # hls can be string or object
                    if isinstance(hls, str):
                         hls_url = hls
                    elif isinstance(hls, dict) and "url" in hls:
                         hls_url = hls["url"]
                    
                    # Add HLS to streams array with proper format label
                    if hls_url:
                        streams.append({
                            "quality": "adaptive",
                            "url": hls_url,
                            "format": "hls"
                        })

                # Extract MP4 (standard - dict format)
                standard = sources.get("standard")
                if standard and isinstance(standard, dict):
                    # xHamster often provides: 240p, 480p, 720p, 1080p
                    for quality, items in standard.items():
                        # items is usually a list of urls or a single object
                        # often just key=quality, value=url or list
                        url_to_add = None
                        if isinstance(items, str):
                            url_to_add = items
                        elif isinstance(items, list) and len(items) > 0:
                            # Usually list of dicts or strings
                            first = items[0]
                            if isinstance(first, str):
                                url_to_add = first
                            elif isinstance(first, dict):
                                url_to_add = first.get("url")
                                
                        if url_to_add:
                            # Map quality names to simpler ones
                            q_label = quality
                            if "1080" in quality: q_label = "1080p"
                            elif "720" in quality: q_label = "720p"
                            elif "480" in quality: q_label = "480p"
                            elif "240" in quality: q_label = "240p"
                            
                            streams.append({
                                "quality": q_label,
                                "url": url_to_add,
                                "format": "mp4"
                            })

                # Extract MP4 (h264/mp4 - list format)
                # Some videos use 'h264' (list of dicts) instead of 'standard'
                mp4_list = sources.get("h264") or sources.get("mp4")
                if mp4_list and isinstance(mp4_list, list):
                     for item in mp4_list:
                         if isinstance(item, dict) and "url" in item:
                             # Extract quality
                             raw_q = str(item.get("quality", "default") or "default")
                             q_label = raw_q
                             
                             if "1080" in raw_q: q_label = "1080p"
                             elif "720" in raw_q: q_label = "720p"
                             elif "480" in raw_q: q_label = "480p"
                             elif "240" in raw_q: q_label = "240p"
                             
                             fmt = "mp4"
                             if ".m3u8" in str(item["url"]):
                                 fmt = "hls"
                             
                             streams.append({
                                "quality": q_label,
                                "url": item["url"],
                                "format": fmt
                             })

    except Exception:
        pass
        
    # Fallback: Regex extraction for HLS if not found in JSON
    # This matches the effective logic from debug scripts
    if not hls_url:
        m = re.search(r'["\'](https:[^"\']+\.m3u8[^"\']*)["\']', html)
        if m:
            hls_url = m.group(1).replace("\\/", "/")
            # Add HLS to streams if extracted via fallback
            streams.append({
                "quality": "adaptive",
                "url": hls_url,
                "format": "hls"
            })
        
    # Sort streams by quality (descending)
    def quality_score(s):
        q = s["quality"]
        if "1080" in q: return 1080
        if "720" in q: return 720
        if "480" in q: return 480
        if "240" in q: return 240
        return 0
        
    # Filter streams to keep ONLY 'hls' format
    streams = [s for s in streams if s.get("format") == "hls"]

    # Deduplicate streams based on URL
    unique_urls = set()
    unique_streams = []
    for s in streams:
        if s["url"] not in unique_urls:
            unique_urls.add(s["url"])
            unique_streams.append(s)
    streams = unique_streams

    streams.sort(key=quality_score, reverse=True)
    
    default_url = None
    if streams:
        default_url = streams[0]["url"]
    elif hls_url:
        default_url = hls_url
        
    return {
        "streams": streams,
        "default": default_url,
        "has_video": len(streams) > 0 or hls_url is not None
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)
    return parse_page(html, url)


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    root = base_url if base_url.endswith("/") else base_url + "/"

    effective_limit: int | None = None
    if limit is not None and limit > 0:
        effective_limit = limit

    candidates: list[str] = []
    if page <= 1:
        candidates.append(root)
    else:
        if "/categories/" in root or "/photos/categories/" in root:
             candidates.append(f"{root.rstrip('/')}/{page}")

        candidates.extend(
            [
                f"{root}?page={page}",
                f"{root}newest/{page}/",
                f"{root}newest/{page}",
                f"{root}videos?page={page}",
            ]
        )

    html = ""
    used = ""
    last_exc: Exception | None = None
    for c in candidates:
        try:
            html = await fetch_html(c)
            used = c
            if html:
                break
        except Exception as e:
            last_exc = e
            continue

    if not html:
        if last_exc:
            raise last_exc
        return []

    soup = BeautifulSoup(html, "lxml")
    base_uri = httpx.URL(used)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Attempt to extract from window.initials
    initials_match = re.search(r"window\.initials\s*=\s*({.+?});", html, re.DOTALL)
    video_props = []
    if initials_match:
        try:
            data = json.loads(initials_match.group(1))
            
            # Possible paths for videoThumbProps
            paths = [
                data.get("layoutPage", {}).get("videoListProps", {}).get("videoThumbProps", []),
                data.get("layoutPage", {}).get("trendingVideoListProps", {}).get("videoThumbProps", []),
                data.get("searchResult", {}).get("videoThumbProps", []),
                data.get("pagesCategoryComponent", {}).get("trendingVideoListProps", {}).get("videoThumbProps", []),
                data.get("pagesCategoryComponent", {}).get("videoListProps", {}).get("videoThumbProps", []),
            ]
            for val in paths:
                if isinstance(val, list) and val:
                    video_props = val
                    break
        except Exception:
            pass

    if video_props:
        for vid in video_props:
            url = vid.get("pageURL")
            if not url:
                continue
                
            try:
                abs_url = str(base_uri.join(url))
            except Exception:
                continue
                
            if abs_url in seen:
                continue
                
            seen.add(abs_url)
            
            thumb = vid.get("thumbURL") or vid.get("imageURL")
            
            duration_val = vid.get("duration")
            duration_str = None
            if isinstance(duration_val, (int, float)):
                duration_val = int(duration_val)
                mins, secs = divmod(duration_val, 60)
                hours, mins = divmod(mins, 60)
                if hours > 0:
                    duration_str = f"{hours}:{mins:02d}:{secs:02d}"
                else:
                    duration_str = f"{mins}:{secs:02d}"

            views_val = vid.get("views")
            views_str = _format_views_num(str(views_val)) if views_val is not None else None
            
            landing = vid.get("landing") or {}
            uploader_name = landing.get("name") if isinstance(landing, dict) else None
            uploader_avatar_url = landing.get("logo") if isinstance(landing, dict) else None
            
            items.append({
                "url": abs_url,
                "title": vid.get("title"),
                "thumbnail_url": thumb,
                "duration": duration_str,
                "views": views_str,
                "uploader_name": uploader_name,
                "uploader_avatar_url": uploader_avatar_url,
            })
    else:
        # Fallback to DOM parsing
        for a in soup.select('a[href*="/videos/"]'):
            href = a.get("href")
            if not href:
                continue
    
            if "/videos/" not in href:
                continue
    
            try:
                abs_url = str(base_uri.join(href))
            except Exception:
                continue
    
            if abs_url in seen:
                continue
    
            img = a.find("img")
            thumb = _best_image_url(img)
    
            # Try to find a specific title element first
            title_el = a.find(class_=re.compile(r"video-thumb-info__name"))
            title = _first_non_empty(
                _text(title_el),
                img.get("alt") if img else None,
                a.get("title"),
                _text(a),  # Fallback to the original broad search
            )
    
            duration = _find_duration_like_text(a)
    
            # Extract metadata from the video card or its parent container
            # Be conservative - only look at the anchor and its immediate parent/siblings
            # to avoid matching page-level elements
            card = a.parent if hasattr(a, 'parent') and a.parent else a
    
            # Extract views
            views = None
            views_el = card.find(class_=re.compile(r"video-thumb-views|video-thumb-info__views|entity-views-container__value|video-thumb-info__meta-item"))
            if views_el:
                views_text = _text(views_el)
                if views_text:
                    # Clean up the views text (e.g., "1.2M views" -> "1.2M")
                    views = _format_views_num(views_text)
            
            if not views:
                # Fallback: search for text pattern in the entire card text
                card_text = card.get_text(" ", strip=True)
                m = re.search(r"(\d+(?:\.\d+)?)\s*([KMB])?\s*(?:views|view)\b", card_text, re.IGNORECASE)
                if m:
                    num = m.group(1)
                    suffix = (m.group(2) or "").upper()
                    views = _format_views_num(f"{num}{suffix}" if suffix else num)
    
            # Extract uploader name with avatar
            uploader_name = None
            uploader_avatar_url = None
            
            uploader_el = card.find(class_=re.compile(r"video-uploader__name|video-thumb-uploader__name|video-user-info__name"))
            if not uploader_el:
                 # Try finding uploader link within the card only
                uploader_link = card.find('a', href=re.compile(r"/users/|/channels/|/creators/|/pornstars/"))
                if uploader_link:
                    uploader_name = _text(uploader_link)
            else:
                uploader_name = _text(uploader_el)
                uploader_link = uploader_el
                
            # Fallback if xHamster censors the name with "*******" for certain IP ranges
            if uploader_name and ("*" in uploader_name or uploader_name.strip() == ""):
                uploader_name = None
                
            if not uploader_name and uploader_link:
                href = uploader_link.get("href", "")
                if href:
                    parts = [p for p in href.split("/") if p and "?" not in p]
                    if parts:
                        uploader_name = parts[-1].replace("-", " ").title()
                
            # Extract uploader logo/avatar
            # Typical classes: video-uploader-logo, video-thumb-uploader__logo, etc.
            logo_el = card.find(class_=re.compile(r"video-uploader-logo|video-thumb-uploader__logo|video-user-info__avatar"))
            if logo_el:
                # Check for data-background-image first (often used for avatars)
                bg_img = logo_el.get("data-background-image")
                if bg_img:
                    uploader_avatar_url = str(bg_img).strip()
                elif logo_el.name == 'img':
                    uploader_avatar_url = _best_image_url(logo_el)
                else:
                    img_in_logo = logo_el.find('img')
                    if img_in_logo:
                         uploader_avatar_url = _best_image_url(img_in_logo)
            
            # If still no avatar, try checking the uploader link for an image
            if not uploader_avatar_url:
                 uploader_link = card.find('a', href=re.compile(r"/users/|/channels/"))
                 if uploader_link:
                     img = uploader_link.find('img')
                     if img:
                         # Check if it looks like an avatar (often small or specific class)
                         if "avatar" in str(img.get("class", "")) or "logo" in str(img.get("class", "")):
                             uploader_avatar_url = _best_image_url(img)
    
            # If no thumbnail, skip (usually not a card)
            if not thumb:
                continue
    
            seen.add(abs_url)
            items.append(
                {
                    "url": abs_url,
                    "title": title,
                    "thumbnail_url": thumb,
                    "duration": duration,
                    "views": views,
                    "uploader_name": uploader_name,
                    "uploader_avatar_url": uploader_avatar_url,
                }
            )

    return items


async def crawl_videos(
    base_url: str,
    start_page: int = 1,
    max_pages: int = 5,
    per_page_limit: int = 0,
    max_items: int = 500,
) -> list[dict[str, Any]]:
    if start_page < 1:
        start_page = 1
    if max_pages < 1:
        max_pages = 1
    if per_page_limit < 0:
        per_page_limit = 0
    if max_items < 1:
        max_items = 1

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    # If per_page_limit==0, we try to return "all cards on the page" by using no limit.
    for page in range(start_page, start_page + max_pages):
        page_items = await list_videos(
            base_url=base_url,
            page=page,
            limit=per_page_limit,
        )

        if not page_items:
            break

        for it in page_items:
            url = str(it.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            results.append(it)
            if len(results) >= max_items:
                return results

    return results