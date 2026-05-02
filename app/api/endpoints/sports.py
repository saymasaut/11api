from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.core import cache
from app.models.sports_models import SportsDataPayload, SportsDataResponse

router = APIRouter()

SPORTS_SOURCE_URL = "https://gbplayer.cc/data/app.json"
SPORTS_DATA_BASE_URL = "https://gbplayer.cc/data/"
SPORTS_CACHE_KEY = "sports:data:decoded"

_PLAIN_ALPHA = "aAbBcCdDeEfFgGhHiIjJkKlLmMnNoOpPqQrRsStTuUvVwWxXyYzZ"
_CODED_ALPHA = "fFgGjJkKaApPbBmMoOzZeEnNcCdDrRqQtTvVuUxXhHiIwWyYlLsS"


def _identity(s: str) -> str:
    return s


def _reverse(s: str) -> str:
    return s[::-1]


def _rot13(s: str) -> str:
    out: list[str] = []
    for ch in s:
        o = ord(ch)
        if 65 <= o <= 90:
            out.append(chr(((o - 65 + 13) % 26) + 65))
        elif 97 <= o <= 122:
            out.append(chr(((o - 97 + 13) % 26) + 97))
        else:
            out.append(ch)
    return "".join(out)


def _sportzfy_alphabet_swap(s: str) -> str:
    table = {ord(c): _PLAIN_ALPHA[i] for i, c in enumerate(_CODED_ALPHA)}
    return s.translate(table)


_TRANSFORMS = (
    _identity,
    _sportzfy_alphabet_swap,
    _reverse,
    _rot13,
    lambda s: _rot13(_reverse(s)),
    lambda s: _reverse(_rot13(s)),
)


def _sanitize_b64(value: str) -> str:
    v = value.strip().replace("\n", "").replace("\r", "")
    v = v.replace("-", "+").replace("_", "/")
    pad = (-len(v)) % 4
    return v + ("=" * pad)


def _try_b64decode(value: str) -> bytes | None:
    try:
        return base64.b64decode(_sanitize_b64(value), validate=False)
    except Exception:
        return None


def _try_json_parse(value: str) -> Any | None:
    t = value.strip()
    if not t or t[0] not in "{[":
        return None
    try:
        return json.loads(t)
    except Exception:
        return None


def _looks_like_base64(value: str) -> bool:
    t = value.strip()
    return len(t) >= 16 and bool(re.fullmatch(r"[A-Za-z0-9+/=_-]+", t))


def _try_parse_bytes(raw: bytes, depth: int = 0) -> Any | None:
    if depth > 2:
        return None
    texts = []
    try:
        texts.append(raw.decode("utf-8", errors="ignore"))
    except Exception:
        pass
    try:
        texts.append(raw.decode("latin-1", errors="ignore"))
    except Exception:
        pass
    for text in texts:
        candidates = [fn(text) for fn in _TRANSFORMS]
        for c in candidates:
            parsed = _try_json_parse(c)
            if parsed is not None:
                return parsed
        for c in candidates:
            if not _looks_like_base64(c):
                continue
            decoded = _try_b64decode(c)
            if decoded is None:
                continue
            parsed = _try_parse_bytes(decoded, depth + 1)
            if parsed is not None:
                return parsed
    return None


def _decode_token(token: str) -> Any | None:
    raw = token.strip().replace("\n", "").replace("\r", "")
    if not raw:
        return None
    for transform in _TRANSFORMS:
        candidate = transform(raw)
        decoded = _try_b64decode(candidate)
        if decoded is None:
            continue
        parsed = _try_parse_bytes(decoded)
        if parsed is not None:
            return parsed
    return None


def _parse_token_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except Exception:
            return []
    return []


def _extract_maps(decoded: Any) -> list[dict[str, Any]]:
    if isinstance(decoded, dict):
        return [_normalize_map_links(decoded)]
    if isinstance(decoded, list):
        return [_normalize_map_links(item) for item in decoded if isinstance(item, dict)]
    return []


