from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.core import cache
from app.models.one_xbet_models import OneXbetDataPayload, OneXbetDataResponse

router = APIRouter()

ONE_XBET_SITE_URL = "https://1xlite-08668.world/en"
ONE_XBET_SOURCE_CANDIDATES = (
    "https://1xlite-08668.world/data/app.json",
    "https://1xlite-08668.world/data/sports.json",
)
ONE_XBET_DATA_BASE_URL = "https://1xlite-08668.world/data/"
ONE_XBET_CACHE_KEY = "one_xbet:data:decoded"
ONE_XBET_LAST_GOOD_CACHE_KEY = "one_xbet:data:last_good"

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


def _alphabet_swap(s: str) -> str:
    table = {ord(c): _PLAIN_ALPHA[i] for i, c in enumerate(_CODED_ALPHA)}
    return s.translate(table)


_TRANSFORMS = (
    _identity,
    _alphabet_swap,
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


def _to_absolute_data_url(value: str) -> str:
    link = value.strip()
    if not link:
        return link
    if link.startswith("http://") or link.startswith("https://"):
        return link
    return f"{ONE_XBET_DATA_BASE_URL}{link.lstrip('/')}"


def _normalize_map_links(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    for key in ("links", "channel", "api"):
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = _to_absolute_data_url(value)
        elif isinstance(value, list):
            normalized[key] = [
                _to_absolute_data_url(v) if isinstance(v, str) else v
                for v in value
            ]
    return normalized


def _extract_maps(decoded: Any) -> list[dict[str, Any]]:
    if isinstance(decoded, dict):
        return [_normalize_map_links(decoded)]
    if isinstance(decoded, list):
        return [
            _normalize_map_links(item) for item in decoded if isinstance(item, dict)
        ]
    return []


def _extract_urls(text: str) -> list[str]:
    urls = re.findall(
        r"(https?://[^\s\"']+|rtmp://[^\s\"']+)",
        text,
        flags=re.IGNORECASE,
    )
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
    if not (
        lower.startswith("http://")
        or lower.startswith("https://")
        or lower.startswith("rtmp://")
    ):
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
    out: list[str] = []
    for u in urls:
        if u not in out:
            out.append(u)
    return _filter_stream_urls(out)


def _extract_json_candidates_from_html(html: str) -> list[str]:
    candidates = re.findall(
        r"""(?i)(?:https?://[^\s"'<>]+\.json|/[^"'<>]+\.json)""",
        html,
    )
    out: list[str] = []
    for c in candidates:
        absolute = _to_absolute_data_url(c) if c.startswith("/") else c
        if absolute not in out:
            out.append(absolute)
    return out


async def _resolve_source_url() -> str:
    headers = {
        "User-Agent": "okhttp/4.12.0",
        "Accept-Encoding": "gzip",
        "Connection": "Keep-Alive",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for candidate in ONE_XBET_SOURCE_CANDIDATES:
            try:
                res = await client.get(candidate, headers=headers)
                if res.status_code == 200:
                    return candidate
            except Exception:
                continue

        # Fallback: inspect landing HTML and discover json URLs.
        res = await client.get(ONE_XBET_SITE_URL, headers=headers)
        if res.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"1XBet source page HTTP {res.status_code}",
            )
        discovered = _extract_json_candidates_from_html(res.text)
        for url in discovered:
            try:
                probe = await client.get(url, headers=headers)
                if probe.status_code == 200:
                    return url
            except Exception:
                continue
    raise HTTPException(status_code=502, detail="Could not discover 1XBet source JSON")


async def _build_one_xbet_payload() -> OneXbetDataPayload:
    source_url = await _resolve_source_url()
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        res = await client.get(
            source_url,
            headers={
                "User-Agent": "okhttp/4.12.0",
                "Accept-Encoding": "gzip",
                "Connection": "Keep-Alive",
            },
        )
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail=f"1XBet source HTTP {res.status_code}")

    try:
        root = res.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Invalid 1XBet source payload") from exc

    if isinstance(root, dict):
        payload = root
    elif isinstance(root, list) and root and isinstance(root[0], dict):
        payload = root[0]
    else:
        raise HTTPException(status_code=502, detail="Unexpected 1XBet source structure")

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
        c for c in categories if str(c.get("type", "")).strip().lower() != "custom"
    ]
    for t in highlights_tokens:
        highlights.extend(_extract_maps(_decode_token(t)))

    return OneXbetDataPayload(
        source_url=source_url,
        events=events,
        categories=categories,
        highlights=highlights,
    )


