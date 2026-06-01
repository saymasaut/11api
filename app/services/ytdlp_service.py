"""yt-dlp wrapper for extract, resolve, and download+merge jobs."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from app.config.settings import settings
from app.models.downloader_schemas import DownloaderFormatItem
from app.services.downloader_exceptions import DownloaderApiError, classify_ytdlp_error

logger = logging.getLogger(__name__)

_MERGE_PROTOCOLS = frozenset({"m3u8", "m3u8_native", "http_dash_segments", "dash"})
_FRAGMENTED_EXTS = frozenset({"m3u8", "mpd"})


def get_yt_dlp_version() -> Optional[str]:
    try:
        import yt_dlp

        return getattr(yt_dlp, "version", None) or getattr(yt_dlp, "__version__", None)
    except Exception:
        return None


def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _base_ydl_opts() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": settings.SCRAPER_TIMEOUT,
    }


def _format_needs_merge(fmt: dict[str, Any]) -> bool:
    protocol = str(fmt.get("protocol") or "").lower()
    ext = str(fmt.get("ext") or "").lower()
    vcodec = str(fmt.get("vcodec") or "none")
    acodec = str(fmt.get("acodec") or "none")
    if ext in _FRAGMENTED_EXTS:
        return True
    if any(p in protocol for p in _MERGE_PROTOCOLS):
        return True
    if "+" in str(fmt.get("format_id") or ""):
        return True
    has_v = vcodec not in ("none", "")
    has_a = acodec not in ("none", "")
    return has_v != has_a and (has_v or has_a)


def _resolution_label(fmt: dict[str, Any]) -> Optional[str]:
    height = fmt.get("height")
    if height:
        return f"{height}p"
    note = fmt.get("format_note") or fmt.get("resolution")
    if note:
        return str(note)
    return None


def _build_format_item(fmt: dict[str, Any]) -> DownloaderFormatItem:
    return DownloaderFormatItem(
        format_id=str(fmt.get("format_id") or ""),
        ext=fmt.get("ext"),
        resolution=_resolution_label(fmt),
        vcodec=fmt.get("vcodec"),
        acodec=fmt.get("acodec"),
        filesize=fmt.get("filesize") or fmt.get("filesize_approx"),
        protocol=fmt.get("protocol"),
        format_note=fmt.get("format_note"),
        needs_merge=_format_needs_merge(fmt),
        has_video=str(fmt.get("vcodec") or "none") not in ("none", ""),
        has_audio=str(fmt.get("acodec") or "none") not in ("none", ""),
    )


def _iter_usable_formats(info: dict[str, Any]) -> list[dict[str, Any]]:
    formats = info.get("formats") or []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        fid = str(fmt.get("format_id") or "")
        if not fid or fid in seen:
            continue
        url = fmt.get("url")
        ext = str(fmt.get("ext") or "").lower()
        if not url and ext not in _FRAGMENTED_EXTS:
            continue
        seen.add(fid)
        out.append(fmt)
    return out


def extract_info(url: str) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError as e:
        raise DownloaderApiError(
            "yt-dlp is not installed on the server",
            status_code=503,
            error_code="YTDLP_NOT_INSTALLED",
        ) from e

    try:
        opts = {
            **_base_ydl_opts(),
            "skip_download": True,
            "extract_flat": "in_playlist",
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise classify_ytdlp_error(e) from e

    if not info:
        raise DownloaderApiError(
            "No information returned from yt-dlp",
            status_code=404,
            error_code="NO_METADATA",
        )

    is_playlist = info.get("_type") == "playlist"
    entries = info.get("entries") or []
    playlist_count = len(entries) if is_playlist else None

    formats_raw = _iter_usable_formats(info)
    formats = [_build_format_item(f) for f in formats_raw]
    formats.sort(
        key=lambda f: (f.filesize or 0, f.resolution or ""),
        reverse=True,
    )

    return {
        "url": url,
        "id": info.get("id"),
        "title": info.get("title") or info.get("fulltitle"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "is_playlist": is_playlist,
        "playlist_count": playlist_count,
        "formats": formats,
    }


def resolve_direct_url(url: str, format_id: str) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError as e:
        raise DownloaderApiError(
            "yt-dlp is not installed on the server",
            status_code=503,
            error_code="YTDLP_NOT_INSTALLED",
        ) from e

    try:
        opts = {
            **_base_ydl_opts(),
            "format": format_id,
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise classify_ytdlp_error(e) from e

    if not info:
        raise DownloaderApiError(
            "Could not resolve format",
            status_code=404,
            error_code="FORMAT_NOT_FOUND",
        )

    title = info.get("title") or info.get("fulltitle")
    ext = info.get("ext")
    direct = info.get("url")
    protocol = str(info.get("protocol") or "").lower()
    req_ext = str(ext or "").lower()

    headers: dict[str, str] = {}
    if info.get("http_headers") and isinstance(info["http_headers"], dict):
        headers = {str(k): str(v) for k, v in info["http_headers"].items()}

    needs_job = (
        not direct
        or req_ext in _FRAGMENTED_EXTS
        or any(p in protocol for p in _MERGE_PROTOCOLS)
        or _format_needs_merge(info)
    )

    if needs_job:
        return {
            "direct_url": None,
            "http_headers": headers,
            "ext": ext,
            "title": title,
            "needs_job": True,
            "recommended_format_id": format_id,
        }

    return {
        "direct_url": direct,
        "http_headers": headers,
        "ext": ext,
        "title": title,
        "needs_job": False,
        "recommended_format_id": None,
    }


def download_with_format(
    url: str,
    format_id: str,
    output_dir: Path,
    *,
    progress_callback: Optional[Callable[[float], None]] = None,
    cancel_event: Optional[Any] = None,
) -> Path:
    """
    Download and merge to output_dir. Returns path to final file.
  progress_callback receives 0.0–1.0.
    """
    try:
        import yt_dlp
    except ImportError as e:
        raise DownloaderApiError(
            "yt-dlp is not installed on the server",
            status_code=503,
            error_code="YTDLP_NOT_INSTALLED",
        ) from e

    if not is_ffmpeg_available() and ("+" in format_id or "best" in format_id.lower()):
        raise DownloaderApiError(
            "ffmpeg is required for this format but is not available on the server",
            status_code=503,
            error_code="FFMPEG_REQUIRED",
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(output_dir / "%(title).200B [%(id)s].%(ext)s")

    def _hook(d: dict[str, Any]) -> None:
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            raise DownloaderApiError(
                "Download canceled",
                status_code=409,
                error_code="CANCELED",
            )
        if progress_callback and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total and total > 0:
                progress_callback(min(0.99, done / total))

    opts: dict[str, Any] = {
        **_base_ydl_opts(),
        "format": format_id,
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "progress_hooks": [_hook],
        "noplaylist": True,
    }
    if not is_ffmpeg_available():
        opts.pop("merge_output_format", None)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise DownloaderApiError(
                    "Download failed",
                    status_code=502,
                    error_code="DOWNLOAD_FAILED",
                )

            filepath = ydl.prepare_filename(info)
            path = Path(filepath)
            if path.exists():
                if progress_callback:
                    progress_callback(1.0)
                return path

            candidates = sorted(
                output_dir.glob("*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for c in candidates:
                if c.is_file() and c.suffix.lower() in (
                    ".mp4",
                    ".mkv",
                    ".webm",
                    ".m4a",
                    ".mp3",
                ):
                    if progress_callback:
                        progress_callback(1.0)
                    return c

        raise DownloaderApiError(
            "Download finished but output file not found",
            status_code=502,
            error_code="OUTPUT_MISSING",
        )
    except DownloaderApiError:
        raise
    except Exception as e:
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            raise DownloaderApiError(
                "Download canceled",
                status_code=409,
                error_code="CANCELED",
            ) from e
        raise classify_ytdlp_error(e) from e


def check_temp_dir_writable() -> bool:
    try:
        root = Path(settings.DOWNLOADER_TEMP_DIR)
        root.mkdir(parents=True, exist_ok=True)
        test = root / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink(missing_ok=True)
        return True
    except Exception as e:
        logger.warning("Downloader temp dir not writable: %s", e)
        return False
