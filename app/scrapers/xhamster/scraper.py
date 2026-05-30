
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.core.pool import fetch_html


def can_handle(host: str) -> bool:
    host_l = host.lower().strip()
    # Support common xHamster mirror domains while avoiding overly-broad substring matches.
    # Examples: xhamster.com, m.xhamster.com, xhamster.desi, xhamster1.desi, xhamster2.xxx
    return bool(
        re.search(r"(^|\.)xhamster(\d+)?\.(com|desi|xxx)$", host_l)
        or re.search(r"(^|\.)xhaccess\.com$", host_l)
    )

def get_categories() -> list[dict]:
    import os

    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []

    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        cat_url = str(item.get("url") or "").strip()
        if not name or not cat_url:
            continue
        entry = {"name": name, "url": cat_url}
        if item.get("video_count") is not None:
            entry["video_count"] = item.get("video_count")
        out.append(entry)
    return out


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


def _parse_balanced_json(html: str, start: int) -> Any | None:
    """Parse JSON object/array from a specific offset with nesting awareness."""
    if start < 0 or start >= len(html):
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
                except Exception:
                    return None
    return None


def _extract_initials_data(html: str) -> dict[str, Any]:
    """Extract `window.initials` JSON safely without regex-brace truncation."""
    m = re.search(r"window\.initials\s*=\s*\{", html)
    if not m:
        return {}
    parsed = _parse_balanced_json(html, m.end() - 1)
    return parsed if isinstance(parsed, dict) else {}


