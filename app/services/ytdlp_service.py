"""yt-dlp wrapper — universal site support via yt-dlp extractors (1000+ sites)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Callable, Optional, cast
from urllib.parse import urljoin, urlparse

from app.config.settings import settings
from app.models.downloader_schemas import DownloaderFormatItem
from app.services.downloader_exceptions import DownloaderApiError, classify_ytdlp_error

logger = logging.getLogger(__name__)

_MERGE_PROTOCOLS = frozenset(
    {
        "m3u8",
        "m3u8_native",
        "http_dash_segments",
        "dash",
        "mhtml",
        "f4m",
    }
)
_FRAGMENTED_EXTS = frozenset({"m3u8", "mpd", "f4m", "ism"})
_STORYBOARD_RE = frozenset({"storyboard", "images", "slideshow"})

# Seal-style presets — work across most yt-dlp-supported sites
_PRESET_FORMATS: list[DownloaderFormatItem] = [
    DownloaderFormatItem(
        format_id="bv*+ba/b",
        ext="mp4",
        resolution="Best",
        format_note="Best video + audio (recommended)",
        needs_merge=True,
        has_video=True,
        has_audio=True,
    ),
    DownloaderFormatItem(
        format_id="best",
        ext="mp4",
        resolution="Best",
        format_note="Best single file",
        needs_merge=False,
        has_video=True,
        has_audio=True,
    ),
    DownloaderFormatItem(
        format_id="bestvideo+bestaudio/b",
        ext="mp4",
        resolution="Best",
        format_note="Best video + best audio (merge)",
        needs_merge=True,
        has_video=True,
        has_audio=True,
    ),
    DownloaderFormatItem(
        format_id="bestaudio/b",
        ext="m4a",
        resolution="Audio",
        format_note="Audio only",
        needs_merge=False,
        has_video=False,
        has_audio=True,
    ),
    DownloaderFormatItem(
        format_id="worst",
        ext="mp4",
        resolution="Low",
        format_note="Smallest / fastest",
        needs_merge=False,
        has_video=True,
        has_audio=True,
    ),
]

def get_preset_formats() -> list[DownloaderFormatItem]:
    """Universal yt-dlp format selectors (prefixed by orchestrator)."""
    return list(_PRESET_FORMATS)


_YTDLP_NOT_INSTALLED = DownloaderApiError(
    "yt-dlp is not installed on the server",
    status_code=503,
    error_code="YTDLP_NOT_INSTALLED",
)


def _import_yt_dlp() -> Any:
    """Import yt-dlp once; optional dep may be missing in dev venv."""
    try:
        import yt_dlp  # pyright: ignore[reportMissingImports]
    except ImportError as e:
        raise _YTDLP_NOT_INSTALLED from e
    return yt_dlp


def get_yt_dlp_version() -> Optional[str]:
    try:
        yt_dlp = _import_yt_dlp()
    except DownloaderApiError:
        return None
    return cast(
        Optional[str],
        getattr(yt_dlp, "version", None) or getattr(yt_dlp, "__version__", None),
    )


def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _base_ydl_opts(*, for_download: bool = False) -> dict[str, Any]:
    """Options tuned for broad extractor compatibility (YouTube, social, HLS, DASH)."""
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        # Single video when URL has ?list= — avoids empty format lists on mix URLs
        "noplaylist": True,
        "socket_timeout": settings.DOWNLOADER_SOCKET_TIMEOUT,
        "retries": settings.DOWNLOADER_RETRIES,
        "fragment_retries": settings.DOWNLOADER_RETRIES,
        "extractor_retries": settings.DOWNLOADER_RETRIES,
        "geo_bypass": True,
        "nocheckcertificate": settings.DOWNLOADER_IGNORE_SSL,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        # YouTube / JS challenge sites
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web", "ios"],
            },
            "generic": {"impersonate": True},
        },
    }
    cookies = (settings.DOWNLOADER_COOKIES_FILE or "").strip()
    if cookies and Path(cookies).is_file():
        opts["cookiefile"] = cookies
    if for_download:
        opts["concurrent_fragment_downloads"] = 4
    return opts


def _format_needs_merge(fmt: dict[str, Any]) -> bool:
    protocol = str(fmt.get("protocol") or "").lower()
    ext = str(fmt.get("ext") or "").lower()
    vcodec = str(fmt.get("vcodec") or "none")
    acodec = str(fmt.get("acodec") or "none")
    fid = str(fmt.get("format_id") or "")
    if ext in _FRAGMENTED_EXTS:
        return True
    if any(p in protocol for p in _MERGE_PROTOCOLS):
        return True
    if "+" in fid:
        return True
    has_v = vcodec not in ("none", "")
    has_a = acodec not in ("none", "")
    return has_v != has_a and (has_v or has_a)


def _resolution_label(fmt: dict[str, Any]) -> Optional[str]:
    height = fmt.get("height")
    if height:
        return f"{int(height)}p"
    note = fmt.get("format_note") or fmt.get("resolution")
    if note:
        return str(note)
    return None


def _is_storyboard_or_junk(fmt: dict[str, Any]) -> bool:
    fid = str(fmt.get("format_id") or "").lower()
    if fid.startswith("sb") or fid == "storyboard":
        return True
    note = str(fmt.get("format_note") or "").lower()
    if any(x in note for x in _STORYBOARD_RE):
        return True
    vcodec = str(fmt.get("vcodec") or "none")
    acodec = str(fmt.get("acodec") or "none")
    ext = str(fmt.get("ext") or "").lower()
    if vcodec in ("none", "") and acodec in ("none", "") and ext in ("mhtml", "jpg", "png"):
        return True
    return False


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


def _iter_selectable_formats(info: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Include HLS/DASH/merge formats even without a direct URL — yt-dlp resolves them at download time.
    """
    formats = info.get("formats") or []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        if _is_storyboard_or_junk(fmt):
            continue
        fid = str(fmt.get("format_id") or "")
        if not fid or fid in seen:
            continue
        seen.add(fid)
        out.append(fmt)
    return out


