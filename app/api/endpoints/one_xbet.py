from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.core import cache
from app.models.one_xbet_models import OneXbetDataPayload, OneXbetDataResponse

router = APIRouter()

ONE_XBET_SITE_URL = "https://1xlite-08668.world/en"
ONE_XBET_LIVE_URL = "https://1xlite-08668.world/en/live"
ONE_XBET_OFFICIAL_LIVE_BEST_GAMES_URL = (
    "https://5y7xvr1pm3q.com/MainFeedLive/mobile/v1/bestGames"
)
ONE_XBET_OFFICIAL_PREMATCH_BEST_GAMES_URL = (
    "https://5y7xvr1pm3q.com/MainFeedLine/mobile/v1/bestGames"
)
ONE_XBET_OFFICIAL_BEST_GAMES_PARAMS = {
    "cfView": "3",
    "country": "19",
    "gr": "1357",
    "lng": "en_GB",
    "ref": "1",
    "whence": "22",
}
ONE_XBET_SOURCE_CANDIDATES = (
    "https://1xlite-08668.world/data/app.json",
    "https://1xlite-08668.world/data/sports.json",
)
ONE_XBET_DATA_BASE_URL = "https://1xlite-08668.world/data/"
ONE_XBET_MEDIA_CDN_BASE = "https://v3.traincdn.com"
ONE_XBET_CACHE_KEY = "one_xbet:data:live"
ONE_XBET_LAST_GOOD_CACHE_KEY = "one_xbet:data:live:last_good"
ONE_XBET_PREMATCH_CACHE_KEY = "one_xbet:data:prematch"
ONE_XBET_PREMATCH_LAST_GOOD_CACHE_KEY = "one_xbet:data:prematch:last_good"
ONE_XBET_LIVE_VIDEO_CACHE_PREFIX = "one_xbet:live-video:"
ONE_XBET_XMEDIAGET_EDGE_HOSTS = (
    "edge14.xmediaget.com",
    "edge3.xmediaget.com",
    "edge1.xmediaget.com",
    *(f"edge{n}.xmediaget.com" for n in range(2, 21) if n not in {3, 14}),
)
ONE_XBET_LIVE_VIDEO_AI = 3
ONE_XBET_LIVE_VIDEO_AV = 1025
ONE_XBET_LIVE_VIDEO_UI = 0
ONE_XBET_LIVE_VIDEO_VARIANT = "1"

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


def _strip_html_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


def _sport_slug_from_live_href(href: str) -> str:
    m = re.match(r"/en/live/([^/]+)/", href.strip(), flags=re.IGNORECASE)
    return (m.group(1) or "").strip().lower() if m else ""


_DASHBOARD_EVENT_HREF_RE = re.compile(
    r"""(?i)(?:href|data-href)\s*=\s*(["'])(/en/live/[^"']+/\d+-[^"']+)\1""",
)


