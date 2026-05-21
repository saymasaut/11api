from __future__ import annotations

import json
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.core.pool import fetch_html as pool_fetch_html

BASE_SITE = "https://cosxplay.com/"
SITE_HOST = "cosxplay.com"

_VIDEO_PAGE_RE = re.compile(
    r"^https://(?:www\.)?cosxplay\.com/(?P<id>\d+)-[^/]+/?$",
    re.IGNORECASE,
)

_EXCLUDED_PATH_PARTS = frozenset(
    {
        "categories",
        "tag",
        "page",
        "embed",
        "video-actors",
        "video-tags",
        "contact",
        "privacy-policy",
        "dmca",
        "terms-of-use",
        "nosotros",
        "core",
        "storage",
        "girls",
        "dafeluv",
        "qejoles",
    }
)

_NOSO_MP4_RE = re.compile(
    r"https?://xcdn\d*\.nosofiles\.com/[^\s\"'<>]+?_(?:high|low|\d{3,4}p)\.mp4(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)


def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == SITE_HOST or h.endswith(f".{SITE_HOST}")


def get_categories() -> list[dict]:
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, "categories.json")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


async def fetch_page(url: str, *, referer: str | None = None) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer or BASE_SITE,
    }
    return await pool_fetch_html(url, headers=headers)


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _meta(soup: BeautifulSoup, *, prop: str | None = None, name: str | None = None) -> Optional[str]:
    if prop:
        tag = soup.find("meta", attrs={"property": prop})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    if name:
        tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return str(tag.get("content")).strip()
    return None


def _clean_title(title: str | None) -> Optional[str]:
    if not title:
        return None
    t = str(title).strip()
    for suffix in (
        " | CosXplay.com",
        " - CosXplay.com",
        " | CosXplay",
        " - CosXplay",
    ):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t or None


def _normalize_numberish(value: str | None) -> Optional[str]:
    if not value:
        return None
    txt = str(value).strip().replace(",", "").replace("\u00a0", " ")
    txt = re.sub(r"\s+", "", txt)
    txt = re.sub(r"[^0-9KMBkmb\.]", "", txt)
    return txt.upper() or None


def _extract_duration(text: str | None) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"\b(?:\d{1,2}:){1,2}\d{2}\b", text)
    if m:
        return m.group(0)
    iso = re.match(r"^P(?:\d+Y)?(?:\d+M)?(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", text.strip(), re.I)
    if iso:
        h = int(iso.group(1) or 0)
        m_val = int(iso.group(2) or 0)
        s = int(iso.group(3) or 0)
        if h > 0:
            return f"{h}:{m_val:02d}:{s:02d}"
        return f"{m_val}:{s:02d}"
    return None


def _extract_views(text: str | None) -> Optional[str]:
    if not text:
        return None
    if str(text).isdigit():
        return _normalize_numberish(text)
    m = re.search(r"\bviews?\s*[:\-]?\s*(\d[\d\s,\.]*\s*[KMBkmb]?)\b", text, flags=re.IGNORECASE)
    if not m:
        m = re.search(r"\b(\d[\d,\.]*\s*[KMBkmb])\b", text, flags=re.IGNORECASE)
    if not m:
        return None
    return _normalize_numberish(m.group(1))


def _is_placeholder_image_url(url: str) -> bool:
    low = (url or "").strip().lower()
    if not low or low.startswith("data:"):
        return True
    return any(x in low for x in ("placeholder", "/girls/flags/", "1x1.", "blank."))


def _normalize_image_url(url: str) -> Optional[str]:
    u = (url or "").strip()
    if not u or _is_placeholder_image_url(u):
        return None
    if u.startswith("//"):
        return f"https:{u}"
    if u.startswith("/"):
        return urljoin(BASE_SITE, u)
    if u.startswith("http"):
        return u
    return None


