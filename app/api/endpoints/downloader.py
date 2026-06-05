from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models.downloader_schemas import ExtractResponse
from app.services.ytdlp_service import extract_video

router = APIRouter()


@router.get("/extract", response_model=ExtractResponse)
async def extract_endpoint(url: str = Query(..., description="Video page URL")):
    """
    Extract metadata and downloadable formats for any yt-dlp-supported URL.
    """
    if not url or not url.strip():
        raise HTTPException(status_code=400, detail="unsupported_url")

    try:
        return await extract_video(url)
    except HTTPException:
        raise
    except Exception as e:
        detail = str(e).strip() or "extract_failed"
        if len(detail) > 200:
            detail = detail[:199] + "…"
        raise HTTPException(status_code=500, detail=detail) from e