def _build_payload_from_live_html(html: str) -> OneXbetDataPayload:
    # Fallback parser for public /en/live page when JSON/API feeds are blocked.
    # We extract event links and labels and map them into app-friendly event rows.
    anchor_matches = re.findall(
        r"""<a[^>]+href=["'](/en/live/[^"']+)["'][^>]*>(.*?)</a>""",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for href, raw_label in anchor_matches:
        label = _strip_html_tags(raw_label)
        if not label:
            continue
        # Event pages usually end with ".../<event_id>-slug"
        event_id_match = re.search(r"/(\d+)-[^/]+$", href)
        event_id = event_id_match.group(1) if event_id_match else ""
        if not event_id or event_id in seen_ids:
            continue
        seen_ids.add(event_id)

        absolute_url = f"https://1xlite-08668.world{href}"
        title = re.sub(r"\s+", " ", label).strip()

        # Best-effort league extraction from URL path segment:
        # /en/live/football/<league-segment>/<event-id>-...
        league = ""
        league_match = re.search(r"/en/live/[^/]+/([^/]+)/\d+-", href)
        if league_match:
            league = league_match.group(1).replace("-", " ").strip()

        sport = _sport_slug_from_live_href(href)

        events.append(
            {
                "id": event_id,
                "event_id": event_id,
                "title": title,
                "eventName": title,
                "league": league or "Live",
                "sport": sport,
                "status": "live",
                "stream_url": absolute_url,
                "source": "live-page-fallback",
            }
        )

    return OneXbetDataPayload(events=events)


def _build_payload_from_dashboard_cards(html: str) -> OneXbetDataPayload:
    # Prefer scanning concrete event hrefs instead of splitting on the first </li>,
    # because nested markup (common on some sports) breaks naive <li> boundaries.
    events: list[dict[str, Any]] = []
    seen: set[str] = set()

    for href_match in _DASHBOARD_EVENT_HREF_RE.finditer(html):
        href = href_match.group(2).strip()
        event_id_match = re.search(r"/(\d+)-[^/]+$", href)
        event_id = event_id_match.group(1) if event_id_match else ""
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)

        pos = href_match.start()
        chunk_start = max(0, pos - 7000)
        chunk_end = min(len(html), pos + 11000)
        chunk = html[chunk_start:chunk_end]
        local_pos = pos - chunk_start
        prefix = chunk[:local_pos]

        league_labels = re.findall(
            r"""dashboard-champ-name__caption[^>]*>(.*?)</span>""",
            prefix,
            flags=re.IGNORECASE | re.DOTALL,
        )
        league = _strip_html_tags(league_labels[-1]).strip() if league_labels else ""

        league_urls = re.findall(
            r"""dashboard-champ-name__label[^>]*href="(/en/live/[^"]+)\"""",
            prefix,
            flags=re.IGNORECASE,
        )
        league_url = (
            f"https://1xlite-08668.world{league_urls[-1]}" if league_urls else ""
        )

        names = re.findall(
            r"""dashboard-game-team-info__name"[^>]*>(.*?)</span>""",
            chunk,
            flags=re.IGNORECASE | re.DOTALL,
        )
        names = [_strip_html_tags(x).strip() for x in names if _strip_html_tags(x).strip()]
        home = names[0] if len(names) > 0 else ""
        away = names[1] if len(names) > 1 else ""

        logo_urls = re.findall(
            r"""src="([^"]*logo_teams[^"]+)\"""",
            chunk,
            flags=re.IGNORECASE,
        )
        home_logo = logo_urls[0] if len(logo_urls) > 0 else ""
        away_logo = logo_urls[1] if len(logo_urls) > 1 else ""

        scores = re.findall(
            r"""ui-game-scores__num"[^>]*>(.*?)</span>""",
            chunk,
            flags=re.IGNORECASE | re.DOTALL,
        )
        scores = [_strip_html_tags(x).strip() for x in scores if _strip_html_tags(x).strip()]
        home_score = scores[0] if len(scores) > 0 else ""
        away_score = scores[1] if len(scores) > 1 else ""

        more_match = re.search(
            r"""dashboard-game-more__count"[^>]*>([^<]+)""",
            chunk,
            flags=re.IGNORECASE,
        )
        more_count = _strip_html_tags(more_match.group(1)).strip() if more_match else ""

        status_match = re.search(
            r"""dashboard-game-info__time"[^>]*>(.*?)</span>""",
            chunk,
            flags=re.IGNORECASE | re.DOTALL,
        )
        status = _strip_html_tags(status_match.group(1)).strip() if status_match else ""
        if not status:
            status = "Event in progress"

        title = f"{home} vs {away}".strip() if (home or away) else "Live match"
        event_url = f"https://1xlite-08668.world{href}"
        sport = _sport_slug_from_live_href(href)

        events.append(
            {
                "id": event_id,
                "event_id": event_id,
                "title": title,
                "eventName": title,
                "teamAName": home,
                "teamBName": away,
                "teamALogo": home_logo,
                "teamBLogo": away_logo,
                "teamAScore": home_score,
                "teamBScore": away_score,
                "league": league or "Live",
                "league_url": league_url,
                "sport": sport,
                "status": "live",
                "status_text": status,
                "more_markets": more_count,
                "event_url": event_url,
                "stream_url": event_url,
                "source": "live-dashboard-fallback",
            }
        )

    return OneXbetDataPayload(events=events)