def _best_image_url(img: Any) -> Optional[str]:
    if img is None:
        return None
    classes = " ".join(img.get("class") or []).lower()
    # Kolortube cards: lazy `data-src` until JS adds `loaded` and copies to `src`.
    prefer_src_first = "loaded" in classes or "video-img" in classes
    keys = (
        ("src", "data-src", "data-original", "data-lazy-src", "srcset")
        if prefer_src_first
        else ("data-src", "data-original", "data-lazy-src", "srcset", "src")
    )
    for key in keys:
        v = img.get(key)
        if not v:
            continue
        url = str(v).strip()
        if not url:
            continue
        if key == "srcset" and " " in url:
            url = url.split(" ", 1)[0].strip()
        normalized = _normalize_image_url(url)
        if normalized:
            return normalized
    return None


def _thumb_from_video_block(block: Any) -> Optional[str]:
    """Prefer main poster `img.video-img` inside `.thumb`, not tag flag icons."""
    for selector in (
        "a.thumb img.video-img",
        "img.video-img.img-fluid",
        "img.video-img",
    ):
        img = block.select_one(selector)
        if img:
            thumb = _best_image_url(img)
            if thumb:
                return thumb
    return None


def _normalize_video_href(href: str) -> Optional[str]:
    href = (href or "").strip()
    if not href:
        return None
    if href.startswith("//"):
        href = f"https:{href}"
    elif href.startswith("/"):
        href = f"https://{SITE_HOST}{href}"
    if not href.startswith("http"):
        return None
    href = href.split("#", 1)[0].split("?", 1)[0]
    if SITE_HOST not in urlparse(href).netloc.lower():
        return None

    parts = [p for p in urlparse(href).path.split("/") if p]
    if len(parts) != 1:
        return None
    if parts[0].lower() in _EXCLUDED_PATH_PARTS:
        return None
    if not re.match(r"^\d+-", parts[0]):
        return None

    m = _VIDEO_PAGE_RE.match(href if href.endswith("/") else href + "/")
    if not m:
        return None
    return urlunparse(("https", SITE_HOST, f"/{parts[0]}/", "", "", ""))


def _is_preview_asset(url: str) -> bool:
    low = (url or "").lower()
    return any(x in low for x in ("trailer.mp4", "/preview", "_preview", "_poster", ".jpg", ".png", ".webp"))


def _quality_from_mp4_url(url: str) -> str:
    low = (url or "").lower()
    if "_high." in low or "_high?" in low:
        return "high"
    if "_low." in low or "_low?" in low:
        return "low"
    qm = re.search(r"_(\d{3,4})p\.mp4", low)
    if qm:
        return f"{qm.group(1)}p"
    return "source"


def _parse_json_ld_graph(soup: BeautifulSoup) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=False)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        if isinstance(parsed, dict):
            graph = parsed.get("@graph")
            if isinstance(graph, list):
                out.extend([x for x in graph if isinstance(x, dict)])
            else:
                out.append(parsed)
        elif isinstance(parsed, list):
            out.extend([x for x in parsed if isinstance(x, dict)])
    return out


