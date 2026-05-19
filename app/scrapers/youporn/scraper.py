from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.youporn.com"
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{_BASE_URL}/",
    # Desktop layout + age gate (required since 2024 site changes)
    "Cookie": "platform=pc; age_verified=1",
}


def can_handle(host: str) -> bool:
    return "youporn.com" in host.lower()


def _best_image_url(img: Any) -> Optional[str]:
    """Extract the best image URL from an img element, checking multiple lazy-load attributes."""
    if img is None:
        return None

    video_fallback = None

    for attr in (
        "data-poster",
        "data-src",
        "data-thumb_url",
        "data-original",
        "data-lazy",
        "data-image",
        "src",
        "data-mediabook",
    ):
        value = img.get(attr)
        if not value:
            continue
        url = str(value).strip()
        if not url or "data:image" in url:
            continue

        url_lower = url.lower()
        is_video = any(
            url_lower.endswith(ext) or f"{ext}/" in url_lower or f"{ext}?" in url_lower
            for ext in (".mp4", ".webm", ".m3u8", ".ts")
        )

        if is_video:
            if not video_fallback:
                video_fallback = url
            continue

        return url

    return video_fallback


def _parse_balanced_json(html: str, start: int) -> Any | None:
    """Parse a JSON object or array starting at `start` (must point to { or [)."""
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


def _extract_playervars(html: str) -> dict[str, Any]:
    m = re.search(r"\bplayervars\s*[=:]\s*\{", html)
    if m:
        obj = _parse_balanced_json(html, m.end() - 1)
        if isinstance(obj, dict):
            return obj

    m = re.search(r"var\s+page_params\s*=\s*\{", html)
    if m:
        page_params = _parse_balanced_json(html, m.end() - 1)
        if isinstance(page_params, dict):
            setup = page_params.get("video_player_setup", {})
            if isinstance(setup, dict):
                pv = setup.get("playervars", {})
                if isinstance(pv, dict):
                    return pv

    return {}


def _is_blocked_page(html: str) -> bool:
    lower = html.lower()
    return (
        "disable access to our website" in lower
        or ("age verification" in lower and "virginia" in lower)
    )


def _is_watch_page_unavailable(html: str) -> bool:
    """Detect deactivated / removed watch pages (no player vars)."""
    if _is_blocked_page(html):
        return True
    if "id=\"watch-container\"" in html or "id='watch-container'" in html:
        return False
    return "playervars" not in html and "mediaDefinitions" not in html


def _quality_sort_key(stream: dict) -> int:
    q = stream.get("quality", "")
    digits = "".join(filter(str.isdigit, str(q)))
    return int(digits) if digits else 0


def _normalize_quality(quality: Any, video_url: str, fmt: str) -> str:
    if isinstance(quality, list):
        quality = str(quality[0]) if quality else ""
    elif isinstance(quality, int):
        quality = str(quality)

    if quality and str(quality).isdigit():
        return f"{quality}p"

    if quality and str(quality) not in ("unknown", "adaptive", "hls"):
        return str(quality)

    if fmt == "hls" or ".m3u8" in video_url:
        m_q = re.search(r"/(\d{3,4})[pP]?/", video_url) or re.search(r"(\d{3,4})[pP]_", video_url)
        if m_q:
            return f"{m_q.group(1)}p"
        return "adaptive"

    return str(quality) if quality else "unknown"


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_html(url: str) -> str:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(20.0, connect=20.0),
        headers=_DEFAULT_HEADERS,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def _resolve_proxy_url(proxy_url: str, expected_format: str | None = None) -> list[dict]:
    """
    Resolve a YouPorn proxy URL (e.g., /media/mp4/?s=...) to actual CDN streams.
    """
    if proxy_url.startswith("/"):
        proxy_url = urljoin(_BASE_URL, proxy_url)

    try:
        headers = {
            "User-Agent": _DEFAULT_HEADERS["User-Agent"],
            "Accept": "application/json",
            "Referer": _DEFAULT_HEADERS["Referer"],
        }
        async with httpx.AsyncClient(
            headers=headers, timeout=15.0, follow_redirects=True
        ) as client:
            resp = await client.get(proxy_url)
            if resp.status_code != 200:
                return []

            data = resp.json()
            if isinstance(data, dict):
                if "videoUrl" in data:
                    data = [data]
                else:
                    return []

            if not isinstance(data, list):
                return []

            streams: list[dict] = []
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
                stream_fmt = "hls" if fmt == "hls" or ".m3u8" in video_url else "mp4"

                streams.append(
                    {
                        "quality": _normalize_quality(quality, video_url, stream_fmt),
                        "url": video_url,
                        "format": stream_fmt,
                    }
                )
            return streams
    except Exception as exc:
        logger.debug("YouPorn proxy resolve failed for %s: %s", proxy_url, exc)

    return []