def _merge_event_lists(
    primary: list[dict[str, Any]], secondary: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    # Keep primary order, then append unseen events from secondary.
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _event_key(item: dict[str, Any]) -> str:
        for key in ("id", "event_id", "match_id", "eventUrl", "event_url", "url"):
            value = item.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        return ""

    for event in primary:
        if not isinstance(event, dict):
            continue
        key = _event_key(event)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(event)

    for event in secondary:
        if not isinstance(event, dict):
            continue
        key = _event_key(event)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(event)

    return merged


def _resolve_media_url(value: str) -> str:
    v = value.strip()
    if not v:
        return v
    if v.startswith("http://") or v.startswith("https://"):
        return v
    if v.startswith("/"):
        return f"{ONE_XBET_MEDIA_CDN_BASE}{v}"
    if v.startswith("sfiles/"):
        return f"{ONE_XBET_MEDIA_CDN_BASE}/{v}"
    return v


def _resolve_media_urls_in_payload(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _resolve_media_urls_in_payload(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_media_urls_in_payload(v) for v in obj]
    if isinstance(obj, str):
        v = obj.strip()
        if v.startswith("/sfiles/") or v.startswith("sfiles/"):
            return _resolve_media_url(v)
    return obj


def _build_payload_from_official_best_games(root: Any) -> OneXbetDataPayload:
    if not isinstance(root, list):
        raise HTTPException(status_code=502, detail="Unexpected official bestGames structure")
    # Passthrough official items, only resolving relative media paths to CDN URLs.
    events = [
        _resolve_media_urls_in_payload(dict(item))
        for item in root
        if isinstance(item, dict)
    ]
    return OneXbetDataPayload(events=events)


async def _fetch_official_best_games(url: str) -> OneXbetDataPayload:
    headers = {
        "User-Agent": "okhttp/4.12.0",
        "Accept-Encoding": "gzip",
        "Connection": "Keep-Alive",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        res = await client.get(
            url,
            params=ONE_XBET_OFFICIAL_BEST_GAMES_PARAMS,
            headers=headers,
        )
    if res.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Official feed HTTP {res.status_code}",
        )
    return _build_payload_from_official_best_games(res.json())


async def _get_official_feed_response(
    *,
    feed_url: str,
    cache_key: str,
    last_good_key: str,
) -> OneXbetDataResponse:
    cached = await cache.get(cache_key)
    if cached:
        return OneXbetDataResponse.model_validate(cached)

    try:
        payload = await _fetch_official_best_games(feed_url)
        response = OneXbetDataResponse(data=payload)
        dumped = response.model_dump()
        await cache.set(cache_key, dumped, ttl_seconds=60)
        await cache.set(last_good_key, dumped, ttl_seconds=60 * 60 * 24 * 7)
        return response
    except Exception:
        last_good = await cache.get(last_good_key)
        if last_good:
            fallback = OneXbetDataResponse.model_validate(last_good)
            fallback.status = "degraded-cache"
            return fallback
        return OneXbetDataResponse(
            status="degraded-empty",
            data=OneXbetDataPayload(events=[]),
        )


async def _get_merged_cached_payload() -> OneXbetDataPayload:
    events: list[dict[str, Any]] = []
    for key in (ONE_XBET_CACHE_KEY, ONE_XBET_PREMATCH_CACHE_KEY):
        cached = await cache.get(key)
        if not cached:
            continue
        data = OneXbetDataResponse.model_validate(cached).data
        events.extend(data.events)
    return OneXbetDataPayload(events=events)


def _assign_display_order(events: list[dict[str, Any]]) -> None:
    for i, item in enumerate(events):
        if isinstance(item, dict):
            item["display_order"] = i


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
    headers = {
        "User-Agent": "okhttp/4.12.0",
        "Accept-Encoding": "gzip",
        "Connection": "Keep-Alive",
    }
    official_payload: OneXbetDataPayload | None = None
    json_payload: OneXbetDataPayload | None = None
    live_dashboard_payload: OneXbetDataPayload | None = None
    live_html_payload: OneXbetDataPayload | None = None

    # First: official bestGames endpoint.
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            official_res = await client.get(
                ONE_XBET_OFFICIAL_LIVE_BEST_GAMES_URL,
                params=ONE_XBET_OFFICIAL_BEST_GAMES_PARAMS,
                headers=headers,
            )
        if official_res.status_code == 200:
            official_payload = _build_payload_from_official_best_games(official_res.json())
    except Exception:
        pass

    # First: attempt source JSON payload (often stable, but may be a subset).
    try:
        source_url = await _resolve_source_url()
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            res = await client.get(source_url, headers=headers)
        if res.status_code == 200:
            root = res.json()
            if isinstance(root, dict):
                payload = root
            elif isinstance(root, list) and root and isinstance(root[0], dict):
                payload = root[0]
            else:
                payload = None

            if isinstance(payload, dict):
                events_tokens = _parse_token_list(payload.get("events"))
                events: list[dict[str, Any]] = []
                for t in events_tokens:
                    events.extend(_extract_maps(_decode_token(t)))

                json_payload = OneXbetDataPayload(events=events)
    except Exception:
        pass

    # Second: always try public /en/live cards and merge to avoid featured-only subsets.
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            live_res = await client.get(ONE_XBET_LIVE_URL, headers=headers)
        if live_res.status_code == 200:
            live_dashboard_payload = _build_payload_from_dashboard_cards(live_res.text)
            if not live_dashboard_payload.events:
                live_html_payload = _build_payload_from_live_html(live_res.text)
    except Exception:
        pass

    # User-requested behavior: if official feed exists, return it raw as-is.
    if official_payload and official_payload.events:
        return official_payload

    # Fallback behavior when official feed is unavailable.
    result: OneXbetDataPayload | None = None
    if official_payload and live_dashboard_payload and json_payload:
        merged = _merge_event_lists(official_payload.events, live_dashboard_payload.events)
        merged = _merge_event_lists(merged, json_payload.events)
        result = OneXbetDataPayload(events=merged)
    elif official_payload and live_dashboard_payload:
        result = OneXbetDataPayload(
            events=_merge_event_lists(official_payload.events, live_dashboard_payload.events),
        )
    elif official_payload and json_payload:
        result = OneXbetDataPayload(
            events=_merge_event_lists(official_payload.events, json_payload.events),
        )
    elif official_payload:
        result = official_payload
    elif json_payload and live_dashboard_payload:
        result = OneXbetDataPayload(
            events=_merge_event_lists(live_dashboard_payload.events, json_payload.events),
        )
    elif live_dashboard_payload and live_dashboard_payload.events:
        result = live_dashboard_payload
    elif json_payload:
        result = json_payload
    elif live_html_payload and live_html_payload.events:
        result = live_html_payload

    if result is None:
        raise HTTPException(status_code=502, detail="Could not build 1XBet payload")
    _assign_display_order(result.events)
    return result


@router.get("/1xbet/live-data", response_model=OneXbetDataResponse, tags=["1XBet"])
async def get_one_xbet_live_data() -> OneXbetDataResponse:
    return await _get_official_feed_response(
        feed_url=ONE_XBET_OFFICIAL_LIVE_BEST_GAMES_URL,
        cache_key=ONE_XBET_CACHE_KEY,
        last_good_key=ONE_XBET_LAST_GOOD_CACHE_KEY,
    )


@router.get("/1xbet/prematch-data", response_model=OneXbetDataResponse, tags=["1XBet"])
async def get_one_xbet_prematch_data() -> OneXbetDataResponse:
    return await _get_official_feed_response(
        feed_url=ONE_XBET_OFFICIAL_PREMATCH_BEST_GAMES_URL,
        cache_key=ONE_XBET_PREMATCH_CACHE_KEY,
        last_good_key=ONE_XBET_PREMATCH_LAST_GOOD_CACHE_KEY,
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


def _extract_video_id_from_event(event: dict[str, Any]) -> str | None:
    video = event.get("video")
    if not isinstance(video, dict):
        return None
    if video.get("enabled") is not True:
        return None
    raw_id = video.get("id")
    if raw_id is None:
        return None
    video_id = str(raw_id).strip()
    if not video_id.isdigit():
        return None
    return video_id


def _normalize_xmediaget_edge_host(edge: str | None) -> str | None:
    if edge is None:
        return None
    host = edge.strip().lower().removeprefix("https://").removeprefix("http://")
    host = host.split("/", 1)[0]
    if not host:
        return None
    if "." not in host:
        host = f"{host}.xmediaget.com"
    return host


def _xmediaget_edge_candidates(preferred: str | None = None) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(host: str | None) -> None:
        if not host or host in seen:
            return
        seen.add(host)
        ordered.append(host)

    _add(_normalize_xmediaget_edge_host(preferred))
    for host in ONE_XBET_XMEDIAGET_EDGE_HOSTS:
        _add(host)
    return ordered


def _build_xmediaget_hls_playlist_url(
    edge_host: str, video_id: str, auth_query: str
) -> str:
    query = auth_query.strip().lstrip("?")
    base = (
        f"https://{edge_host}/hls-live/{video_id}/"
        f"{ONE_XBET_LIVE_VIDEO_VARIANT}/mediaplaylist.m3u8"
    )
    return f"{base}?{query}" if query else base


async def _fetch_xmediaget_hls_auth_query(
    edge_host: str, video_id: str
) -> str | None:
    params = {
        "ai": ONE_XBET_LIVE_VIDEO_AI,
        "av": ONE_XBET_LIVE_VIDEO_AV,
        "ui": ONE_XBET_LIVE_VIDEO_UI,
    }
    auth_path = (
        f"/hls-live-auth/{video_id}/{ONE_XBET_LIVE_VIDEO_VARIANT}"
        f"?{urlencode(params)}"
    )
    headers = {
        "User-Agent": "okhttp/4.12.0",
        "Accept-Encoding": "gzip",
        "Connection": "Keep-Alive",
    }
    url = f"https://{edge_host}{auth_path}"
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        res = await client.get(url, headers=headers)
    if res.status_code != 200:
        return None
    query = res.text.strip()
    if not query or "s=" not in query or "t=" not in query:
        return None
    return query


async def _resolve_xmediaget_live_video_url(
    video_id: str,
    *,
    preferred_edge: str | None = None,
) -> dict[str, Any]:
    if not video_id.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Only numeric xmediaget video ids are supported for HLS live playback",
        )

    cache_key = f"{ONE_XBET_LIVE_VIDEO_CACHE_PREFIX}{video_id}"
    cached = await cache.get(cache_key)
    if isinstance(cached, dict) and cached.get("stream_url"):
        return cached

    last_error = "No xmediaget edge returned a signed playlist"
    for edge_host in _xmediaget_edge_candidates(preferred_edge):
        auth_query = await _fetch_xmediaget_hls_auth_query(edge_host, video_id)
        if not auth_query:
            continue
        stream_url = _build_xmediaget_hls_playlist_url(
            edge_host, video_id, auth_query
        )
        result = {
            "status": "success",
            "video_id": video_id,
            "stream_url": stream_url,
            "resolved_url": stream_url,
            "format": "hls",
            "edge": edge_host,
            "variant": ONE_XBET_LIVE_VIDEO_VARIANT,
            "auth_query": auth_query,
        }
        await cache.set(cache_key, result, ttl_seconds=45)
        return result

    raise HTTPException(status_code=502, detail=last_error)


async def _resolve_live_video_for_event(
    event_id: str,
    *,
    video_id: str | None = None,
    preferred_edge: str | None = None,
) -> dict[str, Any]:
    resolved_video_id = (video_id or "").strip()
    event: dict[str, Any] | None = None

    if not resolved_video_id:
        payload = await _get_merged_cached_payload()
        event = _find_event(payload, event_id)
        if event is None:
            payload = await _get_cached_or_build_payload()
            event = _find_event(payload, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        resolved_video_id = _extract_video_id_from_event(event) or ""
        if not resolved_video_id:
            raise HTTPException(
                status_code=404,
                detail="Event has no enabled numeric live video id",
            )

    result = await _resolve_xmediaget_live_video_url(
        resolved_video_id,
        preferred_edge=preferred_edge,
    )
    result["event_id"] = event_id
    if event is not None:
        result["event"] = event
    return result


@router.get("/1xbet/live-video", tags=["1XBet"])
async def resolve_one_xbet_live_video(
    eventId: str | None = Query(
        None, description="1XBet event id from live/prematch feeds"
    ),
    videoId: str | None = Query(
        None, description="Numeric xmediaget video id (video.id from feed)"
    ),
    edge: str | None = Query(
        None,
        description="Optional xmediaget edge host (e.g. edge14 or edge14.xmediaget.com)",
    ),
) -> dict[str, Any]:
    target_event_id = (eventId or "").strip()
    target_video_id = (videoId or "").strip()
    if not target_event_id and not target_video_id:
        raise HTTPException(
            status_code=400,
            detail="Provide eventId and/or videoId",
        )
    if not target_event_id:
        target_event_id = target_video_id
    return await _resolve_live_video_for_event(
        target_event_id,
        video_id=target_video_id or None,
        preferred_edge=edge,
    )


async def _get_cached_or_build_payload() -> OneXbetDataPayload:
    merged = await _get_merged_cached_payload()
    if merged.events:
        return merged

    try:
        live = await _fetch_official_best_games(ONE_XBET_OFFICIAL_LIVE_BEST_GAMES_URL)
        response = OneXbetDataResponse(data=live)
        dumped = response.model_dump()
        await cache.set(ONE_XBET_CACHE_KEY, dumped, ttl_seconds=60)
        await cache.set(ONE_XBET_LAST_GOOD_CACHE_KEY, dumped, ttl_seconds=60 * 60 * 24 * 7)
        return live
    except Exception:
        last_good = await cache.get(ONE_XBET_LAST_GOOD_CACHE_KEY)
        if last_good:
            return OneXbetDataResponse.model_validate(last_good).data
        return OneXbetDataPayload(events=[])

