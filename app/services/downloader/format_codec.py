"""Encode/decode backend in format_id: ``ytdlp:best``, ``gallery-dl:all``, ``pytubefix:720p``."""

from __future__ import annotations

BACKEND_YTDLP = "ytdlp"
BACKEND_GALLERY_DL = "gallery-dl"
BACKEND_PYTUBEFIX = "pytubefix"

KNOWN_BACKENDS = frozenset({BACKEND_YTDLP, BACKEND_GALLERY_DL, BACKEND_PYTUBEFIX})


def split_format_id(composite_id: str) -> tuple[str, str]:
    text = (composite_id or "").strip()
    if ":" in text:
        backend, _, fid = text.partition(":")
        backend = backend.strip().lower()
        fid = fid.strip()
        if backend in KNOWN_BACKENDS and fid:
            return backend, fid
    return BACKEND_YTDLP, text


def join_format_id(backend: str, format_id: str) -> str:
    fid = (format_id or "").strip()
    backend = (backend or BACKEND_YTDLP).strip().lower()
    if ":" in fid:
        existing_backend, _, rest = fid.partition(":")
        if existing_backend.lower() in KNOWN_BACKENDS:
            return fid
    return f"{backend}:{fid}"
