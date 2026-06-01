"""Pydantic schemas for /api/v1/downloader endpoints."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class DownloaderFormatItem(BaseModel):
    format_id: str
    ext: Optional[str] = None
    resolution: Optional[str] = None
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    filesize: Optional[int] = None
    protocol: Optional[str] = None
    format_note: Optional[str] = None
    needs_merge: bool = False
    has_video: bool = True
    has_audio: bool = True


class DownloaderExtractResponse(BaseModel):
    status: str = "success"
    url: str
    id: Optional[str] = None
    title: Optional[str] = None
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    uploader: Optional[str] = None
    is_playlist: bool = False
    playlist_count: Optional[int] = None
    formats: list[DownloaderFormatItem] = Field(default_factory=list)


class DownloaderResolveResponse(BaseModel):
    status: str = "success"
    direct_url: Optional[str] = None
    http_headers: dict[str, str] = Field(default_factory=dict)
    ext: Optional[str] = None
    title: Optional[str] = None
    needs_job: bool = False
    recommended_format_id: Optional[str] = None


class DownloaderJobCreateRequest(BaseModel):
    url: str
    format_id: str


class DownloaderJobCreateResponse(BaseModel):
    status: str = "success"
    job_id: str
    state: str = "queued"


class DownloaderJobStatusResponse(BaseModel):
    job_id: str
    state: str
    progress: float = 0.0
    title: Optional[str] = None
    error: Optional[str] = None
    file_url: Optional[str] = None
    file_size: Optional[int] = None
    ext: Optional[str] = None


class DownloaderJobCancelResponse(BaseModel):
    status: str = "success"
    state: str = "canceled"


class DownloaderHealthResponse(BaseModel):
    status: str
    yt_dlp_version: Optional[str] = None
    ffmpeg_available: bool = False
    temp_dir_writable: bool = False
    max_file_size_mb: int = 0
