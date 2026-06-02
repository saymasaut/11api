from __future__ import annotations

import asyncio
import importlib
import ipaddress
import logging
import os
import re
import socket
from io import StringIO
from types import ModuleType
from typing import Any, Optional, Type
from urllib.parse import urlparse

from fastapi import HTTPException

from app.models.downloader_schemas import ExtractResponse, FormatItem, MetadataBlock

logger = logging.getLogger(__name__)

_ytdlp_module: ModuleType | None = None
_ytdlp_download_error: Type[BaseException] | None = None
_ytdlp_extractor_error: Type[BaseException] | None = None
_gallery_dl_module: ModuleType | None = None
_gallery_dl_checked = False


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


def _ensure_gallery_dl() -> ModuleType | None:
    """Load gallery-dl once for fallback extraction (galleries, some social posts)."""
    global _gallery_dl_module, _gallery_dl_checked
    if _gallery_dl_checked:
        return _gallery_dl_module
    _gallery_dl_checked = True
    try:
        _gallery_dl_module = importlib.import_module("gallery_dl")
    except ImportError as e:
        logger.warning("gallery-dl not installed: %s", e)
        _gallery_dl_module = None
    return _gallery_dl_module


def _apply_gallery_cookies(config_mod: ModuleType) -> None:
    cookies_file = os.environ.get("GALLERY_DL_COOKIES_FILE", "").strip()
    if not cookies_file:
        cookies_file = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookies_file and os.path.isfile(cookies_file):
        config_mod.set(("extractor",), "cookies", cookies_file)


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
    "pixiv": "Pixiv",
    "tumblr": "Tumblr",
    "deviantart": "DeviantArt",
    "pinterest": "Pinterest",
}

_VIDEO_EXTENSIONS = frozenset({"mp4", "webm", "mkv", "mov", "m4v", "gifv"})


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

    return ExtractResponse(
        status="success",
        metadata=metadata,
        formats=formats,
        default_format_id=default_id,
        http_headers=headers,
        filename_hint=filename_hint,
    )


def _ext_from_url(url: str) -> Optional[str]:
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1]
    if "." in name:
        return name.rsplit(".", 1)[-1].lower()
    return None


def _gallery_format_label(kw: dict[str, Any], ext: Optional[str], index: int) -> str:
    parts: list[str] = []
    num = kw.get("num")
    count = kw.get("count")
    if num and count:
        parts.append(f"{num}/{count}")
    height = kw.get("height")
    if height:
        parts.append(f"{height}p")
    if ext:
        parts.append(str(ext).upper())
    return " · ".join(parts) if parts else f"Item {index + 1}"


def _pick_gallery_default_format(formats: list[FormatItem]) -> Optional[str]:
    if not formats:
        return None
    for f in formats:
        if (f.ext or "").lower() in _VIDEO_EXTENSIONS:
            return f.format_id
    best_id: Optional[str] = None
    best_height = -1
    for f in formats:
        if f.resolution and f.resolution.endswith("p"):
            try:
                h = int(f.resolution[:-1])
            except ValueError:
                continue
            if h > best_height:
                best_height = h
                best_id = f.format_id
    return best_id or formats[0].format_id


def _map_gallery_exception(exc: BaseException) -> HTTPException:
    name = exc.__class__.__name__
    msg = str(exc).lower()
    if name in (
        "AuthRequired",
        "AuthenticationError",
        "AuthorizationError",
        "ChallengeError",
    ):
        return HTTPException(status_code=403, detail="private_content")
    if name == "NotFoundError" or "404" in msg:
        return HTTPException(status_code=400, detail="unsupported_url")
    if "429" in msg or "rate" in msg:
        return HTTPException(status_code=429, detail="rate_limited")
    if name == "NoExtractorError":
        return HTTPException(status_code=400, detail="unsupported_url")
    return HTTPException(status_code=400, detail="unsupported_url")