def _extract_streams(soup: BeautifulSoup, html: str, page_url: str) -> dict[str, Any]:
    streams: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    seen_keys: set[str] = set()
    unescaped = html.replace("\\/", "/").replace("\\u0026", "&")

    def add_stream(url: str, *, quality: str, fmt: str) -> None:
        u = (url or "").strip()
        if not u or u in seen_urls:
            return
        if _is_preview_asset(u):
            return
        if fmt == "mp4" and "nosofiles.com" not in u.lower():
            return
        dedupe_key = f"{fmt}:{quality}:{urlparse(u).netloc.lower()}{urlparse(u).path.lower()}"
        if dedupe_key in seen_keys:
            return
        seen_urls.add(u)
        seen_keys.add(dedupe_key)
        streams.append({"url": u, "quality": quality, "format": fmt})

    for obj in _parse_json_ld_graph(soup):
        t = obj.get("@type")
        is_video = t == "VideoObject" or (isinstance(t, list) and "VideoObject" in t)
        if not is_video:
            continue
        content = obj.get("contentUrl")
        if isinstance(content, str):
            add_stream(content, quality=_quality_from_mp4_url(content), fmt="mp4")
        embed = obj.get("embedUrl")
        if isinstance(embed, str) and embed.startswith("http"):
            add_stream(embed, quality="cosxplay", fmt="embed")

    for node in soup.select("video source[src], video source[data-src], video[src]"):
        src = _first_non_empty(node.get("src"), node.get("data-src"))
        if not src:
            continue
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = urljoin(page_url, src)
        if src.startswith("http"):
            add_stream(src, quality=_quality_from_mp4_url(src), fmt="mp4")

    for m in re.finditer(r'var\s+videoHigh\s*=\s*"([^"]+)"', unescaped):
        add_stream(m.group(1), quality="high", fmt="mp4")
    for m in re.finditer(r'videoLow\s*=\s*"([^"]+)"', unescaped):
        add_stream(m.group(1), quality="low", fmt="mp4")
    for m in _NOSO_MP4_RE.finditer(unescaped):
        add_stream(m.group(0), quality=_quality_from_mp4_url(m.group(0)), fmt="mp4")

    def _score(item: dict[str, str]) -> tuple[int, int]:
        fmt = (item.get("format") or "").lower()
        q = (item.get("quality") or "").lower()
        if fmt == "embed":
            return (0, 0)
        if q == "high":
            return (3, 1080)
        if q == "low":
            return (2, 480)
        digits = "".join(ch for ch in q if ch.isdigit())
        return (2, int(digits) if digits else 0)

    streams.sort(key=_score, reverse=True)
    mp4s = [s for s in streams if s.get("format") == "mp4"]
    embed = next((s for s in streams if s.get("format") == "embed"), None)
    default_url = mp4s[0]["url"] if mp4s else (embed["url"] if embed else None)
    return {
        "streams": streams,
        "hls": None,
        "default": default_url,
        "has_video": bool(streams),
    }


