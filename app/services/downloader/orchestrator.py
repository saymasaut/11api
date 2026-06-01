"""Route extract/resolve/download to yt-dlp, gallery-dl, or pytubefix."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from app.models.downloader_schemas import DownloaderFormatItem
from app.services import ytdlp_service
from app.services.downloader import gallery_dl_backend, pytubefix_backend
from app.config.settings import settings
from app.services.downloader.format_codec import (
    BACKEND_GALLERY_DL,
    BACKEND_PYTUBEFIX,
    BACKEND_YTDLP,
    join_format_id,
    split_format_id,
)
from app.services.downloader_exceptions import DownloaderApiError

logger = logging.getLogger(__name__)


def _prefix_ytdlp_formats(data: dict[str, Any]) -> None:
    formats = data.get("formats") or []
    prefixed: list[DownloaderFormatItem] = []
    for item in formats:
        if isinstance(item, DownloaderFormatItem):
            item.format_id = join_format_id(BACKEND_YTDLP, item.format_id)
            prefixed.append(item)
        elif isinstance(item, dict):
            item = dict(item)
            item["format_id"] = join_format_id(BACKEND_YTDLP, item.get("format_id", ""))
            prefixed.append(DownloaderFormatItem(**item))
    data["formats"] = prefixed


def _merge_extract_results(
    primary: dict[str, Any],
    *others: Optional[dict[str, Any]],
) -> dict[str, Any]:
    formats: list[DownloaderFormatItem] = list(primary.get("formats") or [])
    seen = {f.format_id for f in formats}
    extractors = [primary.get("extractor") or BACKEND_YTDLP]

    for extra in others:
        if not extra:
            continue
        ex_name = extra.get("extractor")
        if ex_name:
            extractors.append(ex_name)
        for item in extra.get("formats") or []:
            if isinstance(item, DownloaderFormatItem):
                fmt = item
            else:
                fmt = DownloaderFormatItem(**item)
            if fmt.format_id and fmt.format_id not in seen:
                seen.add(fmt.format_id)
                formats.append(fmt)

    primary["formats"] = formats
    primary["extractor"] = ", ".join(dict.fromkeys(extractors))
    return primary


def extract_info(url: str) -> dict[str, Any]:
    """Try yt-dlp, then merge gallery-dl / pytubefix formats when applicable."""
    last_error: Optional[DownloaderApiError] = None
    primary: Optional[dict[str, Any]] = None

    try:
        primary = ytdlp_service.extract_info(url)
        _prefix_ytdlp_formats(primary)
    except DownloaderApiError as e:
        last_error = e
        logger.info("yt-dlp extract failed, trying other backends: %s", e.message)

    gallery_data = gallery_dl_backend.extract_info(url)
    pytube_data = pytubefix_backend.extract_info(url)

    if primary is None and not gallery_data and not pytube_data:
        if last_error:
            raise last_error
        raise DownloaderApiError(
            "No downloader backend could handle this URL",
            status_code=404,
            error_code="UNSUPPORTED_URL",
        )

    if primary is None:
        primary = gallery_data or pytube_data or {}
    else:
        primary = _merge_extract_results(primary, gallery_data, pytube_data)

    if not primary.get("formats"):
        primary["formats"] = [
            DownloaderFormatItem(
                format_id=join_format_id(BACKEND_YTDLP, p.format_id),
                ext=p.ext,
                resolution=p.resolution,
                format_note=p.format_note,
                needs_merge=p.needs_merge,
                has_video=p.has_video,
                has_audio=p.has_audio,
            )
            for p in ytdlp_service.get_preset_formats()
        ]

    return primary


def resolve_direct_url(url: str, composite_format_id: str) -> dict[str, Any]:
    backend, format_id = split_format_id(composite_format_id)
    if backend == BACKEND_GALLERY_DL:
        return gallery_dl_backend.resolve_direct_url(url, format_id)
    if backend == BACKEND_PYTUBEFIX:
        return pytubefix_backend.resolve_direct_url(url, format_id)
    return ytdlp_service.resolve_direct_url(url, format_id)


def download_with_format(
    url: str,
    composite_format_id: str,
    output_dir: Path,
    *,
    progress_callback: Optional[Callable[[float], None]] = None,
    cancel_event: Optional[Any] = None,
) -> Path:
    backend, format_id = split_format_id(composite_format_id)
    if backend == BACKEND_GALLERY_DL:
        return gallery_dl_backend.download_with_format(
            url,
            format_id,
            output_dir,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
    if backend == BACKEND_PYTUBEFIX:
        return pytubefix_backend.download_with_format(
            url,
            format_id,
            output_dir,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
    return ytdlp_service.download_with_format(
        url,
        format_id,
        output_dir,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )


def check_temp_dir_writable() -> bool:
    return ytdlp_service.check_temp_dir_writable()


def get_health() -> dict[str, Any]:
    backends: dict[str, Optional[str]] = {
        "yt-dlp": ytdlp_service.get_yt_dlp_version(),
        "gallery-dl": gallery_dl_backend.get_version(),
        "pytubefix": pytubefix_backend.get_version(),
    }
    ffmpeg_ok = ytdlp_service.is_ffmpeg_available()
    temp_ok = check_temp_dir_writable()
    any_core = backends["yt-dlp"] is not None
    extra = any(backends[k] for k in ("gallery-dl", "pytubefix"))

    if not any_core and not extra:
        status = "error"
        message = "No downloader libraries installed (yt-dlp, gallery-dl, pytubefix)"
    elif not temp_ok:
        status = "degraded"
        message = "Temp directory is not writable"
    elif not ffmpeg_ok:
        status = "degraded"
        message = "ffmpeg missing — merged/HLS downloads may fail"
    else:
        status = "ok"
        installed = [k for k, v in backends.items() if v]
        message = f"Ready: {', '.join(installed)}"

    return {
        "status": status,
        "yt_dlp_version": backends["yt-dlp"],
        "ffmpeg_available": ffmpeg_ok,
        "temp_dir_writable": temp_ok,
        "max_file_size_mb": settings.DOWNLOADER_MAX_FILE_MB,
        "message": message,
        "backends": backends,
    }
