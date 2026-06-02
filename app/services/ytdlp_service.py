from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import HTTPException

from app.models.downloader_schemas import ExtractResponse, FormatItem, MetadataBlock

logger = logging.getLogger(__name__)

_PLATFORM_LABELS: dict[str, str] = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "twitter": "X",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "reddit": "Reddit",
    "vimeo": "Vimeo",
    "dailymotion": "Dailymotion",
    "twitch": "Twitch",
}


def _validate_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="unsupported_url")
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="unsupported_url")

    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="unsupported_url")

    lowered = host.lower()
    if lowered in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(status_code=400, detail="unsupported_url")

    try:
        for info in socket.getaddrinfo(host, None):
            addr = info[4][0]
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                raise HTTPException(status_code=400, detail="unsupported_url")
    except socket.gaierror:
        pass

    return url.strip()


def _platform_label(extractor_key: Optional[str]) -> Optional[str]:
    if not extractor_key:
        return None
    key = extractor_key.lower()
    for part, label in _PLATFORM_LABELS.items():
        if part in key:
            return label
    return extractor_key.replace("_", " ").title()


def _short_description(text: Optional[str], max_len: int = 280) -> Optional[str]:
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"


def _format_label(fmt: dict[str, Any]) -> str:
    parts: list[str] = []
    height = fmt.get("height")
    if height:
        parts.append(f"{height}p")
    ext = fmt.get("ext")
    if ext:
        parts.append(str(ext).upper())
    note = fmt.get("format_note")
    if note and str(note) not in parts:
        parts.append(str(note))
    if not parts:
        fid = fmt.get("format_id")
        return str(fid) if fid else "Unknown"
    return " · ".join(parts)


def _pick_default_format(formats: list[FormatItem]) -> Optional[str]:
    if not formats:
        return None
    for f in formats:
        if f.is_default:
            return f.format_id
    for f in formats:
        if f.mode == "single" and f.ext in ("mp4", "mkv", "webm"):
            return f.format_id
    return formats[0].format_id


def _build_formats(info: dict[str, Any]) -> list[FormatItem]:
    raw_formats = info.get("formats") or []
    items: list[FormatItem] = []
    seen: set[str] = set()

    for fmt in raw_formats:
        if not isinstance(fmt, dict):
            continue
        fmt_id = fmt.get("format_id")
        if fmt_id is None:
            continue
        fid = str(fmt_id)
        if fid in seen:
            continue

        url = fmt.get("url")
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        has_video = vcodec and vcodec != "none"
        has_audio = acodec and acodec != "none"

        if not url and not (has_video or has_audio):
            continue

        mode: str
        video_url: Optional[str] = None
        audio_url: Optional[str] = None
        single_url: Optional[str] = url

        if has_video and has_audio and url:
            mode = "single"
        elif has_video and url:
            mode = "single"
        elif has_audio and not has_video and url:
            mode = "audio_only"
        else:
            continue

        seen.add(fid)
        items.append(
            FormatItem(
                format_id=fid,
                label=_format_label(fmt),
                ext=fmt.get("ext"),
                resolution=(
                    f"{fmt.get('height')}p" if fmt.get("height") else None
                ),
                filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
                vcodec=vcodec if vcodec != "none" else None,
                acodec=acodec if acodec != "none" else None,
                mode=mode,  # type: ignore[arg-type]
                url=single_url,
                video_url=video_url,
                audio_url=audio_url,
            )
        )

    # Try combined bestvideo+bestaudio pair for high quality
    best_video = None
    best_audio = None
    for fmt in raw_formats:
        if not isinstance(fmt, dict) or not fmt.get("url"):
            continue
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        if vcodec and vcodec != "none" and (acodec is None or acodec == "none"):
            h = fmt.get("height") or 0
            if best_video is None or h > (best_video.get("height") or 0):
                best_video = fmt
        if acodec and acodec != "none" and (vcodec is None or vcodec == "none"):
            abr = fmt.get("abr") or 0
            if best_audio is None or abr > (best_audio.get("abr") or 0):
                best_audio = fmt

    if best_video and best_audio:
        pair_id = f"{best_video.get('format_id')}+{best_audio.get('format_id')}"
        if pair_id not in seen:
            vh = best_video.get("height")
            label = f"{vh}p · MP4 (merge)" if vh else "Best quality (merge)"
            items.insert(
                0,
                FormatItem(
                    format_id=pair_id,
                    label=label,
                    ext="mp4",
                    resolution=f"{vh}p" if vh else None,
                    filesize=None,
                    vcodec=best_video.get("vcodec"),
                    acodec=best_audio.get("acodec"),
                    mode="separate",
                    video_url=best_video.get("url"),
                    audio_url=best_audio.get("url"),
                    is_default=True,
                ),
            )
            seen.add(pair_id)

    if items and not any(f.is_default for f in items):
        # Prefer single-file MP4
        for f in items:
            if f.mode == "single" and (f.ext or "").lower() == "mp4":
                f.is_default = True
                break
        if not any(f.is_default for f in items):
            items[0].is_default = True

    return items


