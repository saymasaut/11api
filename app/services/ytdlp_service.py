from __future__ import annotations

import asyncio
import importlib
import ipaddress
import logging
import os
import re
import socket
from types import ModuleType
from typing import Any, Optional, Type
from urllib.parse import urlparse

from fastapi import HTTPException

from app.models.downloader_schemas import ExtractResponse, FormatItem, MetadataBlock

logger = logging.getLogger(__name__)

_ytdlp_module: ModuleType | None = None
_ytdlp_download_error: Type[BaseException] | None = None
_ytdlp_extractor_error: Type[BaseException] | None = None


def _ensure_ytdlp() -> ModuleType:
    """Load yt-dlp once; raise 503 if the package is missing on the server."""
    global _ytdlp_module, _ytdlp_download_error, _ytdlp_extractor_error
    if _ytdlp_module is not None:
        return _ytdlp_module
    try:
        _ytdlp_module = importlib.import_module("yt_dlp")
        utils = importlib.import_module("yt_dlp.utils")
        _ytdlp_download_error = utils.DownloadError
        _ytdlp_extractor_error = utils.ExtractorError
    except ImportError as e:
        logger.error("yt-dlp not installed: %s", e)
        raise HTTPException(
            status_code=503,
            detail="ytdlp_not_installed",
        ) from e
    return _ytdlp_module

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


_CONTAINER_DISPLAY: dict[str, str] = {
    "m3u8": "M3U8",
    "mpd": "DASH",
    "mp4": "MP4",
    "webm": "WEBM",
    "mkv": "MKV",
    "mov": "MOV",
    "flv": "FLV",
    "mpegts": "TS",
    "ts": "TS",
    "m4a": "M4A",
    "mp3": "MP3",
    "aac": "AAC",
    "opus": "OPUS",
}


def _container_display_name(container: str) -> str:
    key = container.lower().strip()
    return _CONTAINER_DISPLAY.get(key, key.upper())


def _container_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    lower = url.lower().split("?", 1)[0]
    if ".m3u8" in lower or lower.endswith("m3u8"):
        return "m3u8"
    if ".mpd" in lower or lower.endswith("mpd"):
        return "mpd"
    for ext in ("webm", "mkv", "mp4", "mov", "flv", "m4a", "mp3"):
        if f".{ext}" in lower:
            return ext
    if "/hls/" in lower or "playlist.m3u" in lower:
        return "m3u8"
    return None


def _detect_container_ext(fmt: dict[str, Any]) -> str:
    """Resolve real container; yt-dlp often reports ext=mp4 for HLS/DASH manifests."""
    url = str(fmt.get("url") or "")
    protocol = str(fmt.get("protocol") or "").lower()
    ext = str(fmt.get("ext") or "").lower().strip()
    container = str(fmt.get("container") or "").lower().strip()

    from_url = _container_from_url(url)
    if from_url:
        return from_url

    if container in _CONTAINER_DISPLAY:
        return container

    if "m3u8" in protocol:
        return "m3u8"
    if "dash" in protocol or "mpd" in protocol:
        return "mpd"

    if ext == "mpegts" and ("m3u8" in url.lower() or "m3u8" in protocol):
        return "m3u8"
    if ext in _CONTAINER_DISPLAY:
        return ext
    if ext:
        return ext
    return "mp4"


def _resolution_label(fmt: dict[str, Any]) -> Optional[str]:
    height = fmt.get("height")
    if height:
        return f"{int(height)}p"
    width = fmt.get("width")
    if width:
        w = int(width)
        if w >= 3840:
            return "2160p"
        if w >= 2560:
            return "1440p"
        if w >= 1920:
            return "1080p"
        if w >= 1280:
            return "720p"
        if w >= 854:
            return "480p"
        if w >= 640:
            return "360p"
        return f"{w}w"
    return None


def _bitrate_kbps(fmt: dict[str, Any]) -> Optional[int]:
    for key in ("abr", "tbr", "vbr"):
        val = fmt.get(key)
        if val is not None:
            try:
                return int(round(float(val)))
            except (TypeError, ValueError):
                continue
    return None


def _audio_codec_label(acodec: Optional[str]) -> Optional[str]:
    if not acodec or acodec == "none":
        return None
    lower = acodec.lower()
    if lower.startswith("mp4a") or lower in ("aac", "m4a"):
        return "AAC"
    if "opus" in lower:
        return "Opus"
    if "mp3" in lower or lower == "mp3":
        return "MP3"
    if "vorbis" in lower:
        return "Vorbis"
    if "flac" in lower:
        return "FLAC"
    return None


def _video_quality_label(fmt: dict[str, Any]) -> str:
    parts: list[str] = []
    res = _resolution_label(fmt)
    if res:
        parts.append(res)

    container = _container_display_name(_detect_container_ext(fmt))
    parts.append(container)

    fps = fmt.get("fps")
    if fps and float(fps) >= 50:
        parts.append(f"{int(round(float(fps)))}fps")

    return " · ".join(parts)


def _audio_quality_label(fmt: dict[str, Any]) -> str:
    parts: list[str] = []
    bitrate = _bitrate_kbps(fmt)
    if bitrate:
        parts.append(f"{bitrate} kbps")

    codec = _audio_codec_label(fmt.get("acodec"))
    if codec:
        parts.append(codec)
    else:
        parts.append(_container_display_name(_detect_container_ext(fmt)))

    return " · ".join(parts) if parts else "Audio"


def _merge_quality_label(best_video: dict[str, Any]) -> str:
    res = _resolution_label(best_video)
    if res:
        return f"{res} · MP4 (merge)"
    return "Best · MP4 (merge)"


