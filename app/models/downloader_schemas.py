from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class FormatItem(BaseModel):
    format_id: str
    label: str
    ext: Optional[str] = None
    resolution: Optional[str] = None
    filesize: Optional[int] = None
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    mode: Literal["single", "separate", "audio_only"] = "single"
    url: Optional[str] = None
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    is_default: bool = False


class MetadataBlock(BaseModel):
    title: Optional[str] = None
    full_title: Optional[str] = None
    thumbnail: Optional[str] = None
    thumbnails: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    uploader: Optional[str] = None
    uploader_id: Optional[str] = None
    channel: Optional[str] = None
    channel_id: Optional[str] = None
    duration: Optional[float] = None
    timestamp: Optional[float] = None
    upload_date: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    webpage_url: Optional[str] = None
    original_url: Optional[str] = None
    extractor: Optional[str] = None
    extractor_key: Optional[str] = None
    platform: Optional[str] = None


class ExtractResponse(BaseModel):
    status: Literal["success", "error"] = "success"
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Optional[MetadataBlock] = None
    formats: list[FormatItem] = Field(default_factory=list)
    default_format_id: Optional[str] = None
    http_headers: dict[str, str] = Field(default_factory=dict)
    filename_hint: Optional[str] = None