def _map_gallery_to_response(data_job: Any, original_url: str) -> ExtractResponse:
    urls: list[str] = list(data_job.data_urls or [])
    metas: list[dict[str, Any]] = list(data_job.data_meta or [])
    post_meta: dict[str, Any] = {}
    for entry in data_job.data_post or []:
        if isinstance(entry, dict):
            post_meta.update(entry)

    extractor = getattr(data_job, "extractor", None)
    category = getattr(extractor, "category", None) or "gallery-dl"
    subcategory = getattr(extractor, "subcategory", None)
    extractor_key = f"{category}:{subcategory}" if subcategory else str(category)

    formats: list[FormatItem] = []
    thumbs: list[str] = []

    for i, media_url in enumerate(urls):
        kw = metas[i] if i < len(metas) else {}
        ext = kw.get("extension") or _ext_from_url(media_url)
        height = kw.get("height")
        resolution = f"{height}p" if height else None
        formats.append(
            FormatItem(
                format_id=str(i),
                label=_gallery_format_label(kw, ext, i),
                ext=ext,
                resolution=resolution,
                filesize=kw.get("filesize") or kw.get("size"),
                mode="single",
                url=media_url,
            )
        )
        if ext and ext.lower() not in _VIDEO_EXTENSIONS:
            thumbs.append(media_url)

    first_kw = metas[0] if metas else {}
    title = (
        post_meta.get("title")
        or first_kw.get("title")
        or post_meta.get("description")
        or first_kw.get("description")
        or first_kw.get("filename")
    )
    uploader = (
        post_meta.get("author")
        or post_meta.get("username")
        or first_kw.get("author")
        or first_kw.get("username")
        or first_kw.get("user")
    )
    thumb = post_meta.get("thumbnail") or first_kw.get("thumbnail")
    if not thumb and thumbs:
        thumb = thumbs[0]

    tags_raw = post_meta.get("tags") or first_kw.get("tags") or []
    tags = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []

    metadata = MetadataBlock(
        title=str(title) if title else None,
        full_title=str(title) if title else None,
        thumbnail=str(thumb) if thumb else None,
        thumbnails=thumbs[:12] if thumbs else ([str(thumb)] if thumb else []),
        description=_short_description(
            post_meta.get("description") or first_kw.get("description")
        ),
        uploader=str(uploader) if uploader else None,
        channel=str(uploader) if uploader else None,
        webpage_url=original_url,
        original_url=original_url,
        extractor=str(category),
        extractor_key=extractor_key,
        platform=_platform_label(str(category)),
        tags=tags,
        keywords=tags,
    )

    default_id = _pick_gallery_default_format(formats)
    if default_id:
        for f in formats:
            f.is_default = f.format_id == default_id

    headers: dict[str, str] = {}
    extractor_lower = str(category).lower()
    if "instagram" in extractor_lower:
        headers.setdefault("Referer", "https://www.instagram.com/")
        headers.setdefault("Origin", "https://www.instagram.com")
    elif "twitter" in extractor_lower or category == "twitter":
        headers.setdefault("Referer", "https://twitter.com/")
        headers.setdefault("Origin", "https://twitter.com")

    filename_hint = metadata.title or "media"
    safe = re.sub(r'[/\\:*?"<>|]+', "_", str(filename_hint)).strip()
    filename_hint = safe[:120] if safe else "media"

    return ExtractResponse(
        status="success",
        metadata=metadata,
        formats=formats,
        default_format_id=default_id,
        http_headers=headers,
        filename_hint=filename_hint,
    )


def _extract_gallery_dl(url: str) -> ExtractResponse:
    if _ensure_gallery_dl() is None:
        raise HTTPException(status_code=503, detail="gallery_dl_not_installed")

    config_mod = importlib.import_module("gallery_dl.config")
    job_mod = importlib.import_module("gallery_dl.job")

    config_mod.load()
    _apply_gallery_cookies(config_mod)

    data_job = job_mod.DataJob(url, file=StringIO())
    try:
        data_job.run()
    except Exception as e:
        logger.exception("gallery-dl extract failed for %s", url)
        raise _map_gallery_exception(e) from e

    if data_job.exception:
        raise _map_gallery_exception(data_job.exception)

    if not data_job.data_urls:
        raise HTTPException(status_code=400, detail="unsupported_url")

    return _map_gallery_to_response(data_job, url)


def _extract_ytdlp(url: str) -> ExtractResponse:
    ytdlp = _ensure_ytdlp()
    download_error = _ytdlp_download_error or Exception
    extractor_error = _ytdlp_extractor_error or Exception

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
        raise HTTPException(status_code=400, detail="unsupported_url") from e
    except download_error as e:
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


def _extract_sync(url: str) -> ExtractResponse:
    """Try yt-dlp first, then gallery-dl when yt-dlp fails or returns no formats."""
    ytdlp_error: HTTPException | None = None
    ytdlp_result: ExtractResponse | None = None

    try:
        ytdlp_result = _extract_ytdlp(url)
        if ytdlp_result.formats:
            return ytdlp_result
        logger.info("yt-dlp returned no formats for %s, trying gallery-dl", url)
    except HTTPException as e:
        ytdlp_error = e
        logger.info("yt-dlp failed for %s (%s), trying gallery-dl", url, e.detail)

    try:
        gallery_result = _extract_gallery_dl(url)
        if gallery_result.formats:
            return gallery_result
    except HTTPException as gallery_error:
        if gallery_error.detail == "gallery_dl_not_installed" and ytdlp_error:
            raise ytdlp_error from gallery_error
        if ytdlp_error:
            raise ytdlp_error from gallery_error
        raise gallery_error

    if ytdlp_result and ytdlp_result.formats:
        return ytdlp_result
    if ytdlp_error:
        raise ytdlp_error
    raise HTTPException(status_code=400, detail="unsupported_url")


async def extract_video(url: str) -> ExtractResponse:
    safe_url = _validate_url(url)
    return await asyncio.to_thread(_extract_sync, safe_url)