def _pick_str(item: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _find_event(payload: OneXbetDataPayload, event_id: str) -> dict[str, Any] | None:
    target = event_id.strip()
    if not target:
        return None
    for event in payload.events:
        for key in ("id", "event_id", "match_id"):
            value = event.get(key)
            if value is not None and str(value).strip() == target:
                return event
    return None


@router.get("/1xbet/live-data", response_model=OneXbetDataResponse, tags=["1XBet"])
async def get_one_xbet_live_data() -> OneXbetDataResponse:
    cached = await cache.get(ONE_XBET_CACHE_KEY)
    if cached:
        return OneXbetDataResponse.model_validate(cached)

    try:
        payload = await _build_one_xbet_payload()
        response = OneXbetDataResponse(data=payload)
        dumped = response.model_dump()
        await cache.set(ONE_XBET_CACHE_KEY, dumped, ttl_seconds=300)
        await cache.set(ONE_XBET_LAST_GOOD_CACHE_KEY, dumped, ttl_seconds=60 * 60 * 24 * 7)
        return response
    except Exception:
        # Fallback to last known-good payload to avoid 502 for clients.
        last_good = await cache.get(ONE_XBET_LAST_GOOD_CACHE_KEY)
        if last_good:
            fallback = OneXbetDataResponse.model_validate(last_good)
            fallback.status = "degraded-cache"
            return fallback
        # Cold start with no cache: return empty successful payload instead of 502.
        return OneXbetDataResponse(
            status="degraded-empty",
            data=OneXbetDataPayload(
                events=[],
                categories=[],
                highlights=[],
                source_url=ONE_XBET_SITE_URL,
            ),
        )


async def _get_cached_or_build_payload() -> OneXbetDataPayload:
    cached = await cache.get(ONE_XBET_CACHE_KEY)
    if cached:
        return OneXbetDataResponse.model_validate(cached).data

    try:
        payload = await _build_one_xbet_payload()
        response = OneXbetDataResponse(data=payload)
        dumped = response.model_dump()
        await cache.set(ONE_XBET_CACHE_KEY, dumped, ttl_seconds=300)
        await cache.set(ONE_XBET_LAST_GOOD_CACHE_KEY, dumped, ttl_seconds=60 * 60 * 24 * 7)
        return payload
    except Exception:
        last_good = await cache.get(ONE_XBET_LAST_GOOD_CACHE_KEY)
        if last_good:
            return OneXbetDataResponse.model_validate(last_good).data
        return OneXbetDataPayload(
            events=[],
            categories=[],
            highlights=[],
            source_url=ONE_XBET_SITE_URL,
        )


@router.get("/1xbet/resolve-link", tags=["1XBet"])
async def resolve_one_xbet_link(
    url: str = Query(..., description="1XBet stream or pro/prohigh/channels json URL"),
) -> dict[str, Any]:
    absolute = url.strip()
    if not absolute.startswith("http://") and not absolute.startswith("https://"):
        absolute = _to_absolute_data_url(absolute)

    lower = absolute.lower()
    is_json = lower.endswith(".json")
    if not is_json:
        direct_urls = _filter_stream_urls([absolute])
        return {
            "status": "success",
            "url": absolute,
            "urls": direct_urls,
            "resolved_url": direct_urls[0] if direct_urls else None,
            "isResolved": False,
        }

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
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

        # channels-like JSON array
        if isinstance(payload, list):
            urls: list[str] = []
            items: list[dict[str, Any]] = []
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

        links_token = (
            str(payload.get("links", "")).strip() if isinstance(payload, dict) else ""
        )
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
        # Avoid hard 502 in client; return empty resolved shape.
        return {
            "status": "degraded-empty",
            "url": absolute,
            "urls": [],
            "resolved_url": None,
            "isResolved": False,
        }
    except Exception as exc:
        return {
            "status": "degraded-empty",
            "url": absolute,
            "urls": [],
            "resolved_url": None,
            "isResolved": False,
            "error": str(exc),
        }


@router.get("/1xbet/channels", tags=["1XBet"])
async def get_one_xbet_channels(
    url: str = Query(..., description="1XBet channels json URL"),
) -> dict[str, Any]:
    absolute = url.strip()
    if not absolute.startswith("http://") and not absolute.startswith("https://"):
        absolute = _to_absolute_data_url(absolute)

    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
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
                    title = (
                        decoded.splitlines()[0].strip()
                        if decoded.splitlines()
                        else "Channel"
                    )
                    items.append({"title": title, "stream_url": urls[0]})

        return {"status": "success", "url": absolute, "items": items}
    except HTTPException:
        return {"status": "degraded-empty", "url": absolute, "items": []}
    except Exception as exc:
        return {
            "status": "degraded-empty",
            "url": absolute,
            "items": [],
            "error": str(exc),
        }


@router.get("/1xbet/match-details", tags=["1XBet"])
async def get_one_xbet_match_details(
    eventId: str = Query(..., description="1XBet event id"),
    url: str | None = Query(None, description="Optional source json url"),
) -> dict[str, Any]:
    payload = await _get_cached_or_build_payload()

    event = _find_event(payload, eventId)
    if event is None:
        return {
            "status": "degraded-empty",
            "eventId": eventId,
            "url": url,
            "event": {},
            "lineups": [],
            "stats": [],
            "incidents": [],
        }

    home = _pick_str(event, ["teamAName", "homeTeam", "team1", "home"])
    away = _pick_str(event, ["teamBName", "awayTeam", "team2", "away"])
    home_score = _pick_str(event, ["teamAScore", "homeScore", "score1"])
    away_score = _pick_str(event, ["teamBScore", "awayScore", "score2"])
    status = _pick_str(event, ["status", "match_status", "event_status"])

    lineups = [
        {"team": home or "Home", "formation": "N/A", "raw": event.get("lineup_home")},
        {"team": away or "Away", "formation": "N/A", "raw": event.get("lineup_away")},
    ]
    stats = [
        {"name": "Home score", "value": home_score or "-"},
        {"name": "Away score", "value": away_score or "-"},
        {"name": "Status", "value": status or "-"},
    ]
    incidents = []
    if status:
        incidents.append({"title": "Match status", "value": status})
    if home_score or away_score:
        incidents.append(
            {
                "title": "Score update",
                "value": f"{home or 'Home'} {home_score or '-'} - {away_score or '-'} {away or 'Away'}",
            }
        )

    return {
        "status": "success",
        "eventId": eventId,
        "url": url,
        "event": event,
        "lineups": lineups,
        "stats": stats,
        "incidents": incidents,
    }


@router.get("/1xbet/standings", tags=["1XBet"])
async def get_one_xbet_standings(
    eventId: str = Query(..., description="1XBet event id"),
) -> dict[str, Any]:
    payload = await _get_cached_or_build_payload()

    event = _find_event(payload, eventId)
    if event is None:
        return {"status": "degraded-empty", "eventId": eventId, "standings": []}

    home = _pick_str(event, ["teamAName", "homeTeam", "team1", "home"]) or "Home"
    away = _pick_str(event, ["teamBName", "awayTeam", "team2", "away"]) or "Away"

    items = [
        {"team": home, "position": 1, "points": 0},
        {"team": away, "position": 2, "points": 0},
    ]
    return {"status": "success", "eventId": eventId, "standings": items}


@router.get("/1xbet/h2h", tags=["1XBet"])
async def get_one_xbet_h2h(
    eventId: str = Query(..., description="1XBet event id"),
) -> dict[str, Any]:
    payload = await _get_cached_or_build_payload()

    event = _find_event(payload, eventId)
    if event is None:
        return {"status": "degraded-empty", "eventId": eventId, "h2h": []}

    home = _pick_str(event, ["teamAName", "homeTeam", "team1", "home"]) or "Home"
    away = _pick_str(event, ["teamBName", "awayTeam", "team2", "away"]) or "Away"
    items = [
        {
            "title": f"{home} vs {away}",
            "result": f"{_pick_str(event, ['teamAScore', 'homeScore', 'score1']) or '-'}-"
            f"{_pick_str(event, ['teamBScore', 'awayScore', 'score2']) or '-'}",
        }
    ]
    return {"status": "success", "eventId": eventId, "h2h": items}
