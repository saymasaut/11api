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
ONE_XBET_LIVE_URL = "https://1xlite-08668.world/en/live"
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


def _strip_html_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


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

        events.append(
            {
                "id": event_id,
                "event_id": event_id,
                "title": title,
                "eventName": title,
                "league": league or "Live",
                "status": "live",
                "stream_url": absolute_url,
                "source": "live-page-fallback",
            }
        )

    return OneXbetDataPayload(
        source_url=ONE_XBET_SITE_URL,
        events=events,
        categories=[],
        highlights=[],
    )


def _build_payload_from_dashboard_cards(html: str) -> OneXbetDataPayload:
    # Parse dashboard game cards from /en/live markup.
    game_blocks = re.finditer(
        r"""<li[^>]+class="[^"]*dashboard-game[^"]*"[^>]*>(.*?)</li>""",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    events: list[dict[str, Any]] = []
    seen: set[str] = set()

    for match in game_blocks:
        block = match.group(1)
        href_match = re.search(
            r"""href="(/en/live/[^"]+/\d+-[^"]+)\"""",
            block,
            flags=re.IGNORECASE,
        )
        if not href_match:
            continue
        href = href_match.group(1).strip()
        event_id_match = re.search(r"/(\d+)-[^/]+$", href)
        event_id = event_id_match.group(1) if event_id_match else ""
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)

        prefix = html[max(0, match.start() - 7000):match.start()]
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
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        names = [_strip_html_tags(x).strip() for x in names if _strip_html_tags(x).strip()]
        home = names[0] if len(names) > 0 else ""
        away = names[1] if len(names) > 1 else ""

        logo_urls = re.findall(
            r"""src="([^"]*logo_teams[^"]+)\"""",
            block,
            flags=re.IGNORECASE,
        )
        home_logo = logo_urls[0] if len(logo_urls) > 0 else ""
        away_logo = logo_urls[1] if len(logo_urls) > 1 else ""

        scores = re.findall(
            r"""ui-game-scores__num"[^>]*>(.*?)</span>""",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        scores = [_strip_html_tags(x).strip() for x in scores if _strip_html_tags(x).strip()]
        home_score = scores[0] if len(scores) > 0 else ""
        away_score = scores[1] if len(scores) > 1 else ""

        odds = re.findall(
            r"""ui-market__value"[^>]*>(.*?)</span>""",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        odds = [_strip_html_tags(x).strip() for x in odds if _strip_html_tags(x).strip()]
        odd_labels = re.findall(
            r"""ui-market__toggle[^>]*aria-label="([^"]+)\"""",
            block,
            flags=re.IGNORECASE,
        )
        odd1_label = odd_labels[0] if len(odd_labels) > 0 else "1"
        oddx_label = odd_labels[1] if len(odd_labels) > 1 else "X"
        odd2_label = odd_labels[2] if len(odd_labels) > 2 else "2"
        odd1 = odds[0] if len(odds) > 0 else ""
        oddx = odds[1] if len(odds) > 1 else ""
        odd2 = odds[2] if len(odds) > 2 else ""

        more_match = re.search(
            r"""dashboard-game-more__count"[^>]*>([^<]+)""",
            block,
            flags=re.IGNORECASE,
        )
        more_count = _strip_html_tags(more_match.group(1)).strip() if more_match else ""

        status_match = re.search(
            r"""dashboard-game-info__time"[^>]*>(.*?)</span>""",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        status = _strip_html_tags(status_match.group(1)).strip() if status_match else ""
        if not status:
            status = "Event in progress"

        title = f"{home} vs {away}".strip() if (home or away) else "Live match"
        event_url = f"https://1xlite-08668.world{href}"

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
                "status": "live",
                "status_text": status,
                "odd1_label": odd1_label,
                "oddx_label": oddx_label,
                "odd2_label": odd2_label,
                "odd1": odd1,
                "oddx": oddx,
                "odd2": odd2,
                "more_markets": more_count,
                "event_url": event_url,
                "stream_url": event_url,
                "source": "live-dashboard-fallback",
            }
        )

    return OneXbetDataPayload(
        source_url=ONE_XBET_LIVE_URL,
        events=events,
        categories=[],
        highlights=[],
    )


def _extract_first(pattern: str, text: str, flags: int = re.IGNORECASE | re.DOTALL) -> str:
    match = re.search(pattern, text, flags)
    if not match:
        return ""
    return _strip_html_tags(match.group(1)).strip()


async def _parse_event_page_details(event_url: str) -> dict[str, Any]:
    headers = {
        "User-Agent": "okhttp/4.12.0",
        "Accept-Encoding": "gzip",
        "Connection": "Keep-Alive",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        res = await client.get(event_url, headers=headers)
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Event page HTTP {res.status_code}")

    html = res.text
    title = _extract_first(r"<title>(.*?)</title>", html)
    league = _extract_first(r"/en/live/[^/]+/([^/]+)/\d+-", event_url).replace("-", " ")

    team_names = re.findall(
        r'class="scoreboard-team-name__text"[^>]*>(.*?)</span>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    clean_teams = [_strip_html_tags(t).strip() for t in team_names if _strip_html_tags(t).strip()]
    home = clean_teams[0] if len(clean_teams) > 0 else ""
    away = clean_teams[1] if len(clean_teams) > 1 else ""

    score_values = re.findall(
        r'class="[^"]*scoreboard-scores__score[^"]*"[^>]*>(.*?)</span>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    clean_scores = [_strip_html_tags(s).strip() for s in score_values if _strip_html_tags(s).strip()]
    home_score = clean_scores[0] if len(clean_scores) > 0 else ""
    away_score = clean_scores[1] if len(clean_scores) > 1 else ""

    # Cricket-specific highlights often appear in tables.
    wickets = re.findall(
        r'cricket-overs-statistic__text"[^>]*>(.*?)</span>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    wickets_clean = []
    for w in wickets:
        text = _strip_html_tags(w).strip()
        if text and text not in wickets_clean:
            wickets_clean.append(text)
    wickets_clean = wickets_clean[:8]

    status = "live" if ("event in progress" in html.lower() or "scoreboard-status" in html.lower()) else ""

    return {
        "title": title,
        "league": league,
        "home": home,
        "away": away,
        "home_score": home_score,
        "away_score": away_score,
        "status": status,
        "wickets": wickets_clean,
    }


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
    try:
        source_url = await _resolve_source_url()
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            res = await client.get(source_url, headers=headers)
        if res.status_code != 200:
            raise HTTPException(status_code=502, detail=f"1XBet source HTTP {res.status_code}")

        root = res.json()
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
    except Exception:
        # If feed/json endpoint is blocked, parse public live page directly.
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            live_res = await client.get(ONE_XBET_LIVE_URL, headers=headers)
        if live_res.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"1XBet live page HTTP {live_res.status_code}",
            )
        payload = _build_payload_from_dashboard_cards(live_res.text)
        if payload.events:
            return payload
        return _build_payload_from_live_html(live_res.text)


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
    if url and url.strip():
        try:
            parsed = await _parse_event_page_details(url.strip())
            home = parsed.get("home", "")
            away = parsed.get("away", "")
            home_score = parsed.get("home_score", "")
            away_score = parsed.get("away_score", "")
            status = parsed.get("status", "")
            wickets = parsed.get("wickets", [])
            return {
                "status": "success",
                "eventId": eventId,
                "url": url,
                "event": {
                    "id": eventId,
                    "title": parsed.get("title", ""),
                    "eventName": parsed.get("title", ""),
                    "league": parsed.get("league", ""),
                    "teamAName": home,
                    "teamBName": away,
                    "teamAScore": home_score,
                    "teamBScore": away_score,
                    "match_status": status,
                },
                "lineups": [
                    {"team": home or "Home", "formation": "N/A"},
                    {"team": away or "Away", "formation": "N/A"},
                ],
                "stats": [
                    {"name": "Home score", "value": home_score or "-"},
                    {"name": "Away score", "value": away_score or "-"},
                    {"name": "Status", "value": status or "-"},
                ],
                "incidents": [{"title": "Wicket", "value": w} for w in wickets],
            }
        except Exception:
            # Fall through to cache/event-based fallback path below.
            pass

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