def _height_sort_key(item: DownloaderFormatItem) -> tuple[int, int]:
    res = item.resolution or ""
    digits = "".join(c for c in res if c.isdigit())
    height = int(digits) if digits else 0
    return (height, item.filesize or 0)


def _merge_format_lists(
    extracted: list[DownloaderFormatItem],
) -> list[DownloaderFormatItem]:
    """Presets first, then site-specific formats (deduped by format_id)."""
    seen: set[str] = set()
    merged: list[DownloaderFormatItem] = []
    for preset in _PRESET_FORMATS:
        if preset.format_id not in seen:
            seen.add(preset.format_id)
            merged.append(preset)
    site_sorted = sorted(extracted, key=_height_sort_key, reverse=True)
    for item in site_sorted:
        if item.format_id and item.format_id not in seen:
            seen.add(item.format_id)
            merged.append(item)
    return merged


def _resolve_playlist_to_video(ydl: Any, url: str, info: dict[str, Any]) -> dict[str, Any]:
    """If user pasted a playlist URL, fully extract the first video."""
    if info.get("_type") not in ("playlist", "multi_video"):
        return info

    entries = [e for e in (info.get("entries") or []) if e is not None]
    if not entries:
        raise DownloaderApiError(
            "Playlist has no videos",
            status_code=404,
            error_code="EMPTY_PLAYLIST",
        )

    first = entries[0]
    if not isinstance(first, dict):
        raise DownloaderApiError(
            "Could not read playlist entry",
            status_code=502,
            error_code="PLAYLIST_ENTRY_ERROR",
        )

    video_url = (
        first.get("webpage_url")
        or first.get("url")
        or first.get("original_url")
    )
    if not video_url and first.get("id"):
        base = info.get("webpage_url") or url
        parsed = urlparse(base)
        if "youtube.com" in (parsed.netloc or ""):
            video_url = f"https://www.youtube.com/watch?v={first['id']}"
        else:
            video_url = urljoin(base.rstrip("/") + "/", str(first["id"]))

    if not video_url:
        raise DownloaderApiError(
            "Could not resolve a video from this playlist",
            status_code=404,
            error_code="PLAYLIST_ENTRY_ERROR",
        )

    logger.info("Playlist URL — extracting first video: %s", video_url)
    return ydl.extract_info(video_url, download=False)


def extract_info(url: str) -> dict[str, Any]:
    yt_dlp = _import_yt_dlp()
    playlist_count: Optional[int] = None

    try:
        opts = {**_base_ydl_opts(), "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise DownloaderApiError(
                    "No information returned from yt-dlp",
                    status_code=404,
                    error_code="NO_METADATA",
                )
            if info.get("_type") in ("playlist", "multi_video"):
                entries = info.get("entries") or []
                playlist_count = len([e for e in entries if e is not None])
                info = _resolve_playlist_to_video(ydl, url, info)

    except DownloaderApiError:
        raise
    except Exception as e:
        raise classify_ytdlp_error(e) from e

    formats_raw = _iter_selectable_formats(info)
    site_formats = [_build_format_item(f) for f in formats_raw]
    formats = _merge_format_lists(site_formats)

    if not formats:
        formats = list(_PRESET_FORMATS)

    return {
        "url": url,
        "id": info.get("id"),
        "title": info.get("title") or info.get("fulltitle"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "is_playlist": False,
        "playlist_count": playlist_count,
        "formats": formats,
        "extractor": info.get("extractor") or info.get("extractor_key"),
    }


def resolve_direct_url(url: str, format_id: str) -> dict[str, Any]:
    yt_dlp = _import_yt_dlp()

    # yt-dlp format selectors (presets) always need server merge/download
    if format_id in {p.format_id for p in _PRESET_FORMATS} or "/" in format_id or "*" in format_id:
        return {
            "direct_url": None,
            "http_headers": {},
            "ext": "mp4",
            "title": None,
            "needs_job": True,
            "recommended_format_id": format_id,
        }

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
    yt_dlp = _import_yt_dlp()

    merge_likely = (
        "+" in format_id
        or "/" in format_id
        or "*" in format_id
        or "best" in format_id.lower()
    )
    if merge_likely and not is_ffmpeg_available():
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
        **_base_ydl_opts(for_download=True),
        "format": format_id,
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "progress_hooks": [_hook],
        "fixup": "never",
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
                    ".opus",
                    ".aac",
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
