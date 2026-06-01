"""Downloader error types and yt-dlp exception classification."""

from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import HTTPException


class DownloaderApiError(Exception):
    """Maps to a JSON error response for downloader endpoints."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        error_code: str = "DOWNLOADER_ERROR",
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        super().__init__(message)

    def to_http_exception(self) -> HTTPException:
        return HTTPException(
            status_code=self.status_code,
            detail=error_detail(self.error_code, self.message),
        )


def error_detail(error_code: str, message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "error_code": error_code,
        "message": message,
    }


def classify_ytdlp_error(exc: BaseException) -> DownloaderApiError:
    """Turn yt-dlp / network failures into stable API error codes."""
    msg = str(exc).strip() or exc.__class__.__name__
    lower = msg.lower()
    name = exc.__class__.__name__.lower()

    if "unsupportedurl" in name or "unsupported url" in lower:
        return DownloaderApiError(msg, status_code=404, error_code="UNSUPPORTED_URL")
    if "urlunavailable" in name or "video unavailable" in lower:
        return DownloaderApiError(msg, status_code=404, error_code="VIDEO_UNAVAILABLE")
    if "private video" in lower or "sign in" in lower or "login" in lower:
        return DownloaderApiError(msg, status_code=403, error_code="LOGIN_REQUIRED")
    if "geo" in lower or "not available in your country" in lower:
        return DownloaderApiError(msg, status_code=403, error_code="GEO_BLOCKED")
    if "copyright" in lower or "blocked" in lower and "country" not in lower:
        return DownloaderApiError(msg, status_code=403, error_code="CONTENT_BLOCKED")
    if "http error 404" in lower or "not found" in lower:
        return DownloaderApiError(msg, status_code=404, error_code="VIDEO_NOT_FOUND")
    if "http error 403" in lower:
        return DownloaderApiError(msg, status_code=403, error_code="ACCESS_DENIED")
    if "timed out" in lower or "timeout" in lower or "timedout" in name:
        return DownloaderApiError(msg, status_code=504, error_code="TIMEOUT")
    if "ffmpeg" in lower and ("not found" in lower or "required" in lower):
        return DownloaderApiError(msg, status_code=503, error_code="FFMPEG_REQUIRED")
    if "no suitable formats" in lower or "requested format" in lower:
        return DownloaderApiError(msg, status_code=400, error_code="INVALID_FORMAT")
    if "canceled" in lower or "cancelled" in lower:
        return DownloaderApiError(msg, status_code=409, error_code="CANCELED")
    if "exceeds maximum size" in lower:
        return DownloaderApiError(msg, status_code=413, error_code="FILE_TOO_LARGE")

    return DownloaderApiError(msg, status_code=502, error_code="YTDLP_ERROR")


def http_exception_from_validation(message: str, error_code: str = "VALIDATION_ERROR") -> HTTPException:
    return HTTPException(status_code=400, detail=error_detail(error_code, message))


_JOB_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_FILE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}\.[a-f0-9]{32}$")


def validate_job_id(job_id: str) -> str:
    text = (job_id or "").strip()
    if not text or not _JOB_ID_RE.match(text):
        raise http_exception_from_validation("Invalid job_id", "INVALID_JOB_ID")
    return text


def validate_file_token(token: str) -> str:
    text = (token or "").strip()
    if not text or not _FILE_TOKEN_RE.match(text):
        raise HTTPException(
            status_code=400,
            detail=error_detail("INVALID_TOKEN", "Invalid file token"),
        )
    if ".." in text or "/" in text or "\\" in text:
        raise HTTPException(
            status_code=400,
            detail=error_detail("INVALID_TOKEN", "Invalid file token"),
        )
    return text


def normalize_http_detail(detail: Any) -> dict[str, Any]:
    """Normalize FastAPI/HTTPException detail for JSON clients."""
    if isinstance(detail, dict):
        if "status" in detail and "message" in detail:
            return detail
        if "message" in detail:
            return error_detail(detail.get("error_code", "ERROR"), str(detail["message"]))
        return error_detail("ERROR", str(detail))
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(x) for x in item.get("loc", ()))
                msg = item.get("msg", "")
                parts.append(f"{loc}: {msg}" if loc else str(msg))
            else:
                parts.append(str(item))
        return error_detail("VALIDATION_ERROR", "; ".join(parts) or "Validation failed")
    return error_detail("ERROR", str(detail))