def _is_proxy_media_url(url: str) -> bool:
    return "/media/" in url and "?s=" in url


def _extract_video_streams(html: str) -> dict[str, Any]:
    streams: list[dict] = []
    hls_url = None

    playervars = _extract_playervars(html)
    media_defs = playervars.get("mediaDefinitions", [])

    if not media_defs:
        m = re.search(r'mediaDefinitions["\']?\s*:\s*\[', html)
        if m:
            parsed = _parse_balanced_json(html, m.end() - 1)
            if isinstance(parsed, list):
                media_defs = parsed

    for md in media_defs:
        if not isinstance(md, dict):
            continue

        video_url = md.get("videoUrl")
        if not video_url:
            continue

        if video_url.endswith((".jpg", ".jpeg", ".png")):
            continue

        if video_url.startswith("/"):
            video_url = urljoin(_BASE_URL, video_url)

        fmt = md.get("format") or ("hls" if ".m3u8" in video_url else "mp4")
        quality = _normalize_quality(md.get("quality"), video_url, fmt)

        stream = {"quality": quality, "url": video_url, "format": fmt}
        if fmt == "hls" or ".m3u8" in video_url:
            stream["format"] = "hls"
            hls_url = video_url

        streams.append(stream)

    if not streams:
        soup = BeautifulSoup(html, "lxml")
        video = soup.find("video")
        if video:
            src = video.get("src")
            if src and not src.endswith((".jpg", ".jpeg", ".png")):
                streams.append({"quality": "unknown", "url": src, "format": "mp4"})
            for source in video.find_all("source"):
                src = source.get("src")
                type_ = source.get("type", "")
                if src and not src.endswith((".jpg", ".jpeg", ".png")):
                    fmt = "hls" if "mpegurl" in type_ or ".m3u8" in src else "mp4"
                    if fmt == "hls":
                        hls_url = src
                    streams.append({"quality": "unknown", "url": src, "format": fmt})

    default_url = hls_url or (streams[0]["url"] if streams else None)
    return {"streams": streams, "default": default_url, "has_video": len(streams) > 0}


def parse_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = None
    title_el = soup.select_one(".watchVideoTitle, h1.watchVideoTitle")
    if title_el:
        title = title_el.get_text(strip=True)
    if not title:
        meta_title = soup.find("meta", property="og:title")
        if meta_title:
            title = meta_title.get("content")
    if not title:
        t_tag = soup.find("title")
        if t_tag:
            title = t_tag.get_text(strip=True)

    if title:
        for suffix in (" - YouPorn", " | YouPorn", " - youporn.com"):
            title = title.replace(suffix, "")

    thumbnail = None
    meta_thumb = soup.find("meta", property="og:image")
    if meta_thumb:
        thumbnail = meta_thumb.get("content")
    if not thumbnail:
        m_poster = re.search(
            r'(?:imageurl\s*=|poster\s*:)\s*(["\'])(?P<thumb>.+?)\1',
            html,
            re.IGNORECASE,
        )
        if m_poster:
            thumbnail = m_poster.group("thumb")

    duration = None
    playervars = _extract_playervars(html)
    dur_secs = playervars.get("duration")
    if dur_secs is None:
        meta_dur = soup.find("meta", property="video:duration")
        if meta_dur:
            try:
                dur_secs = int(meta_dur.get("content"))
            except (TypeError, ValueError):
                dur_secs = None
    if dur_secs is not None:
        try:
            secs = int(dur_secs)
            m, s = divmod(secs, 60)
            h, m = divmod(m, 60)
            duration = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        except (TypeError, ValueError):
            pass

    views = None
    views_el = soup.select_one("[data-value]")
    if views_el and views_el.get("data-value"):
        views = str(views_el.get("data-value")).strip()
    if not views:
        text_blob = soup.get_text(" ", strip=True)
        m_views = re.search(r"([\d,.]+[KMkm]?)\s+views", text_blob, re.IGNORECASE)
        if m_views:
            views = m_views.group(1)

    uploader = None
    uploader_el = soup.select_one(".submitByLink, .submitter, .video-uploaded-by, .uploader-name")
    if uploader_el:
        uploader = uploader_el.get_text(strip=True).replace("Uploaded by:", "").strip()

    tags: list[str] = []
    for t in soup.select(
        ".tagBoxContent a, .categories-wrapper a, .tags-wrapper a, .video-tags a"
    ):
        txt = t.get_text(strip=True)
        if txt:
            tags.append(txt)

    video_data = _extract_video_streams(html)

    return {
        "url": url,
        "title": title,
        "description": None,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": uploader,
        "category": "YouPorn",
        "tags": tags,
        "video": video_data,
        "related_videos": [],
        "preview_url": None,
    }