def _to_absolute_data_url(value: str) -> str:
    link = value.strip()
    if not link:
        return link
    if link.startswith("http://") or link.startswith("https://"):
        return link
    return f"{SPORTS_DATA_BASE_URL}{link.lstrip('/')}"


def _normalize_map_links(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    for key in ("links", "channel", "api"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = _to_absolute_data_url(value)
        elif isinstance(value, list):
            normalized[key] = [_to_absolute_data_url(v) if isinstance(v, str) else v for v in value]
    return normalized


def _extract_urls(text: str) -> list[str]:
    urls = re.findall(r"(https?://[^\s\"']+|rtmp://[^\s\"']+)", text, flags=re.IGNORECASE)
    # preserve order, unique
    deduped: list[str] = []
    for u in urls:
        if u not in deduped:
            deduped.append(u.strip())
    return deduped


def _is_valid_stream_url(value: str) -> bool:
    v = value.strip()
    if not v:
        return False
    lower = v.lower()
    if lower in {"http", "https", "http:", "https:", "http:/", "https:/"}:
        return False
    if lower.endswith(".mpd") or ".mpd?" in lower:
        return False
    if not (lower.startswith("http://") or lower.startswith("https://") or lower.startswith("rtmp://")):
        return False
    parts = v.split("://", 1)
    if len(parts) != 2 or not parts[1].strip("/"):
        return False
    return True


def _filter_stream_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    for u in urls:
        v = u.strip()
        if not _is_valid_stream_url(v):
            continue
        if v not in out:
            out.append(v)
    return out


def _decode_to_urls(decoded: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(decoded, str):
        urls.extend(_extract_urls(decoded))
    elif isinstance(decoded, dict):
        for k in ("stream_url", "url", "play_url", "link", "hls_url", "m3u8"):
            v = decoded.get(k)
            if isinstance(v, str) and v.strip():
                urls.append(v.strip())
    elif isinstance(decoded, list):
        for it in decoded:
            urls.extend(_decode_to_urls(it))
    # dedupe
    out: list[str] = []
    for u in urls:
        if u not in out:
            out.append(u)
    return _filter_stream_urls(out)


async def _build_sports_payload() -> SportsDataPayload:
    async with httpx.AsyncClient(timeout=20.0) as client:
        res = await client.get(
            SPORTS_SOURCE_URL,
            headers={
                "User-Agent": "okhttp/4.12.0",
                "Accept-Encoding": "gzip",
                "Connection": "Keep-Alive",
            },
        )
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Sports source HTTP {res.status_code}")

    try:
        root = res.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Invalid sports source payload") from exc

    if isinstance(root, dict):
        payload = root
    elif isinstance(root, list) and root and isinstance(root[0], dict):
        payload = root[0]
    else:
        raise HTTPException(status_code=502, detail="Unexpected sports source structure")

    events_tokens = _parse_token_list(payload.get("events"))
    categories_tokens = _parse_token_list(payload.get("categories"))
    highlights_tokens = _parse_token_list(payload.get("highlights"))

    events: list[dict[str, Any]] = []
    categories: list[dict[str, Any]] = []
    highlights: list[dict[str, Any]] = []

    for t in events_tokens:
        events.extend(_extract_maps(_decode_token(t)))
    for t in categories_tokens:
        categories.extend(_extract_maps(_decode_token(t)))
    categories = [
        c
        for c in categories
        if str(c.get("type", "")).strip().lower() != "custom"
    ]
    for t in highlights_tokens:
        highlights.extend(_extract_maps(_decode_token(t)))

    return SportsDataPayload(
        source_url=SPORTS_SOURCE_URL,
        events=events,
        categories=categories,
        highlights=highlights,
    )


@router.get("/sports/data", response_model=SportsDataResponse, tags=["Sports"])
async def get_sports_data() -> SportsDataResponse:
    cached = await cache.get(SPORTS_CACHE_KEY)
    if cached:
        return SportsDataResponse.model_validate(cached)

    payload = await _build_sports_payload()
    response = SportsDataResponse(data=payload)
    await cache.set(SPORTS_CACHE_KEY, response.model_dump(), ttl_seconds=300)
    return response


@router.get("/sports/resolve-link", tags=["Sports"])
async def resolve_sports_link(url: str = Query(..., description="Sports stream or pro/prohigh json URL")) -> dict[str, Any]:
    absolute = url.strip()
    if not absolute.startswith("http://") and not absolute.startswith("https://"):
        absolute = _to_absolute_data_url(absolute)

    lower = absolute.lower()
    is_pro_json = lower.endswith(".json") and (
        "/data/pro/" in lower or "/data/prohigh/" in lower or "/pro/" in lower or "/prohigh/" in lower
    )
    is_channels_json = lower.endswith(".json") and ("/data/channels/" in lower or "/channels/" in lower)
    if not is_pro_json and not is_channels_json:
        direct_urls = _filter_stream_urls([absolute])
        return {
            "status": "success",
            "url": absolute,
            "urls": direct_urls,
            "resolved_url": direct_urls[0] if direct_urls else None,
            "isResolved": False,
        }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                absolute,
                headers={
                    "User-Agent": "okhttp/4.12.0",
                    "Accept-Encoding": "gzip",
                    "Connection": "Keep-Alive",
                },
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Upstream HTTP {resp.status_code}")
        payload = resp.json()

        if is_channels_json:
            urls: list[str] = []
            items: list[dict[str, Any]] = []
            if isinstance(payload, list):
                for entry in payload:
                    if not isinstance(entry, dict):
                        continue
                    token = str(entry.get("channel", "")).strip()
                    if not token:
                        continue
                    decoded = _decode_token(token)
                    decoded_urls = _decode_to_urls(decoded)
                    for stream_url in decoded_urls:
                        if stream_url not in urls:
                            urls.append(stream_url)
                    if isinstance(decoded, dict):
                        item = dict(decoded)
                        if decoded_urls and "stream_url" not in item:
                            item["stream_url"] = decoded_urls[0]
                        items.append(item)
            urls = _filter_stream_urls(urls)
            return {
                "status": "success",
                "url": absolute,
                "urls": urls,
                "items": items,
                "resolved_url": urls[0] if urls else None,
                "isResolved": True,
            }

        links_token = str(payload.get("links", "")).strip() if isinstance(payload, dict) else ""
        if not links_token:
            return {"status": "success", "url": absolute, "urls": [], "isResolved": True}
        decoded = _decode_token(links_token)
        urls = _filter_stream_urls(_decode_to_urls(decoded))
        return {
            "status": "success",
            "url": absolute,
            "urls": urls,
            "resolved_url": urls[0] if urls else None,
            "isResolved": True,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to resolve sports link: {exc}") from exc


@router.get("/sports/channels", tags=["Sports"])
async def get_sports_channels(url: str = Query(..., description="Channels json url")) -> dict[str, Any]:
    absolute = url.strip()
    if not absolute.startswith("http://") and not absolute.startswith("https://"):
        absolute = _to_absolute_data_url(absolute)

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                absolute,
                headers={
                    "User-Agent": "okhttp/4.12.0",
                    "Accept-Encoding": "gzip",
                    "Connection": "Keep-Alive",
                },
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Upstream HTTP {resp.status_code}")

        payload = resp.json()
        if not isinstance(payload, list):
            raise HTTPException(status_code=502, detail="Channels payload is not a list")

        items: list[dict[str, Any]] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            token = str(entry.get("channel", "")).strip()
            if not token:
                continue
            decoded = _decode_token(token)
            if isinstance(decoded, dict):
                items.append(decoded)
            elif isinstance(decoded, str):
                urls = _extract_urls(decoded)
                if urls:
                    title = decoded.splitlines()[0].strip() if decoded.splitlines() else "Channel"
                    items.append({"title": title, "stream_url": urls[0]})

        return {"status": "success", "url": absolute, "items": items}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to load channels: {exc}") from exc
