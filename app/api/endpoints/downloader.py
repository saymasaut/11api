"""Downloader API — yt-dlp extract, resolve, jobs, file serve, health."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.config.settings import settings
from app.models.downloader_schemas import (
    DownloaderExtractResponse,
    DownloaderFormatItem,
    DownloaderHealthResponse,
    DownloaderJobCancelResponse,
    DownloaderJobCreateRequest,
    DownloaderJobCreateResponse,
    DownloaderJobStatusResponse,
    DownloaderResolveResponse,
)
from app.services.downloader_job_store import job_store
from app.services.downloader_security import validate_downloader_url
from app.services import ytdlp_service

logger = logging.getLogger(__name__)

router = APIRouter()


FILES_ROUTE_PREFIX = "/api/v1/downloader/files"


async def _run_download_job(job_id: str) -> None:
    job = await job_store.get_job(job_id)
    if not job:
        return

    if job.cancel_event.is_set():
        await job_store.update_job(job_id, state="canceled")
        return

    await job_store.update_job(job_id, state="processing", progress=0.05)
    work_dir = Path(settings.DOWNLOADER_TEMP_DIR) / job_id
    last_progress = [0.05]

    def on_progress(p: float) -> None:
        last_progress[0] = max(0.05, min(0.99, p))

    try:
        path = await asyncio.to_thread(
            ytdlp_service.download_with_format,
            job.url,
            job.format_id,
            work_dir,
            progress_callback=on_progress,
            cancel_event=job.cancel_event,
        )

        await job_store.update_job(job_id, progress=last_progress[0])

        if job.cancel_event.is_set():
            await job_store.update_job(job_id, state="canceled")
            return

        size = path.stat().st_size
        max_bytes = settings.DOWNLOADER_MAX_FILE_MB * 1024 * 1024
        if size > max_bytes:
            path.unlink(missing_ok=True)
            raise RuntimeError(
                f"File exceeds maximum size ({settings.DOWNLOADER_MAX_FILE_MB} MB)"
            )

        ext = path.suffix.lstrip(".") or "mp4"
        await job_store.issue_file_token(job_id)
        await job_store.update_job(
            job_id,
            state="ready",
            progress=1.0,
            output_path=path,
            ext=ext,
            title=path.stem[:200],
        )
    except asyncio.CancelledError:
        await job_store.update_job(job_id, state="canceled", error="Canceled")
    except Exception as e:
        if job.cancel_event.is_set():
            await job_store.update_job(job_id, state="canceled", error="Canceled")
        else:
            logger.exception("Downloader job %s failed", job_id)
            await job_store.update_job(job_id, state="failed", error=str(e))


@router.get("/health", response_model=DownloaderHealthResponse)
async def downloader_health() -> DownloaderHealthResponse:
    version = ytdlp_service.get_yt_dlp_version()
    if version is None:
        return DownloaderHealthResponse(
            status="error",
            yt_dlp_version=None,
            ffmpeg_available=False,
            temp_dir_writable=False,
            max_file_size_mb=settings.DOWNLOADER_MAX_FILE_MB,
        )
    return DownloaderHealthResponse(
        status="ok",
        yt_dlp_version=version,
        ffmpeg_available=ytdlp_service.is_ffmpeg_available(),
        temp_dir_writable=ytdlp_service.check_temp_dir_writable(),
        max_file_size_mb=settings.DOWNLOADER_MAX_FILE_MB,
    )


@router.get("/extract", response_model=DownloaderExtractResponse)
async def downloader_extract(url: str = Query(..., description="Video page URL")):
    safe_url = validate_downloader_url(url)
    try:
        data = await asyncio.to_thread(ytdlp_service.extract_info, safe_url)
    except Exception as e:
        logger.exception("extract failed for %s", safe_url)
        raise HTTPException(status_code=502, detail=f"Extraction failed: {e}") from e

    formats = data.get("formats") or []
    if isinstance(formats, list) and formats and isinstance(formats[0], DownloaderFormatItem):
        format_items = formats
    else:
        format_items = [
            f if isinstance(f, DownloaderFormatItem) else DownloaderFormatItem(**f)
            for f in formats
        ]

    return DownloaderExtractResponse(
        url=data["url"],
        id=data.get("id"),
        title=data.get("title"),
        thumbnail=data.get("thumbnail"),
        duration=data.get("duration"),
        uploader=data.get("uploader"),
        is_playlist=data.get("is_playlist", False),
        playlist_count=data.get("playlist_count"),
        formats=format_items,
    )


@router.get("/resolve", response_model=DownloaderResolveResponse)
async def downloader_resolve(
    url: str = Query(...),
    format_id: str = Query(..., alias="format_id"),
):
    safe_url = validate_downloader_url(url)
    if not format_id.strip():
        raise HTTPException(status_code=400, detail="format_id is required")
    try:
        data = await asyncio.to_thread(
            ytdlp_service.resolve_direct_url, safe_url, format_id.strip()
        )
    except Exception as e:
        logger.exception("resolve failed")
        raise HTTPException(status_code=502, detail=f"Resolve failed: {e}") from e

    return DownloaderResolveResponse(**data)


@router.post("/jobs", response_model=DownloaderJobCreateResponse)
async def downloader_create_job(body: DownloaderJobCreateRequest):
    safe_url = validate_downloader_url(body.url)
    format_id = body.format_id.strip()
    if not format_id:
        raise HTTPException(status_code=400, detail="format_id is required")

    job = await job_store.create_job(safe_url, format_id)

    async def _wrapped() -> None:
        await _run_download_job(job.job_id)

    task = asyncio.create_task(_wrapped())
    job.task = task

    return DownloaderJobCreateResponse(job_id=job.job_id, state=job.state)


@router.get("/jobs/{job_id}", response_model=DownloaderJobStatusResponse)
async def downloader_get_job(job_id: str, request: Request):
    job = await job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    file_url = None
    file_size = None
    if job.state == "ready" and job.file_token:
        file_url = f"{str(request.base_url).rstrip('/')}{FILES_ROUTE_PREFIX}/{job.file_token}"
        if job.output_path and job.output_path.exists():
            file_size = job.output_path.stat().st_size

    return DownloaderJobStatusResponse(
        job_id=job.job_id,
        state=job.state,
        progress=job.progress,
        title=job.title,
        error=job.error,
        file_url=file_url,
        file_size=file_size,
        ext=job.ext,
    )


@router.delete("/jobs/{job_id}", response_model=DownloaderJobCancelResponse)
async def downloader_cancel_job(job_id: str):
    job = await job_store.cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return DownloaderJobCancelResponse(state=job.state)


@router.get("/files/{token}")
async def downloader_serve_file(
    token: str,
    download: int = Query(0, description="1 for attachment"),
):
    resolved = await job_store.resolve_file_path(token)
    if not resolved:
        raise HTTPException(status_code=404, detail="File not found or expired")

    path, job = resolved
    filename = f"{(job.title or 'video').strip()[:120]}.{job.ext or 'mp4'}"
    filename = "".join(c if c not in '/\\:*?"<>|' else "_" for c in filename)

    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename if download else None,
    )