def _pick_default_format(formats: list[FormatItem]) -> Optional[str]:
    if not formats:
        return None
    for f in formats:
        if f.is_default:
            return f.format_id
    for f in formats:
        if f.mode == "single" and (f.ext or "").lower() in ("mp4", "mkv", "webm"):
            return f.format_id
    for f in formats:
        if f.mode == "single" and (f.ext or "").lower() not in ("m3u8", "mpd"):
            return f.format_id
    return formats[0].format_id


def _sanitize_error_message(msg: str, max_len: int = 200) -> str:
    cleaned = re.sub(r"\s+", " ", str(msg or "")).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"


def _normalize_downloader_url(url: str) -> str:
    """Normalize hosts and short/share links for yt-dlp."""
    raw = url.strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host == "x.com":
        return raw.replace("://x.com", "://twitter.com", 1)
    if host == "mobile.twitter.com":
        return raw.replace("://mobile.twitter.com", "://twitter.com", 1)
    if host == "redd.it":
        post_id = parsed.path.strip("/").split("/")[0]
        if post_id:
            return f"https://www.reddit.com/comments/{post_id}/"
    if "reddit.com" in host and re.search(r"/s/[A-Za-z0-9]+", parsed.path):
        return raw
    return raw


def _is_storyboard_format(fmt: dict[str, Any], fid: str) -> bool:
    fid_l = fid.lower()
    if fid_l.startswith("sb") or "storyboard" in fid_l:
        return True
    note = str(fmt.get("format_note") or "").lower()
    return "storyboard" in note or ("preview" in note and not fmt.get("height"))


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
        if _is_storyboard_format(fmt, fid):
            continue

        url = fmt.get("url")
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        has_video = bool(vcodec and vcodec != "none")
        has_audio = bool(acodec and acodec != "none")

        if not url:
            continue

        # Twitter/X often omits codec fields on progressive MP4 URLs.
        if not has_video and not has_audio:
            fid_l = fid.lower()
            if "audio" in fid_l:
                has_audio = True
            elif fmt.get("height") or fmt.get("width") or "http" in fid_l:
                has_video = True
            else:
                has_video = True

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

        container = _detect_container_ext(fmt)
        seen.add(fid)
        if mode == "audio_only":
            label = _audio_quality_label(fmt)
            resolution = None
        else:
            label = _video_quality_label(fmt)
            resolution = _resolution_label(fmt)

        items.append(
            FormatItem(
                format_id=fid,
                label=label,
                ext=container,
                resolution=resolution,
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
            merge_out = "mp4"
            label = _merge_quality_label(best_video)
            items.insert(
                0,
                FormatItem(
                    format_id=pair_id,
                    label=label,
                    ext=merge_out,
                    resolution=_resolution_label(best_video),
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
        # Prefer progressive MP4 over HLS/DASH manifests when both exist.
        for f in items:
            if f.mode == "single" and (f.ext or "").lower() == "mp4":
                f.is_default = True
                break
        if not any(f.is_default for f in items):
            for f in items:
                if f.mode == "single" and (f.ext or "").lower() not in (
                    "m3u8",
                    "mpd",
                ):
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

    # YouTube CDN (googlevideo) requires Referer / UA matching the watch page.
    extractor_lower = str(extractor_key or "").lower()
    if "youtube" in extractor_lower:
        headers.setdefault("Referer", "https://www.youtube.com/")
        headers.setdefault("Origin", "https://www.youtube.com")
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        )
    if "twitter" in extractor_lower:
        headers.setdefault("Referer", "https://twitter.com/")
        headers.setdefault("Origin", "https://twitter.com")

    # Generic sites: origin of the page URL helps many CDNs accept the download.
    webpage = metadata.webpage_url or original_url
    if webpage and "Referer" not in headers:
        try:
            parsed = urlparse(str(webpage))
            if parsed.scheme and parsed.netloc:
                origin = f"{parsed.scheme}://{parsed.netloc}"
                headers.setdefault("Referer", f"{origin}/")
        except Exception:
            pass

    if not formats:
        raise HTTPException(status_code=400, detail="no_formats")

    return ExtractResponse(
        status="success",
        metadata=metadata,
        formats=formats,
        default_format_id=default_id,
        http_headers=headers,
        filename_hint=filename_hint,
    )


def _extract_sync(url: str) -> ExtractResponse:
    ytdlp = _ensure_ytdlp()
    download_error = _ytdlp_download_error or Exception
    extractor_error = _ytdlp_extractor_error or Exception

    url = _normalize_downloader_url(url)
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
        with ytdlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except extractor_error as e:
        msg = str(e).lower()
        if "private" in msg or "login" in msg or "sign in" in msg:
            raise HTTPException(status_code=403, detail="private_content") from e
        if "geo" in msg or "country" in msg or "blocked" in msg:
            raise HTTPException(status_code=451, detail="geo_blocked") from e
        if "429" in msg or "rate" in msg:
            raise HTTPException(status_code=429, detail="rate_limited") from e
        logger.warning("yt-dlp extractor error for %s: %s", url, e)
        raise HTTPException(
            status_code=400,
            detail=_sanitize_error_message(str(e)),
        ) from e
    except download_error as e:
        msg = str(e).lower()
        if "429" in msg or "rate" in msg:
            raise HTTPException(status_code=429, detail="rate_limited") from e
        logger.warning("yt-dlp download error for %s: %s", url, e)
        raise HTTPException(
            status_code=400,
            detail=_sanitize_error_message(str(e)),
        ) from e
    except Exception as e:
        logger.exception("yt-dlp extract failed for %s", url)
        raise HTTPException(
            status_code=500,
            detail=_sanitize_error_message(str(e) or "extract_failed"),
        ) from e

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