def parse_video_page(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    title = _clean_title(
        _first_non_empty(
            _meta(soup, prop="og:title"),
            _meta(soup, name="twitter:title"),
            soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None,
            soup.title.get_text(strip=True) if soup.title else None,
        )
    ) or "Unknown Video"

    description = _first_non_empty(
        _meta(soup, prop="og:description"),
        _meta(soup, name="description"),
        _meta(soup, name="twitter:description"),
    )
    thumbnail = _first_non_empty(_meta(soup, prop="og:image"), _meta(soup, name="twitter:image"))
    if thumbnail and thumbnail.startswith("//"):
        thumbnail = f"https:{thumbnail}"

    duration = None
    views = None
    preview_url = None
    tags: list[str] = []

    for obj in _parse_json_ld_graph(soup):
        t = obj.get("@type")
        is_video = t == "VideoObject" or (isinstance(t, list) and "VideoObject" in t)
        if is_video:
            title = _clean_title(_first_non_empty(title, obj.get("name"))) or title
            description = _first_non_empty(description, obj.get("description"))
            thumb = obj.get("thumbnailUrl")
            if isinstance(thumb, list) and thumb:
                thumb = thumb[0]
            thumbnail = _first_non_empty(thumbnail, thumb if isinstance(thumb, str) else None)
            duration = _extract_duration(_first_non_empty(duration, obj.get("duration")))
            stats = obj.get("interactionStatistic") or []
            if isinstance(stats, list):
                for stat in stats:
                    if not isinstance(stat, dict):
                        continue
                    itype = stat.get("interactionType") or {}
                    if isinstance(itype, dict) and itype.get("@type") == "WatchAction":
                        views = _normalize_numberish(str(stat.get("userInteractionCount", "")))
        about = obj.get("about")
        if isinstance(about, list):
            for term in about:
                if isinstance(term, dict) and term.get("name"):
                    tags.append(str(term["name"]).strip())

    store_blob_m = re.search(r"const\s+toStore\s*=\s*(\{[^;]+\});", html)
    if store_blob_m:
        blob = store_blob_m.group(1)
        for key, out in (
            ("title", "title"),
            ("thumbnail", "thumbnail"),
            ("preview", "preview"),
            ("length", "duration"),
        ):
            m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', blob)
            if m:
                val = m.group(1)
                if out == "title":
                    title = _clean_title(_first_non_empty(title, val)) or title
                elif out == "thumbnail":
                    thumbnail = _first_non_empty(thumbnail, val)
                elif out == "preview":
                    preview_url = _first_non_empty(preview_url, val)
                elif out == "duration":
                    duration = _extract_duration(_first_non_empty(duration, val))
        vm = re.search(r'"views"\s*:\s*(\d+)', blob)
        if vm and not views:
            views = _normalize_numberish(vm.group(1))

    if not duration:
        duration = _extract_duration(soup.get_text(" ", strip=True))
    if not views:
        views_el = soup.select_one(".views-number")
        views = _extract_views(views_el.get_text(" ", strip=True) if views_el else None)
        if not views:
            views = _extract_views(soup.get_text(" ", strip=True))

    if not tags:
        kw = _meta(soup, name="keywords")
        if kw:
            tags.extend([x.strip() for x in kw.split(",") if x.strip()])
    tags = list(dict.fromkeys(tags))

    return {
        "url": url,
        "title": title,
        "description": description,
        "thumbnail_url": thumbnail,
        "duration": duration,
        "views": views,
        "uploader_name": None,
        "category": None,
        "tags": tags,
        "video": _extract_streams(soup, html, url),
        "related_videos": [],
        "preview_url": preview_url,
    }


async def scrape(url: str) -> dict[str, Any]:
    page_url = str(url).strip()
    if page_url and not page_url.endswith("/"):
        page_url = page_url + "/"
    html = await fetch_page(page_url, referer=BASE_SITE)
    return parse_video_page(html, page_url)


def _build_list_page_url(base_url: str, page: int) -> str:
    raw = (base_url or "").strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    p = urlparse(raw)
    scheme = p.scheme or "https"
    netloc = p.netloc or SITE_HOST
    path = p.path or "/"
    if not path.endswith("/"):
        path += "/"
    path = re.sub(r"/page/\d+/?$", "/", path)
    query = dict(parse_qsl(p.query, keep_blank_values=True))

    if page <= 1:
        return urlunparse((scheme, netloc, path, "", urlencode(query), ""))

    if path == "/":
        new_path = f"/page/{page}/"
    else:
        new_path = path.rstrip("/") + f"/page/{page}/"
    return urlunparse((scheme, netloc, new_path, "", urlencode(query), ""))


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
    page_url = _build_list_page_url(base_url, page)
    try:
        html = await fetch_page(page_url, referer=base_url or BASE_SITE)
    except Exception:
        return []

    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for block in soup.select("div.video-block[data-post-id]"):
        if len(items) >= limit:
            break
        a = block.select_one("a.infos[href], a.thumb[href]")
        if not a:
            continue
        href = _normalize_video_href(a.get("href") or "")
        if not href or href in seen:
            continue

        title_el = block.select_one(".title")
        poster_img = block.select_one("a.thumb img.video-img, img.video-img")
        title = _clean_title(
            _first_non_empty(
                title_el.get_text(" ", strip=True) if title_el else None,
                a.get("aria-label"),
                poster_img.get("alt") if poster_img else None,
            )
        ) or "Unknown Video"
        thumb = _thumb_from_video_block(block)

        ctext = block.get_text(" ", strip=True)
        duration_el = block.select_one(".duration")
        duration = _extract_duration(duration_el.get_text(" ", strip=True) if duration_el else None)
        if not duration:
            duration = _extract_duration(ctext)

        views_el = block.select_one(".views-number")
        views = _extract_views(views_el.get_text(" ", strip=True) if views_el else None)
        if not views:
            views = _extract_views(ctext)

        seen.add(href)
        items.append(
            {
                "url": href,
                "title": title,
                "thumbnail_url": thumb,
                "duration": duration,
                "views": views,
                "uploader_name": None,
            }
        )

    return items[:limit]