def _collect_video_thumb_props(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Gather video cards from layout/search/category blocks in window.initials."""
    props: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_from(node: Any) -> None:
        if not isinstance(node, dict):
            return
        vtp = node.get("videoThumbProps")
        if not isinstance(vtp, list):
            return
        for item in vtp:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or item.get("pageURL") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            props.append(item)

    for root_key in ("layoutPage", "searchResult", "pagesCategoryComponent"):
        root = data.get(root_key)
        if not isinstance(root, dict):
            continue
        add_from(root)
        for child in root.values():
            if isinstance(child, dict):
                add_from(child)

    return props


def _pagination_url_from_initials(data: dict[str, Any], page: int) -> Optional[str]:
    if page <= 1:
        return None
    for container_key in ("layoutPage", "pagesCategoryComponent"):
        pag = (data.get(container_key) or {}).get("paginationProps")
        if not isinstance(pag, dict):
            continue
        template = str(pag.get("pageLinkTemplate") or "")
        if "{#}" in template:
            return template.replace("{#}", str(page))
        first = str(pag.get("pageLinkFirst") or "").strip()
        if first:
            return f"{first.rstrip('/')}/{page}"
    return None


def _is_video_list_url(href: str) -> bool:
    """Filter out non-watch links that still contain /videos/."""
    path = (href or "").split("?", 1)[0].rstrip("/").lower()
    if "/videos/" not in path:
        return False
    slug = path.rsplit("/videos/", 1)[-1]
    if not slug or slug in ("", "index"):
        return False
    banned = ("search", "categories", "tags", "photos", "creators", "channels")
    if slug in banned or any(f"/{b}/" in path for b in banned):
        return False
    return True


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

    related_videos: list[dict[str, Any]] = []
    rel_container = (
        soup.find(class_=re.compile(r"related-videos|upsell-videos|thumb-list--related", re.I))
        or soup.select_one(".thumb-list--related, [data-block='related'], section.related")
    )
    thumb_links: list[Any] = []
    if rel_container:
        thumb_links = rel_container.find_all("a", class_="video-thumb__image-container")
    if not thumb_links:
        main = soup.select_one("main") or soup
        thumb_links = main.find_all("a", class_="video-thumb__image-container")

    for a in thumb_links:
        try:
            href = a.get("href")
            if not href or not _is_video_list_url(href):
                continue

            card = a.find_parent(class_="video-thumb") or a.find_parent(
                class_=re.compile(r"thumb-list|video-thumb", re.I)
            )
            if not card:
                continue

            t_el = card.find(class_=re.compile(r"video-thumb.*name|thumb-image-container__title", re.I))
            r_title = _text(t_el)

            img_el = a.find("img")
            ns = a.find("noscript")
            if ns:
                img_el = ns.find("img") or img_el
            r_thumb = _best_image_url(img_el)

            d_el = card.find(class_=re.compile("duration", re.I))
            r_dur = _text(d_el)

            related_videos.append(
                {
                    "url": urljoin(url, href),
                    "title": r_title,
                    "thumbnail_url": r_thumb,
                    "duration": r_dur,
                }
            )
            if len(related_videos) >= 10:
                break
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


def _normalize_stream_url(url: Any) -> Optional[str]:
    if url is None:
        return None
    text = str(url).strip().replace("\\/", "/")
    return text or None


def _is_playable_url(url: Any) -> bool:
    """True for direct http(s) URLs; xHamster now ships encrypted hex tokens in JSON."""
    text = _normalize_stream_url(url)
    if not text:
        return False
    return text.startswith("http://") or text.startswith("https://")


def _normalize_quality_label(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text or text.lower() in ("auto", "default"):
        return "adaptive"
    if text.isdigit():
        return f"{text}p"
    if "1080" in text:
        return "1080p"
    if "720" in text:
        return "720p"
    if "480" in text:
        return "480p"
    if "240" in text:
        return "240p"
    if "144" in text:
        return "144p"
    if "2160" in text or "4k" in text.lower():
        return "2160p"
    return text


def _streams_from_hls_master(url: str) -> list[dict[str, Any]]:
    """Build HLS stream entries from a master playlist URL (may list multiple renditions)."""
    url = _normalize_stream_url(url) or url
    streams: list[dict[str, Any]] = []
    m = re.search(r"multi=([^/]+)/", url)
    if m:
        for part in m.group(1).split(","):
            segments = [s for s in part.split(":") if s]
            if not segments:
                continue
            qm = re.search(r"(\d+p)", segments[-1], re.IGNORECASE)
            q_label = _normalize_quality_label(qm.group(1) if qm else segments[-1])
            streams.append({"quality": q_label, "url": url, "format": "hls"})
    if not streams:
        streams.append({"quality": "adaptive", "url": url, "format": "hls"})
    return streams


def _append_stream(
    streams: list[dict[str, Any]],
    *,
    url: Any,
    quality: Any,
    fmt: str,
) -> None:
    normalized = _normalize_stream_url(url)
    if not _is_playable_url(normalized):
        return
    streams.append(
        {
            "quality": _normalize_quality_label(quality),
            "url": normalized,
            "format": fmt,
        }
    )


def _prefer_h264_m3u8(urls: list[str]) -> Optional[str]:
    if not urls:
        return None
    h264 = [u for u in urls if ".h264." in u.lower()]
    if h264:
        return h264[0]
    non_av1 = [u for u in urls if ".av1." not in u.lower()]
    if non_av1:
        return non_av1[0]
    return urls[0]


def _extract_hls_from_sources(hls: Any) -> Optional[str]:
    """Resolve a playable HLS URL from xplayer sources (shape varies by page version)."""
    if isinstance(hls, str):
        return _normalize_stream_url(hls) if _is_playable_url(hls) else None
    if not isinstance(hls, dict):
        return None
    if _is_playable_url(hls.get("url")):
        return _normalize_stream_url(hls.get("url"))

    # Prefer H.264 over AV1 for device compatibility (matches mobile client behavior).
    for codec in ("h264", "hevc", "vp9", "av1"):
        entry = hls.get(codec)
        if isinstance(entry, str) and _is_playable_url(entry):
            return _normalize_stream_url(entry)
        if isinstance(entry, dict):
            for key in ("url", "fallback", "masterUrl", "master"):
                val = entry.get(key)
                if _is_playable_url(val):
                    return _normalize_stream_url(val)

    for entry in hls.values():
        if isinstance(entry, str) and _is_playable_url(entry):
            return _normalize_stream_url(entry)
        if isinstance(entry, dict):
            for key in ("url", "fallback", "masterUrl", "master"):
                val = entry.get(key)
                if _is_playable_url(val):
                    return _normalize_stream_url(val)
    return None


def _extract_standard_streams(standard: Any) -> list[dict[str, Any]]:
    """Extract MP4/HLS entries from `sources.standard` (legacy resolution keys or codec lists)."""
    out: list[dict[str, Any]] = []
    if not isinstance(standard, dict):
        return out

    resolution_keys = ("240", "480", "720", "1080", "144", "2160", "4k")
    for key, items in standard.items():
        key_l = str(key).lower()
        # Codec buckets (av1/h264) hold encrypted tokens — iterate list items instead.
        if key_l in ("av1", "h264", "vp9", "hevc"):
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                q = item.get("quality") or item.get("label") or key
                fmt = "hls" if ".m3u8" in str(url or "") else "mp4"
                _append_stream(out, url=url, quality=q, fmt=fmt)
            continue

        url_to_add: Optional[str] = None
        quality_label = key
        if isinstance(items, str):
            url_to_add = items
        elif isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, str):
                url_to_add = first
            elif isinstance(first, dict):
                url_to_add = first.get("url")
                quality_label = first.get("quality") or first.get("label") or key
        elif isinstance(items, dict):
            url_to_add = items.get("url")
            quality_label = items.get("quality") or items.get("label") or key

        if not url_to_add:
            continue
        if any(res in key_l for res in resolution_keys):
            quality_label = key
        fmt = "hls" if ".m3u8" in str(url_to_add) else "mp4"
        _append_stream(out, url=url_to_add, quality=quality_label, fmt=fmt)
    return out


def _find_hls_in_html(html: str) -> Optional[str]:
    found: list[str] = []
    for pattern in (
        r"https://video(?:-cf|-nss)?\.xhcdn\.com[^\"'\s]+\.m3u8[^\"'\s]*",
        r'["\'](https:[^"\']+\.m3u8[^"\']*)["\']',
    ):
        for m in re.finditer(pattern, html, re.IGNORECASE):
            candidate = m.group(1) if m.lastindex else m.group(0)
            if _is_playable_url(candidate):
                normalized = _normalize_stream_url(candidate)
                if normalized and normalized not in found:
                    found.append(normalized)
    return _prefer_h264_m3u8(found)


def _extract_video_data(html: str) -> dict[str, Any]:
    """
    Extract video streams from xHamster's window.initials JSON and page markup.
    """
    streams: list[dict[str, Any]] = []
    hls_url: Optional[str] = None

    try:
        data = _extract_initials_data(html)
        if data:
            sources = None

            xplayer = data.get("xplayerSettings")
            if isinstance(xplayer, dict):
                sources = xplayer.get("sources")

            if not sources:
                video_model = data.get("videoModel")
                if isinstance(video_model, dict):
                    sources = video_model.get("sources")

            if isinstance(sources, dict):
                hls_url = _extract_hls_from_sources(sources.get("hls"))
                if hls_url:
                    streams.extend(_streams_from_hls_master(hls_url))

                streams.extend(_extract_standard_streams(sources.get("standard")))

                for list_key in ("h264", "mp4"):
                    mp4_list = sources.get(list_key)
                    if not isinstance(mp4_list, list):
                        continue
                    for item in mp4_list:
                        if not isinstance(item, dict):
                            continue
                        url = item.get("url")
                        raw_q = item.get("quality") or item.get("label") or "default"
                        fmt = "hls" if ".m3u8" in str(url or "") else "mp4"
                        _append_stream(streams, url=url, quality=raw_q, fmt=fmt)

    except Exception:
        pass

    if not hls_url:
        hls_url = _find_hls_in_html(html)
        if hls_url:
            streams.extend(_streams_from_hls_master(hls_url))

    streams = [s for s in streams if s.get("format") in ("hls", "mp4") and _is_playable_url(s.get("url"))]

    seen: set[tuple[str, str, str]] = set()
    unique_streams: list[dict[str, Any]] = []
    for s in streams:
        key = (s.get("url", ""), s.get("quality", ""), s.get("format", ""))
        if key in seen:
            continue
        seen.add(key)
        unique_streams.append(s)
    streams = unique_streams

    def quality_score(s: dict[str, Any]) -> int:
        q = str(s.get("quality", ""))
        if "2160" in q or "4k" in q.lower():
            score = 2160
        elif "1080" in q:
            score = 1080
        elif "720" in q:
            score = 720
        elif "480" in q:
            score = 480
        elif "240" in q:
            score = 240
        elif "144" in q:
            score = 144
        elif q == "adaptive":
            score = 720
        else:
            score = 0
        if s.get("format") == "hls":
            score += 1
        return score

    streams.sort(key=quality_score, reverse=True)

    default_url: Optional[str] = None
    if streams:
        default_url = streams[0]["url"]
    elif hls_url:
        default_url = hls_url

    return {
        "streams": streams,
        "default": default_url,
        "has_video": bool(streams),
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
                f"{root.rstrip('/')}/{page}",
                f"{root}newest/{page}/",
                f"{root}newest/{page}",
                f"{root}videos?page={page}",
            ]
        )

        # Use pagination template from page 1 when available (category/search pages).
        try:
            seed_html = await fetch_html(root)
            seed_data = _extract_initials_data(seed_html)
            pag_url = _pagination_url_from_initials(seed_data, page)
            if pag_url:
                candidates.insert(0, pag_url)
        except Exception:
            pass

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

    data = _extract_initials_data(html)
    video_props = _collect_video_thumb_props(data) if data else []

    if video_props:
        for vid in video_props:
            url = vid.get("pageURL")
            if not url or not _is_video_list_url(str(url)):
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
            if not href or not _is_video_list_url(href):
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

    if effective_limit is not None:
        return items[:effective_limit]
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