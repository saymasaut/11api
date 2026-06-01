"""URL validation and SSRF protection for the downloader module."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException


_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
    }
)


def _is_private_ip(hostname: str) -> bool:
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        return False


def _resolve_host_ips(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
        return [info[4][0] for info in infos]
    except socket.gaierror:
        return []


def validate_downloader_url(url: str) -> str:
    """
    Validate user-supplied URL for downloader endpoints.
    Raises HTTPException on invalid or blocked URLs.
    Returns normalized URL string.
    """
    text = (url or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="URL is required")

    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail="Only http and https URLs are supported",
        )

    host = (parsed.hostname or "").lower().strip()
    if not host:
        raise HTTPException(status_code=400, detail="Invalid URL host")

    if host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        raise HTTPException(status_code=422, detail="URL host is not allowed")

    if _is_private_ip(host):
        raise HTTPException(status_code=422, detail="Private network URLs are not allowed")

    # Resolve DNS and block private IPs (SSRF)
    for ip in _resolve_host_ips(host):
        if _is_private_ip(ip):
            raise HTTPException(
                status_code=422,
                detail="URL resolves to a private network address",
            )

    return text
