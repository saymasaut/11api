"""In-memory job store with signed file tokens for downloader outputs."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.config.settings import settings


@dataclass
class DownloaderJob:
    job_id: str
    url: str
    format_id: str
    state: str = "queued"
    progress: float = 0.0
    title: Optional[str] = None
    error: Optional[str] = None
    output_path: Optional[Path] = None
    file_token: Optional[str] = None
    ext: Optional[str] = "mp4"
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None
    created_at: float = field(default_factory=time.time)


class DownloaderJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, DownloaderJob] = {}
        self._tokens: dict[str, tuple[str, float, int]] = {}  # token -> (job_id, expires, uses)
        self._lock = asyncio.Lock()

    def _sign_token(self, job_id: str, nonce: str) -> str:
        msg = f"{job_id}:{nonce}".encode()
        digest = hmac.new(
            settings.SECRET_KEY.encode(),
            msg,
            hashlib.sha256,
        ).hexdigest()[:32]
        return f"{nonce}.{digest}"

    def _verify_token(self, token: str) -> Optional[str]:
        parts = token.split(".", 1)
        if len(parts) != 2:
            return None
        nonce, digest = parts
        for job_id, job in self._jobs.items():
            if job.file_token != token:
                continue
            expected = self._sign_token(job_id, nonce).split(".", 1)[1]
            if hmac.compare_digest(digest, expected):
                return job_id
        entry = self._tokens.get(token)
        if entry:
            job_id, expires, _uses = entry
            if time.time() > expires:
                return None
            return job_id
        return None

    async def create_job(self, url: str, format_id: str) -> DownloaderJob:
        async with self._lock:
            job_id = str(uuid.uuid4())
            job = DownloaderJob(job_id=job_id, url=url, format_id=format_id)
            self._jobs[job_id] = job
            return job

    async def get_job(self, job_id: str) -> Optional[DownloaderJob]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def update_job(
        self,
        job_id: str,
        *,
        state: Optional[str] = None,
        progress: Optional[float] = None,
        title: Optional[str] = None,
        error: Optional[str] = None,
        output_path: Optional[Path] = None,
        ext: Optional[str] = None,
    ) -> Optional[DownloaderJob]:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if state is not None:
                job.state = state
            if progress is not None:
                job.progress = progress
            if title is not None:
                job.title = title
            if error is not None:
                job.error = error
            if output_path is not None:
                job.output_path = output_path
            if ext is not None:
                job.ext = ext
            return job

    async def issue_file_token(self, job_id: str) -> str:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            nonce = secrets.token_urlsafe(16)
            token = self._sign_token(job_id, nonce)
            job.file_token = token
            expires = time.time() + settings.DOWNLOADER_FILE_TOKEN_TTL_SEC
            self._tokens[token] = (job_id, expires, 0)
            return token

    async def resolve_file_path(self, token: str) -> Optional[tuple[Path, DownloaderJob]]:
        job_id = self._verify_token(token)
        if not job_id:
            return None
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job or not job.output_path:
                return None
            path = job.output_path
            if not path.exists():
                return None
            entry = self._tokens.get(token)
            if entry:
                jid, expires, uses = entry
                if time.time() > expires:
                    return None
                self._tokens[token] = (jid, expires, uses + 1)
            return path, job

    async def cancel_job(self, job_id: str) -> Optional[DownloaderJob]:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            job.cancel_event.set()
            if job.task and not job.task.done():
                job.task.cancel()
            job.state = "canceled"
            if job.output_path and job.output_path.parent.exists():
                try:
                    for p in job.output_path.parent.iterdir():
                        if p.is_file():
                            p.unlink(missing_ok=True)
                except OSError:
                    pass
            return job

    async def cleanup_old_jobs(self, max_age_sec: int = 3600) -> int:
        now = time.time()
        removed = 0
        async with self._lock:
            stale = [
                jid
                for jid, j in self._jobs.items()
                if now - j.created_at > max_age_sec
                and j.state in ("ready", "failed", "canceled")
            ]
            for jid in stale:
                job = self._jobs.pop(jid, None)
                if job and job.output_path:
                    try:
                        parent = job.output_path.parent
                        if parent.exists():
                            for p in parent.iterdir():
                                p.unlink(missing_ok=True)
                            parent.rmdir()
                    except OSError:
                        pass
                removed += 1
            expired_tokens = [
                t
                for t, (_, exp, _) in self._tokens.items()
                if now > exp
            ]
            for t in expired_tokens:
                del self._tokens[t]
        return removed


job_store = DownloaderJobStore()