async def scrape(url: str) -> dict[str, Any]:
    html = await fetch_html(url)

    if _is_watch_page_unavailable(html):
        raise ValueError("YouPorn returned a geo-blocked or unavailable page")

    result = parse_page(html, url)

    video_data = result.get("video", {})
    streams: list[dict] = video_data.get("streams", [])

    for stream in streams[:]:
        stream_url = stream.get("url", "")
        if not _is_proxy_media_url(stream_url):
            continue

        resolved = await _resolve_proxy_url(stream_url, expected_format=stream.get("format"))
        if resolved:
            streams.remove(stream)
            streams.extend(resolved)

    streams.sort(key=_quality_sort_key, reverse=True)
    video_data["streams"] = streams
    video_data["has_video"] = bool(streams)

    if streams:
        hls_stream = next((s for s in streams if s.get("format") == "hls"), None)
        video_data["default"] = hls_stream["url"] if hls_stream else streams[0]["url"]
    else:
        video_data["default"] = None

    return result


def _listing_container(el: Any) -> Any:
    """Walk up to a card wrapper that holds thumb, duration, and views."""
    node = el
    for _ in range(6):
        if node is None:
            break
        if getattr(node, "select_one", None) and node.select_one(
            "img, .duration, .tm_video_duration, .info-views, .video-infos"
        ):
            return node
        node = getattr(node, "parent", None)
    return el.parent if el else None


async def list_videos(base_url: str, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    url = base_url.rstrip("/")
    if page > 1:
        sep = "&" if "?" in url else "?"
        url += f"{sep}page={page}"

    try:
        html = await fetch_html(url)
    except Exception:
        return []

    if _is_blocked_page(html):
        logger.warning("YouPorn listing blocked or empty for %s", url)
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add_item(
        href: str,
        title: str | None,
        thumb: str | None,
        container: Any,
    ) -> None:
        if len(items) >= limit:
            return
        if not href or "/watch/" not in href:
            return

        if not href.startswith("http"):
            href = urljoin(_BASE_URL, href)
        href = href.split("?")[0].rstrip("/")
        if href in seen:
            return
        if not thumb:
            return

        seen.add(href)

        duration = "0:00"
        if container is not None:
            dur_el = container.select_one(
                ".duration, .tm_video_duration, .video-duration, .video-duration-text"
            )
            if dur_el:
                duration = dur_el.get_text(strip=True) or duration

        views = "0"
        if container is not None:
            views_el = container.select_one(
                ".info-views, .video-infos, .video-views, .tm_video_views"
            )
            if views_el:
                info_txt = views_el.get_text(strip=True)
                m = re.search(r"([\d,.]+[KMkm]?)", info_txt)
                if m:
                    views = m.group(1)
                elif info_txt:
                    views = info_txt.replace("views", "").strip()

        uploader = "YouPorn"
        if container is not None:
            submitter = container.select_one(
                ".submitter, .video-uploaded-by, .author-title-text, .owner-name"
            )
            if submitter:
                uploader_text = submitter.get_text(strip=True)
                if uploader_text:
                    uploader = uploader_text.replace("Uploaded by:", "").strip()

        items.append(
            {
                "url": href,
                "title": title or "Unknown",
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": uploader,
            }
        )

    # Modern layout (2024+): a.video-title links
    for link in soup.select("a.video-title[href*='/watch/']"):
        if len(items) >= limit:
            break
        try:
            href = link.get("href", "")
            title = link.get("title") or link.get_text(strip=True)
            container = _listing_container(link)
            img = container.select_one("img") if container else link.find("img")
            thumb = _best_image_url(img)
            _add_item(href, title, thumb, container)
        except Exception as exc:
            logger.debug("YouPorn list parse (video-title): %s", exc)

    # Legacy layout: .video-box cards
    if len(items) < limit:
        for box in soup.select(".video-box"):
            if len(items) >= limit:
                break
            try:
                link = box.select_one("a[href*='/watch/']") or box.select_one("a")
                if not link:
                    continue

                href = link.get("href", "")
                img = box.select_one("img")
                thumb = _best_image_url(img)

                title = None
                title_div = box.select_one(
                    ".video-title, .tm_video_title, .video-box-title, a.video-title"
                )
                if title_div:
                    title = title_div.get_text(strip=True)
                if not title:
                    title = link.get("title")
                if not title and img:
                    title = img.get("alt")

                _add_item(href, title, thumb, box)
            except Exception as exc:
                logger.debug("YouPorn list parse (video-box): %s", exc)

    # Fallback: thumb links used on some browse pages
    if len(items) < limit:
        for link in soup.select("a[href*='/watch/']"):
            if len(items) >= limit:
                break
            try:
                href = link.get("href", "")
                if not href:
                    continue
                img = link.find("img")
                if not img:
                    continue
                thumb = _best_image_url(img)
                title = link.get("title") or (img.get("alt") if img else None)
                container = _listing_container(link)
                _add_item(href, title, thumb, container)
            except Exception as exc:
                logger.debug("YouPorn list parse (watch link): %s", exc)

    return items[:limit]
