"""pytubefix backend — YouTube fallback (progressive + adaptive streams)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from app.models.downloader_schemas import DownloaderFormatItem
from app.services.downloader.format_codec import BACKEND_PYTUBEFIX, join_format_id
from app.services.downloader_exceptions import DownloaderApiError

logger = logging.getLogger(__name__)

_YT_RE = re.compile(
    r"^(https?://)?(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/",
    re.I,
)


def is_available() -> bool:
    try:
        from pytubefix import YouTube  # noqa: F401

        return True
    except ImportError:
        return False


def get_version() -> Optional[str]:
    try:
        import pytubefix

        return getattr(pytubefix, "__version__", None)
    except ImportError:
        return None


def is_youtube_url(url: str) -> bool:
    return bool(_YT_RE.match(url.strip()))


def extract_info(url: str) -> Optional[dict[str, Any]]:
    if not is_available() or not is_youtube_url(url):
        return None

    from pytubefix import YouTube
    from pytubefix.exceptions import PytubeFixError

    try:
        yt = YouTube(url, use_oauth=False, allow_oauth_cache=True)
    except PytubeFixError as e:
        logger.debug("pytubefix extract failed: %s", e)
        return None

    formats: list[DownloaderFormatItem] = []
    seen: set[str] = set()

    for stream in yt.streams.filter(progressive=True).order_by("resolution").desc():
        res = stream.resolution or "progressive"
        fid = f"{res}"
        if fid in seen:
            continue
        seen.add(fid)
        formats.append(
            DownloaderFormatItem(
                format_id=join_format_id(BACKEND_PYTUBEFIX, fid),
                ext="mp4",
                resolution=res,
                format_note=f"YouTube progressive {res} (pytubefix)",
                filesize=stream.filesize,
                needs_merge=False,
                has_video=True,
                has_audio=True,
            )
        )

    for stream in yt.streams.filter(adaptive=True, only_video=True).order_by(
        "resolution"
    ).desc():
        res = stream.resolution or "video"
        fid = f"video-{res}"
        if fid in seen:
            continue
        seen.add(fid)
        formats.append(
            DownloaderFormatItem(
                format_id=join_format_id(BACKEND_PYTUBEFIX, fid),
                ext="mp4",
                resolution=res,
                format_note=f"YouTube video {res} (merge on server)",
                filesize=stream.filesize,
                needs_merge=True,
                has_video=True,
                has_audio=False,
            )
        )

    audio = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
    if audio:
        formats.append(
            DownloaderFormatItem(
                format_id=join_format_id(BACKEND_PYTUBEFIX, "audio-best"),
                ext="m4a",
                resolution="Audio",
                format_note="YouTube audio only (pytubefix)",
                filesize=audio.filesize,
                needs_merge=False,
                has_video=False,
                has_audio=True,
            )
        )

    if not formats:
        return None

    return {
        "url": url,
        "id": yt.video_id,
        "title": yt.title,
        "thumbnail": yt.thumbnail_url,
        "duration": yt.length,
        "uploader": yt.author,
        "formats": formats,
        "extractor": "pytubefix",
    }


def _pick_stream(yt: Any, format_id: str):
    if format_id == "audio-best":
        stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
        if stream:
            return stream
        raise DownloaderApiError(
            "No audio stream found",
            status_code=404,
            error_code="FORMAT_NOT_FOUND",
        )

    if format_id.startswith("video-"):
        res = format_id.removeprefix("video-")
        stream = (
            yt.streams.filter(adaptive=True, only_video=True, resolution=res).first()
        )
        if stream:
            return stream
        raise DownloaderApiError(
            f"No video stream for {res}",
            status_code=404,
            error_code="FORMAT_NOT_FOUND",
        )

    stream = yt.streams.filter(progressive=True, resolution=format_id).first()
    if stream:
        return stream
    raise DownloaderApiError(
        f"No progressive stream for {format_id}",
        status_code=404,
        error_code="FORMAT_NOT_FOUND",
    )


def resolve_direct_url(url: str, format_id: str) -> dict[str, Any]:
    from pytubefix import YouTube

    yt = YouTube(url, use_oauth=False, allow_oauth_cache=True)
    stream = _pick_stream(yt, format_id)

    if stream.is_progressive:
        return {
            "direct_url": stream.url,
            "http_headers": {},
            "ext": "mp4",
            "title": yt.title,
            "needs_job": False,
            "recommended_format_id": None,
        }

    return {
        "direct_url": None,
        "http_headers": {},
        "ext": "mp4",
        "title": yt.title,
        "needs_job": True,
        "recommended_format_id": join_format_id(BACKEND_PYTUBEFIX, format_id),
    }


def download_with_format(
    url: str,
    format_id: str,
    output_dir: Path,
    *,
    progress_callback: Optional[Callable[[float], None]] = None,
    cancel_event: Optional[Any] = None,
) -> Path:
    from pytubefix import YouTube

    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise DownloaderApiError(
            "Download canceled",
            status_code=409,
            error_code="CANCELED",
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    yt = YouTube(url, use_oauth=False, allow_oauth_cache=True)
    stream = _pick_stream(yt, format_id)

    if progress_callback:
        progress_callback(0.1)

    stream.download(output_path=str(output_dir))

    candidates = sorted(
        output_dir.glob("*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out_path: Optional[Path] = None
    for c in candidates:
        if c.is_file():
            out_path = c
            break

    if out_path is None or not out_path.exists():
        raise DownloaderApiError(
            "pytubefix download produced no file",
            status_code=502,
            error_code="OUTPUT_MISSING",
        )

    if progress_callback:
        progress_callback(1.0)
    return out_path
