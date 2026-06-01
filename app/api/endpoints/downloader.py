"""Downloader API — yt-dlp extract, resolve, jobs, file serve, health."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

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
from app.services.downloader_exceptions import (
    DownloaderApiError,
    classify_ytdlp_error,
    error_detail,
    http_exception_from_validation,
    validate_file_token,
    validate_job_id,
)
from app.services.downloader_job_store import job_store
from app.services.downloader_security import validate_downloader_url
from app.services.downloader import orchestrator as media_downloader
from app.services import ytdlp_service

logger = logging.getLogger(__name__)

router = APIRouter()

FILES_ROUTE_PREFIX = "/api/v1/downloader/files"

T = TypeVar("T")


async def _run_in_thread(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except DownloaderApiError as e:
        raise e.to_http_exception() from e
    except Exception as e:
        raise classify_ytdlp_error(e).to_http_exception() from e


def _ensure_downloader_ready() -> None:
    health = media_downloader.get_health()
    if health["status"] == "error":
        raise HTTPException(
            status_code=503,
            detail=error_detail(
                "DOWNLOADER_NOT_INSTALLED",
                health.get("message") or "No downloader libraries installed",
            ),
        )
    if not health["temp_dir_writable"]:
        raise HTTPException(
            status_code=503,
            detail=error_detail(
                "TEMP_DIR_NOT_WRITABLE",
                "Downloader temp directory is not writable",
            ),
        )


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
            media_downloader.download_with_format,
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
            err = DownloaderApiError(
                f"File exceeds maximum size ({settings.DOWNLOADER_MAX_FILE_MB} MB)",
                status_code=413,
                error_code="FILE_TOO_LARGE",
            )
            await job_store.update_job(
                job_id,
                state="failed",
                error=err.message,
            )
            return

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
        await job_store.update_job(
            job_id, state="canceled", error="Canceled by server"
        )
    except DownloaderApiError as e:
        await job_store.update_job(job_id, state="failed", error=e.message)
    except Exception as e:
        if job.cancel_event.is_set():
            await job_store.update_job(job_id, state="canceled", error="Canceled")
        else:
            mapped = classify_ytdlp_error(e)
            logger.exception("Downloader job %s failed", job_id)
            await job_store.update_job(job_id, state="failed", error=mapped.message)


@router.get(
    "/health",
    response_model=DownloaderHealthResponse,
    responses={
        200: {"description": "Service status"},
    },
)
async def downloader_health() -> DownloaderHealthResponse:
    h = media_downloader.get_health()
    return DownloaderHealthResponse(
        status=h["status"],
        yt_dlp_version=h.get("yt_dlp_version"),
        ffmpeg_available=h.get("ffmpeg_available", False),
        temp_dir_writable=h.get("temp_dir_writable", False),
        max_file_size_mb=settings.DOWNLOADER_MAX_FILE_MB,
        message=h.get("message"),
        backends=h.get("backends") or {},
    )


@router.get(
    "/extract",
    response_model=DownloaderExtractResponse,
    responses={
        400: {"description": "Invalid URL"},
        422: {"description": "SSRF blocked"},
        404: {"description": "Video not found or no formats"},
        502: {"description": "yt-dlp extraction failed"},
        503: {"description": "yt-dlp not available"},
        504: {"description": "Timeout"},
    },
)
async def downloader_extract(
    url: str = Query(..., min_length=1, description="Video page URL"),
):
    _ensure_downloader_ready()
    safe_url = validate_downloader_url(url)
    data = await _run_in_thread(media_downloader.extract_info, safe_url)

    formats = data.get("formats") or []
    format_items: list[DownloaderFormatItem] = []
    from app.services.downloader.format_codec import split_format_id

    for f in formats:
        if isinstance(f, DownloaderFormatItem):
            item = f
        elif isinstance(f, dict):
            item = DownloaderFormatItem(**f)
        else:
            continue
        backend, _ = split_format_id(item.format_id)
        format_items.append(item.model_copy(update={"backend": backend}))

    return DownloaderExtractResponse(
        url=data["url"],
        id=data.get("id"),
        title=data.get("title"),
        thumbnail=data.get("thumbnail"),
        duration=data.get("duration"),
        uploader=data.get("uploader"),
        is_playlist=data.get("is_playlist", False),
        playlist_count=data.get("playlist_count"),
        resolved_from_playlist=bool(data.get("playlist_count")),
        extractor=data.get("extractor"),
        formats=format_items,
    )


@router.get(
    "/resolve",
    response_model=DownloaderResolveResponse,
    responses={
        400: {"description": "Missing format_id or invalid URL"},
        404: {"description": "Format or video not found"},
        502: {"description": "Resolve failed"},
        503: {"description": "Service unavailable"},
    },
)
async def downloader_resolve(
    url: str = Query(..., min_length=1),
    format_id: str = Query(..., alias="format_id", min_length=1),
):
    _ensure_downloader_ready()
    safe_url = validate_downloader_url(url)
    fid = format_id.strip()
    if not fid:
        raise http_exception_from_validation("format_id is required", "FORMAT_ID_REQUIRED")

    data = await _run_in_thread(media_downloader.resolve_direct_url, safe_url, fid)
    return DownloaderResolveResponse(**data)


@router.post(
    "/jobs",
    response_model=DownloaderJobCreateResponse,
    responses={
        400: {"description": "Invalid request body"},
        503: {"description": "Downloader not ready"},
    },
)
async def downloader_create_job(body: DownloaderJobCreateRequest):
    _ensure_downloader_ready()
    safe_url = validate_downloader_url(body.url)
    format_id = body.format_id.strip()
    if not format_id:
        raise http_exception_from_validation("format_id is required", "FORMAT_ID_REQUIRED")

    job = await job_store.create_job(safe_url, format_id)

    async def _wrapped() -> None:
        await _run_download_job(job.job_id)

    task = asyncio.create_task(_wrapped())
    job.task = task

    return DownloaderJobCreateResponse(job_id=job.job_id, state=job.state)


@router.get(
    "/jobs/{job_id}",
    response_model=DownloaderJobStatusResponse,
    responses={
        400: {"description": "Invalid job_id"},
        404: {"description": "Job not found"},
    },
)
async def downloader_get_job(job_id: str, request: Request):
    jid = validate_job_id(job_id)
    job = await job_store.get_job(jid)
    if not job:
        raise HTTPException(
            status_code=404,
            detail=error_detail("JOB_NOT_FOUND", "Job not found"),
        )

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


@router.delete(
    "/jobs/{job_id}",
    response_model=DownloaderJobCancelResponse,
    responses={
        400: {"description": "Invalid job_id"},
        404: {"description": "Job not found"},
    },
)
async def downloader_cancel_job(job_id: str):
    jid = validate_job_id(job_id)
    job = await job_store.cancel_job(jid)
    if not job:
        raise HTTPException(
            status_code=404,
            detail=error_detail("JOB_NOT_FOUND", "Job not found"),
        )
    return DownloaderJobCancelResponse(state=job.state)


@router.get(
    "/files/{token}",
    responses={
        400: {"description": "Invalid token"},
        404: {"description": "File not found or expired"},
    },
)
async def downloader_serve_file(
    token: str,
    download: int = Query(0, ge=0, le=1, description="1 for attachment"),
):
    safe_token = validate_file_token(token)
    resolved = await job_store.resolve_file_path(safe_token)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=error_detail(
                "FILE_NOT_FOUND",
                "File not found or download link expired",
            ),
        )

    path, job = resolved
    filename = f"{(job.title or 'video').strip()[:120]}.{job.ext or 'mp4'}"
    filename = "".join(c if c not in '/\\:*?"<>|' else "_" for c in filename)

    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename if download else None,
    )