def _map_info_to_response(info: dict[str, Any], original_url: str) -> ExtractResponse:
    extractor_key = info.get("extractor_key") or info.get("extractor")
    thumbs: list[str] = []
    thumb = info.get("thumbnail")
    if thumb:
        thumbs.append(str(thumb))
    for t in info.get("thumbnails") or []:
        if isinstance(t, dict) and t.get("url"):
            u = str(t["url"])
            if u not in thumbs:
                thumbs.append(u)

    tags = info.get("tags") or []
    categories = info.get("categories") or []
    keywords: list[str] = []
    if isinstance(tags, list):
        keywords.extend([str(t) for t in tags])
    if isinstance(categories, list):
        keywords.extend([str(c) for c in categories])

    metadata = MetadataBlock(
        title=info.get("title"),
        full_title=info.get("fulltitle") or info.get("title"),
        thumbnail=thumb,
        thumbnails=thumbs,
        description=_short_description(info.get("description")),
        uploader=info.get("uploader") or info.get("channel"),
        uploader_id=info.get("uploader_id"),
        channel=info.get("channel"),
        channel_id=info.get("channel_id"),
        duration=info.get("duration"),
        timestamp=info.get("timestamp"),
        upload_date=info.get("upload_date"),
        tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        categories=[str(c) for c in categories] if isinstance(categories, list) else [],
        keywords=keywords,
        webpage_url=info.get("webpage_url") or original_url,
        original_url=original_url,
        extractor=info.get("extractor"),
        extractor_key=extractor_key,
        platform=_platform_label(
            str(extractor_key) if extractor_key else None
        ),
    )

    formats = _build_formats(info)
    default_id = _pick_default_format(formats)

    headers: dict[str, str] = {}
    for key in ("http_headers", "request_headers"):
        raw = info.get(key)
        if isinstance(raw, dict):
            for k, v in raw.items():
                if v is not None:
                    headers[str(k)] = str(v)

    filename_hint = info.get("title") or "video"
    safe = re.sub(r'[/\\:*?"<>|]+', "_", str(filename_hint)).strip()
    filename_hint = safe[:120] if safe else "video"

    return ExtractResponse(
        status="success",
        metadata=metadata,
        formats=formats,
        default_format_id=default_id,
        http_headers=headers,
        filename_hint=filename_hint,
    )


def _extract_sync(url: str) -> ExtractResponse:
    try:
        import yt_dlp
        from yt_dlp.utils import DownloadError, ExtractorError
    except ImportError as e:
        logger.error("yt-dlp not installed: %s", e)
        raise HTTPException(status_code=503, detail="merge_unavailable") from e

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "retries": 3,
        "fragment_retries": 3,
    }

    cookies_file = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookies_file and os.path.isfile(cookies_file):
        opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except ExtractorError as e:
        msg = str(e).lower()
        if "private" in msg or "login" in msg or "sign in" in msg:
            raise HTTPException(status_code=403, detail="private_content") from e
        if "geo" in msg or "country" in msg or "blocked" in msg:
            raise HTTPException(status_code=451, detail="geo_blocked") from e
        if "429" in msg or "rate" in msg:
            raise HTTPException(status_code=429, detail="rate_limited") from e
        raise HTTPException(status_code=400, detail="unsupported_url") from e
    except DownloadError as e:
        msg = str(e).lower()
        if "429" in msg or "rate" in msg:
            raise HTTPException(status_code=429, detail="rate_limited") from e
        raise HTTPException(status_code=400, detail="unsupported_url") from e
    except Exception as e:
        logger.exception("yt-dlp extract failed for %s", url)
        raise HTTPException(status_code=500, detail="unsupported_url") from e

    if not info:
        raise HTTPException(status_code=400, detail="unsupported_url")

    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        first = next((e for e in entries if e), None)
        if not first:
            raise HTTPException(status_code=400, detail="unsupported_url")
        info = first

    return _map_info_to_response(info, url)


async def extract_video(url: str) -> ExtractResponse:
    safe_url = _validate_url(url)
    return await asyncio.to_thread(_extract_sync, safe_url)
