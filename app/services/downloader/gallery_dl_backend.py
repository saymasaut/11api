"""gallery-dl backend — image/gallery sites (DeviantArt, Instagram galleries, etc.)."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from app.models.downloader_schemas import DownloaderFormatItem
from app.services.downloader.format_codec import BACKEND_GALLERY_DL, join_format_id
from app.services.downloader_exceptions import DownloaderApiError

logger = logging.getLogger(__name__)

_GALLERY_HOST_HINTS = (
    "deviantart.com",
    "danbooru",
    "gelbooru",
    "e621.net",
    "furaffinity",
    "inkbunny",
    "twitter.com",
    "x.com",
    "instagram.com",
    "imgur.com",
    "reddit.com",
    "patreon.com",
    "fanbox",
    "pixiv.net",
    "artstation",
    "hentai-foundry",
    "nhentai",
    "hitomi.la",
    "kemono.",
    "coomer.",
    "bunkr.",
    "cyberdrop",
    "jpg5.su",
    "saint2.",
)


def is_available() -> bool:
    try:
        import gallery_dl  # noqa: F401

        return True
    except ImportError:
        return False


def get_version() -> Optional[str]:
    try:
        import gallery_dl

        return getattr(gallery_dl, "__version__", None) or getattr(
            gallery_dl, "version", None
        )
    except ImportError:
        return None


def likely_supported(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return any(h in host for h in _GALLERY_HOST_HINTS)


def _import_gallery_dl() -> Any:
    try:
        import gallery_dl  # pyright: ignore[reportMissingImports]

        return gallery_dl
    except ImportError as e:
        raise DownloaderApiError(
            "gallery-dl is not installed on the server",
            status_code=503,
            error_code="GALLERY_DL_NOT_INSTALLED",
        ) from e


def extract_info(url: str) -> Optional[dict[str, Any]]:
    if not is_available():
        return None
    if not likely_supported(url):
        return None

    gallery_dl = _import_gallery_dl()
    from gallery_dl import extractor

    try:
        categories = extractor.find(url)
    except Exception as e:
        logger.debug("gallery-dl find failed for %s: %s", url, e)
        return None

    if not categories:
        return None

    formats = [
        DownloaderFormatItem(
            format_id=join_format_id(BACKEND_GALLERY_DL, "all"),
            ext="zip",
            resolution="Gallery",
            format_note="Full gallery (gallery-dl)",
            needs_merge=True,
            has_video=True,
            has_audio=False,
        ),
        DownloaderFormatItem(
            format_id=join_format_id(BACKEND_GALLERY_DL, "images"),
            ext="zip",
            resolution="Images",
            format_note="Images only (gallery-dl)",
            needs_merge=True,
            has_video=False,
            has_audio=False,
        ),
    ]

    return {
        "url": url,
        "title": "Gallery",
        "thumbnail": None,
        "duration": None,
        "uploader": None,
        "formats": formats,
        "extractor": "gallery-dl",
    }


def resolve_direct_url(url: str, format_id: str) -> dict[str, Any]:
    return {
        "direct_url": None,
        "http_headers": {},
        "ext": "zip",
        "title": "Gallery",
        "needs_job": True,
        "recommended_format_id": join_format_id(BACKEND_GALLERY_DL, format_id),
    }


def download_with_format(
    url: str,
    format_id: str,
    output_dir: Path,
    *,
    progress_callback: Optional[Callable[[float], None]] = None,
    cancel_event: Optional[Any] = None,
) -> Path:
    gallery_dl = _import_gallery_dl()
    import gallery_dl.config

    output_dir.mkdir(parents=True, exist_ok=True)
    gallery_dl.config.load()
    gallery_dl.config.set(("output", "directory"), str(output_dir))
    gallery_dl.config.set(("extractor",), {"base-directory": str(output_dir)})

    if progress_callback:
        progress_callback(0.1)

    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise DownloaderApiError(
            "Download canceled",
            status_code=409,
            error_code="CANCELED",
        )

    try:
        from gallery_dl import job as gdl_job

        download_job = gdl_job.DownloadJob(url)
        status = download_job.run()
        if status != 0:
            raise DownloaderApiError(
                f"gallery-dl finished with status {status}",
                status_code=502,
                error_code="GALLERY_DL_FAILED",
            )
    except DownloaderApiError:
        raise
    except Exception as e:
        raise DownloaderApiError(
            str(e),
            status_code=502,
            error_code="GALLERY_DL_FAILED",
        ) from e

    if progress_callback:
        progress_callback(0.9)

    # Prefer single video; else zip gallery folder; else largest file
    media_ext = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
    videos = [
        p
        for p in output_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in media_ext
    ]
    if videos:
        best = max(videos, key=lambda p: p.stat().st_size)
        if progress_callback:
            progress_callback(1.0)
        return best

    all_files = [p for p in output_dir.rglob("*") if p.is_file()]
    if not all_files:
        raise DownloaderApiError(
            "gallery-dl produced no files",
            status_code=502,
            error_code="OUTPUT_MISSING",
        )

    zip_path = output_dir / "gallery.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in all_files:
            zf.write(fp, arcname=fp.relative_to(output_dir).as_posix())

    if progress_callback:
        progress_callback(1.0)
    return zip_path
