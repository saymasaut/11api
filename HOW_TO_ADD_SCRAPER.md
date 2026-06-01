# How to Add a New Scraper

This guide matches the current backend layout and registration flow.

## Current Structure

```text
backend/
├── main.py
└── app/
    ├── main.py
    └── scrapers/
        ├── __init__.py
        ├── xnxx/
        │   ├── __init__.py
        │   ├── scraper.py
        │   └── categories.json
        └── <site_name>/
            ├── __init__.py
            ├── scraper.py
            └── categories.json
```

## Required Interface

Each scraper module must expose these functions:

- `can_handle(host: str) -> bool`
- `scrape(url: str) -> dict`
- `list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict]`
- `get_categories() -> list[dict]` (or async if the scraper requires it)

Optional:

- `crawl_videos(...)` only if you want `/api/v1/crawls` support

## Step-by-Step

### 1) Create the new scraper folder

Create `backend/app/scrapers/<site_name>/` with:

- `scraper.py`
- `__init__.py`
- `categories.json`

Fastest start:

```bash
cp -r backend/app/scrapers/xnxx backend/app/scrapers/<site_name>
```

Then rename/update internals.

### 2) Implement exports in `__init__.py`

Example:

```python
from .scraper import can_handle, scrape, list_videos, get_categories

__all__ = ["can_handle", "scrape", "list_videos", "get_categories"]
```

If your scraper has `crawl_videos`, include it in imports/`__all__`.

### 3) Register scraper package

Edit `backend/app/scrapers/__init__.py`:

1. Add `from . import <site_name>`
2. Add `"<site_name>"` to `__all__`

If you skip this, importing from `app.scrapers` in `app/main.py` will fail.

### 4) Register in `backend/app/main.py`

Update all required dispatcher/router spots:

1. **Top-level import from `app.scrapers`**
   - Add `<site_name>` to the import list.
2. **`_scrape_dispatch(...)`**
   - Add branch for `can_handle()` -> `scrape()`.
3. **`_list_dispatch(...)`**
   - Add branch for `can_handle()` -> `list_videos()`.
4. **`get_categories(source: str)` endpoint**
   - Add source alias mapping -> `<site_name>.get_categories()`.
5. **`_crawl_dispatch(...)` (optional)**
   - Add only if your scraper implements crawling.

## Minimal `scraper.py` Template

```python
from __future__ import annotations

import httpx
from bs4 import BeautifulSoup


def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return "example.com" in h or "www.example.com" in h


async def scrape(url: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        res = await client.get(url)
        res.raise_for_status()
    soup = BeautifulSoup(res.text, "lxml")

    title = soup.title.get_text(strip=True) if soup.title else ""
    return {
        "url": url,
        "title": title,
        "thumbnail_url": None,
        "duration": None,
        "views": None,
        "uploader_name": None,
        "video": {
            "streams": [],
            "hls": None,
            "default": None,
            "has_video": False,
        },
    }


async def list_videos(base_url: str, page: int = 1, limit: int = 100) -> list[dict]:
    return []


def get_categories() -> list[dict]:
    return []
```

## Categories File

`categories.json` should be a list of category objects your scraper understands. Keep the shape consistent with existing scraper folders so `/api/v1/categories` returns valid `CategoryItem` entries.

## Verification Checklist

Before shipping:

- New folder exists in `backend/app/scrapers/<site_name>/`
- `backend/app/scrapers/__init__.py` includes `<site_name>`
- `backend/app/main.py` updated in:
  - scraper imports
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping
  - optional `_crawl_dispatch`
- `can_handle()` matches real hostnames
- `scrape()` and `list_videos()` return dict keys expected by API schemas

Quick manual tests (replace URL and source):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://example.com/video/123\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://example.com/videos&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=<site_name>"
```

If all three endpoints return valid data, your scraper integration is complete.

## TNAFlix Implementation Notes

Use this as a concrete example for `tnaflix.com` support.

### Host aliases

- `tnaflix.com`
- `www.tnaflix.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "tnaflix.com" or h.endswith(".tnaflix.com")
```

### Metadata extraction fallback order

For `scrape(url)` on TNAFlix, this order is resilient:

1. `og:title` / `og:description` / `og:image`
2. `twitter:title` / `twitter:image`
3. JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`, `keywords`)
4. Visible text fallback (duration/views regex)

This keeps the response stable even when one source disappears.

### Stream extraction approach

TNAFlix video URLs are typically exposed in inline script blocks. For a first pass:

- Scan page HTML for `.m3u8` and `.mp4` URLs
- Unescape script-escaped URLs (`\\/` -> `/`, `\\u0026` -> `&`)
- Build `video.streams` with:
  - `quality`
  - `url`
  - `format` (`hls` or `mp4`)
- Set `video.default` to the best candidate after sorting by quality

Keep the response shape compatible with existing `ScrapeResponse` expectations.

### Listing and pagination patterns

For `list_videos(base_url, page, limit)`:

- Parse video cards by filtering links that contain `/video`
- Pull title from `a[title]`, image `alt`, or visible text
- Pull thumbnail from `data-src` / `data-original` / `src`
- Extract duration/views/uploader from nearest card container text/selectors
- Start with query pagination (`?page={page}`) for page > 1

### Registration checklist for TNAFlix

Besides creating `backend/app/scrapers/tnaflix/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=tnaflix`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text (optional)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` for TNAFlix

### TNAFlix verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.tnaflix.com/video/123456/demo\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.tnaflix.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=tnaflix"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.tnaflix.com/video/123456/demo"
```

## HornySimp Implementation Notes

HornySimp (`hornysimp.com`) is a WordPress/Elementor-style listing site where video pages typically embed third-party players via `<iframe>`, rather than exposing direct `.mp4`/`.m3u8` URLs on the main page HTML.

### Host aliases

- `hornysimp.com`
- `www.hornysimp.com` (if it ever appears)

### Pagination pattern

Section pages and the home page paginate using a query param:

- `?_page=2`
- `?_page=3`

So `list_videos(base_url, page)` should generally build `base_url + "?_page={page}"` (or `&` if `base_url` already has a query).

### Stream extraction approach (same idea as `xxxparodyhd`)

For `scrape(url)`:

- Extract metadata from `og:title`, `og:description`, `og:image`, plus `h1` fallback.
- Collect player embed URLs from `iframe[src]` (skip ad iframes). The site uses two tabs (`Server 1` / `Server 2`); expose each iframe as its own stream with `format="embed"` and `quality` set to `"Server 1"`, `"Server 2"`, … matching the UI.
- Set `video.default` to the **Byse / byseraguci.com** embed (“Server 2”) when present, else **hrnyvid / LuluStream**, else the first embed.
- `GET /api/v1/videos/stream` for `hornysimp.com` includes **flat per-source fields** (`Server 1`, `Server 2`, …) in the JSON response, same pattern as `xxxparodyhd.net` (see `get_stream_url` in `video_streaming.py`).

### Registration checklist for HornySimp

Besides creating `backend/app/scrapers/hornysimp/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=hornysimp`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

### HornySimp verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://hornysimp.com/<post-slug>/\"}"

curl \"http://127.0.0.1:8000/api/v1/videos?base_url=https://hornysimp.com/leaked-clips/&page=1&limit=20\"

curl \"http://127.0.0.1:8000/api/v1/categories?source=hornysimp\"

curl \"http://127.0.0.1:8000/api/v1/videos/info?url=https://hornysimp.com/<post-slug>/\"
```

## PimpBunny Implementation Notes

[PimpBunny](https://pimpbunny.com/) is a Vicetemple-style tube: public video pages live under `/videos/{slug}/`, categories under `/categories/{slug}/`, and sitewide search under `/search/{query}/`.

### Host aliases

- `pimpbunny.com`
- `www.pimpbunny.com` (and other subdomains if they mirror the same paths)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "pimpbunny.com" or h.endswith(".pimpbunny.com")
```

### Metadata and streams (`scrape`)

- Prefer `og:title`, `og:description`, `og:image`, plus `<meta name="keywords">` for tags.
- **Progressive MP4** URLs appear in the HTML as same-origin `https://pimpbunny.com/get_file/.../*.mp4` (often several resolutions, e.g. `_360p`, `_720p`, `_1080p`, plus a basename `/{id}.mp4` “source” variant).
- A **HEAD** request to each `get_file` URL (with `Referer: https://pimpbunny.com/`) usually returns **302** to the real playable URL on a CDN host: `https://st*.pimpbunny.com/remote_control.php?time=...&file=%2Fvideos%2F...&cv=...` (tokens are short-lived). If **HEAD** does not redirect, try **GET** with `Range: bytes=0-0` the same way. Tiers that still do not redirect (often premium-only) are **dropped** from `video.streams` so the API does not expose non-playable bare `get_file` links.
- Parse with regex after unescaping `\\/` → `/` and `\\u0026` → `&`. Build `video.streams` with `format="mp4"` and `quality` from the filename (`_720p`, `_pb_1080p`, etc.). The HTML often lists **the same quality more than once** with different signing hashes; **keep the last match per quality** (the player config block is usually later and is the one that returns 302).
- Resolve each `get_file` like the browser: **Referer** = the **full video page URL**, `GET` with `Range: bytes=0-` (and `HEAD` / `Range: 0-0` as fallbacks), URL form `...mp4/?rnd=<unix_ms>` (see network tab).
- The page also references `https://pimpbunny.com/embed/{numericId}`; you can expose that as `format="embed"` / `quality="embed"` as a fallback for clients that only handle embeds.
- Set `video.default` to the best MP4 by resolution, not the embed.

### Listing and pagination (`list_videos`)

- Video cards link to `https://pimpbunny.com/videos/{slug}/`. Skip `upload-video` and the bare `/videos/` index.
- **Videos index:** page 1 is `https://pimpbunny.com/videos/`, page *n* &gt; 1 is `https://pimpbunny.com/videos/{n}/` (not `?page=`).
- **Categories:** page 1 is `https://pimpbunny.com/categories/{slug}/`, page *n* &gt; 1 is `https://pimpbunny.com/categories/{slug}/{n}/`.
- **Search:** base URL `https://pimpbunny.com/search/{term}/`; for page *n* &gt; 1 add `?page=n` (combine with any existing query params).
- Treat bare `https://pimpbunny.com/` as the videos index when building the first page URL.

### Registration checklist for PimpBunny

Besides creating `backend/app/scrapers/pimpbunny/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=pimpbunny`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - flat `available_qualities` block (same pattern as `tnaflix.com`)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`baseUrl` should be list-friendly, e.g. `https://pimpbunny.com/videos/`)

### PimpBunny verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://pimpbunny.com/videos/gracewearslace-receives-a-cumshot-after-sex/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://pimpbunny.com/videos/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=pimpbunny"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://pimpbunny.com/videos/gracewearslace-receives-a-cumshot-after-sex/"
```

## Hentaiser Implementation Notes

[Hentaiser](https://app.hentaiser.app/) exposes a JSON API and media on a CDN host. For this source, scraper logic can be mostly API-first rather than HTML parsing.

### Host aliases

- `app.hentaiser.app` (site/API)
- `api.hentaiser.app` (video feed API)
- `media2.hentaiser.com` (thumbnail/video CDN)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return (
        h == "app.hentaiser.app"
        or h.endswith(".hentaiser.app")
        or h == "media2.hentaiser.com"
        or h.endswith(".hentaiser.com")
    )
```

### API-first listing (`list_videos`)

Use the API endpoint as primary source:

- `https://api.hentaiser.app/v1/videos?sort=comments&limit=4&top=1`

Recommended approach:

- Build requests against `https://api.hentaiser.app/v1/videos`.
- Keep support for query params such as `sort`, `limit`, and `top`.
- When `page` is requested by backend API, map it to whatever pagination Hentaiser returns (offset/page/cursor) and gracefully fallback to first page if absent.
- Normalize response items to existing list schema (`url`, `title`, `thumbnail_url`, `duration`, `views`, `uploader_name`).

### Media URL and ID extraction (`scrape`)

Given sample URLs:

- Thumbnail URL:
  - `https://media2.hentaiser.com//videos/b/bb/bbd/bbd971bf7492a7ffc9d7e6a35d64dd73.jpg`
- Video URL:
  - `https://media2.hentaiser.com//videos/b/bb/bbd/bbd971bf7492a7ffc9d7e6a35d64dd73.mp4`

Treat the CDN path as stable ID:

- **thumbnail_id**: `/videos/b/bb/bbd/bbd971bf7492a7ffc9d7e6a35d64dd73.jpg`
- **video_id**: `/videos/b/bb/bbd/bbd971bf7492a7ffc9d7e6a35d64dd73.mp4`
- **media host**: `https://media2.hentaiser.com`

Implementation tips:

- Preserve nested path segments under `/videos/...` instead of reducing to only basename.
- Store full URLs in `thumbnail_url` and stream URLs.
- Add one MP4 stream entry (`format="mp4"`, `quality="source"` unless the API provides richer qualities).
- Set `video.default` to that MP4 URL and `video.has_video=True`.

### Registration checklist for Hentaiser

Besides creating `backend/app/scrapers/hentaiser/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=hentaiser`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - quality map (`source` or API-provided tiers)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

### Hentaiser verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://app.hentaiser.app/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://api.hentaiser.app/v1/videos?sort=comments&top=1&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=hentaiser"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://media2.hentaiser.com//videos/b/bb/bbd/bbd971bf7492a7ffc9d7e6a35d64dd73.mp4"
```

## BollywoodMaal Implementation Notes

[BollywoodMaal](https://bollywoodmaal.com/) is a WordPress-style tube site with homepage/category card grids, pagination links, and post pages that usually expose playable sources in HTML or inline script/player config blocks.

### Host aliases

- `bollywoodmaal.com`
- `www.bollywoodmaal.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "bollywoodmaal.com" or h.endswith(".bollywoodmaal.com")
```

### Listing and pagination (`list_videos`)

Use a resilient card parser so theme/layout changes do not break quickly:

- Parse item links from anchors that look like video-post targets (title cards / thumbnails).
- Keep only unique links under the same domain and skip utility URLs (`/contact`, auth/profile paths, policy pages).
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration: parse card text using `mm:ss` / `hh:mm:ss` regex
  - views: parse numeric counters (`129`, `1K`, `34K`) from nearby text
- Page 1 should use `base_url` unchanged.
- For page > 1, follow the site pager links first (`/page/{n}/`, `?paged={n}`, or explicit numbered pager URLs). If no pager exists, fallback to appending `?paged={page}`.

### Metadata and streams (`scrape`)

For detail pages:

- Extract metadata from:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`)
  4. visible title/header fallback
- For playable sources, scan:
  - `<video>` tags (`source[src]`, `video[src]`)
  - `<iframe src>` embeds (external host streams)
  - inline scripts for direct `.mp4` / `.m3u8` URLs
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` entries:
  - direct files: `format="mp4"` / `format="hls"`
  - embedded players: `format="embed"` and `quality` like `Server 1`, `Server 2`
- Set `video.default` with this preference:
  1. highest quality direct MP4
  2. HLS URL
  3. first embed URL

### Registration checklist for BollywoodMaal

Besides creating `backend/app/scrapers/bollywoodmaal/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=bollywoodmaal`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

### BollywoodMaal verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://bollywoodmaal.com/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://bollywoodmaal.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=bollywoodmaal"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://bollywoodmaal.com/<video-post-slug>/"
```

## Viralkand Implementation Notes

[Viralkand](https://viralkand.com/) looks like a WordPress-style clip index with:

- homepage/category grids of card links
- numbered pagination
- search support
- post/detail pages that should be treated as the canonical video URLs

Use the existing `bollywoodmaal`, `hornysimp`, and `masa49` scrapers as the closest starting references.

### Host aliases

- `viralkand.com`
- `www.viralkand.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "viralkand.com" or h.endswith(".viralkand.com")
```

### Listing and pagination (`list_videos`)

The public index exposes a paginated card grid plus category and search pages. Recommended approach:

- Parse candidate video links from thumbnail/title anchors inside the main listing grid.
- Keep only same-domain URLs and skip obvious utility pages such as:
  - `/dmca-remove-a-video`
  - `/18-u-s-c-2257`
  - `/terms-of-use`
  - tag/category index roots without a concrete video item
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible text
  - thumbnail: `data-src`, `data-lazy-src`, `data-original`, `srcset`, then `src`
  - duration: regex for `mm:ss` / `hh:mm:ss`
  - views/rating: parse nearby card text only if easy; keep them optional
- Page 1 should use `base_url` unchanged.
- For page > 1, first follow the site pager format if visible (`/page/{n}/` is the most likely WordPress pattern). If the supplied `base_url` already includes a category/tag path, preserve it and append the page segment there.
- For search URLs, prefer WordPress query search (`https://viralkand.com/?s={query}`) unless live inspection shows a different route.

### Metadata and streams (`scrape`)

For detail pages:

- Extract metadata from:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject` if present
  4. visible `h1` / `<title>` fallback
- Scan for playable sources in:
  - `<video src>` / `<video><source src>`
  - `iframe[src]` embeds
  - inline scripts that expose `.mp4` or `.m3u8`
- Unescape inline-script URLs before using them (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` using:
  - direct files: `format="mp4"` or `format="hls"`
  - embeds: `format="embed"` and qualities like `Server 1`, `Server 2`
- Set `video.default` with this preference:
  1. highest-quality direct MP4
  2. HLS URL
  3. first playable embed

If the site only exposes third-party embeds on the post page, follow the same fallback pattern used by `hornysimp` / `xxxparodyhd`: return embed streams instead of forcing nonexistent direct media URLs.

### Categories (`get_categories`)

Start with a static `categories.json` copied from the public category list the scraper will support. Keep the schema aligned with the other scraper folders so `/api/v1/categories?source=viralkand` returns valid `CategoryItem` entries.

### Registration checklist for Viralkand

Besides creating `backend/app/scrapers/viralkand/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=viralkand`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
  - host checks for stream/info passthrough if needed
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request validation is still backed by explicit domain allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Viralkand verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://viralkand.com/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://viralkand.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=viralkand"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://viralkand.com/<video-post-slug>/"
```

## UncutMaza Implementation Notes

[UncutMaza](https://uncutmazaa.com/) is a WordPress-style clip index focused on episodic posts. The homepage exposes recent post cards with title links, relative publish-time labels, and duration-like badges (`mm:ss`) directly in listing text.

**Note:** `uncutmaza.com` redirects toward `uncutmaza.cc`, which often returns Cloudflare **403** to automated clients. The scraper rewrites `uncutmaza.com` / `uncutmaza.cc` requests to **`uncutmazaa.com`** (live HTML) before fetching.

Use `viralkand`, `mmsbro`, and `bollywoodmaal` as close implementation references.

### Host aliases

- `uncutmazaa.com` (canonical fetch host)
- `uncutmaza.com` / `uncutmaza.cc` (accepted; rewritten for HTTP fetch)

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h in ("uncutmazaa.com", "uncutmaza.com", "uncutmaza.cc")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate detail links from post-card anchors in the primary content grid.
- Keep only same-domain post URLs and skip utility/legal paths when present (`/contact`, `/privacy-policy`, `/dmca`, `/18-u-s-c-2257`, tag/category roots without concrete post slugs).
- Prefer metadata in this order:
  - title: card heading anchor text, then anchor `title`, then image `alt`
  - thumbnail: `data-src`, `data-lazy-src`, `data-original`, first `srcset` entry, then `src`
  - duration: parse `mm:ss` or `hh:mm:ss` from card text (many homepage entries expose `20:00`-style values)
  - views/uploader: optional (`None` if unavailable in card markup)
- Page 1 should use `base_url` unchanged.
- For page > 1, follow visible pager links first (WordPress commonly uses `/page/{n}/`). If no pager can be inferred, fallback to `?paged={n}` while preserving existing query params.

Useful list base URLs:

- `https://uncutmazaa.com/`
- `https://uncutmazaa.com/category/<category-slug>/` (if category archives are used)
- `https://uncutmazaa.com/?s=<query>` (if search query route is used)

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before using them (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` and quality labels (`Server 1`, `Server 2`, ...)
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a detail page only exposes embedded players, return embed streams rather than manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from the site's visible category navigation/archive list. Keep schema aligned with existing scraper folders so `/api/v1/categories?source=uncutmaza` returns valid `CategoryItem` entries.

### Registration checklist for UncutMaza

Besides creating `backend/app/scrapers/uncutmaza/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=uncutmaza`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### UncutMaza verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://uncutmazaa.com/kya-khoob-lagti-ho-episode-6/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://uncutmazaa.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=uncutmaza"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://uncutmazaa.com/kya-khoob-lagti-ho-episode-6/"
```

## Blowjobs.pro Implementation Notes

## DesiPorn.one Implementation Notes

[DesiPorn.one](https://desiporn.one/) is a tube-style site with canonical detail pages under `/videos/{id}/{slug}/`. The home page exposes card listings and navigation for Latest, Top Rated, Most Viewed, Categories, and Search.

### Host aliases

- `desiporn.one`
- `www.desiporn.one`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "desiporn.one" or h.endswith(".desiporn.one")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse card anchors that match `/videos/{numeric_id}/{slug}/`.
- Keep only same-domain video URLs and skip utility pages such as `/terms`, `/2257`, and external DMCA links.
- Prefer metadata in this order:
  - title: anchor text, then `title`, then image `alt`
  - thumbnail: `data-src`, `data-original`, first `srcset` candidate, then `src`
  - duration: regex for `mm:ss` / `hh:mm:ss` from card text
  - views/rating: parse compact counters and percentages when easy; keep optional
- Page 1 should use `base_url` unchanged.
- For page > 1, follow visible paginator routes first; fallback to `?page={n}` if no route is detected.

Useful base URLs to support:

- `https://desiporn.one/`
- `https://desiporn.one/latest/` (or site's "Latest" route if different)
- `https://desiporn.one/top-rated/`
- `https://desiporn.one/most-viewed/`
- `https://desiporn.one/categories/<category-slug>/`
- `https://desiporn.one/search/<term>/` (or query search route used by live markup)

### Metadata and streams (`scrape`)

For detail pages:

- Extract metadata from:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject` if present (`name`, `description`, `thumbnailUrl`, `duration`)
  4. visible title fallback
- Scan for playable URLs in:
  - `<video src>` / `<video><source src>`
  - inline scripts exposing `.mp4` / `.m3u8`
  - `iframe[src]` embeds (fallback)
- Unescape script URLs before using them (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct files: `format="mp4"` or `format="hls"`
  - embeds: `format="embed"` with server labels (`Server 1`, `Server 2`, ...)
- Set `video.default` preference:
  1. highest-quality direct MP4
  2. HLS URL
  3. first playable embed URL

If the page only exposes embedded players, return embed streams instead of fabricating direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from the site's public Categories index and keep schema aligned with existing scraper folders so `/api/v1/categories?source=desiporn` returns valid `CategoryItem` entries.

### Registration checklist for DesiPorn.one

Besides creating `backend/app/scrapers/desiporn/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=desiporn`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
  - host checks for stream/info passthrough if needed
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If URL validation still uses strict allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### DesiPorn.one verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://desiporn.one/videos/22481/desi-sex-bahu-and-sasur-indian-porn-videos/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://desiporn.one/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=desiporn"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://desiporn.one/videos/22481/desi-sex-bahu-and-sasur-indian-porn-videos/"
```

[Blowjobs.pro](https://blowjobs.pro/) is a tube-style site with canonical video pages under `/videos/{id}/{slug}/`, sortable listing views (Newest/Hottest/Most Viewed/Top Rated), category pages, model pages, and search.

### Host aliases

- `blowjobs.pro`
- `www.blowjobs.pro`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "blowjobs.pro" or h.endswith(".blowjobs.pro")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse video cards/anchors that match `/videos/{numeric_id}/{slug}/`.
- Keep only same-domain URLs and skip utility/auth links (`/login`, `/signup`, `/terms`, `/dmca`, `/2257`).
- Prefer metadata in this order:
  - title: anchor text, then `title` attribute, then image `alt`
  - thumbnail: `data-src`, `data-original`, `srcset` first candidate, then `src`
  - duration: regex for `mm:ss` / `hh:mm:ss` from nearby card text
  - views/rating: parse compact counters (`304.8k`, `1.3m`) and percentages when easy; keep optional
- Page 1 should use `base_url` unchanged.
- For page > 1, follow whichever paginator route the page exposes first (numeric path segment or query param); fallback to `?page={n}`.

Useful base URLs to support:

- `https://blowjobs.pro/`
- `https://blowjobs.pro/videos/newest/`
- `https://blowjobs.pro/videos/hottest/`
- `https://blowjobs.pro/videos/most-viewed/`
- `https://blowjobs.pro/videos/top-rated/`
- `https://blowjobs.pro/categories/<category-slug>/`
- `https://blowjobs.pro/models/<model-slug>/`
- `https://blowjobs.pro/search/<term>/` (if search route is enabled in live markup)

### Metadata and streams (`scrape`)

For detail pages:

- Extract metadata from:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`)
  4. visible title fallback
- Scan for playable URLs in:
  - `<video src>` / `<video><source src>`
  - inline scripts for `.mp4` / `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct files: `format="mp4"` or `format="hls"`
  - embeds: `format="embed"` with `quality` labels (`Server 1`, `Server 2`, ...)
- Set `video.default` preference:
  1. highest-quality direct MP4
  2. HLS URL
  3. first embed URL

If detail pages expose only third-party embeds, return embed streams instead of fabricating direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from public category pages under `/categories/` and keep schema aligned with existing scraper folders so `/api/v1/categories?source=blowjobspro` returns valid `CategoryItem` entries.

### Registration checklist for Blowjobs.pro

Besides creating `backend/app/scrapers/blowjobspro/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=blowjobspro`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If URL validation still uses strict allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Blowjobs.pro verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://blowjobs.pro/videos/7209/18-year-old-teen-gives-deepthroat-pov-blowjob/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://blowjobs.pro/videos/newest/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=blowjobspro"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://blowjobs.pro/videos/7209/18-year-old-teen-gives-deepthroat-pov-blowjob/"
```

## BlackPorn24 Implementation Notes

[BlackPorn24](https://blackporn24.com/) follows the same family of tube layout as Blowjobs.pro: canonical detail URLs under `/videos/{id}/{slug}/`, category pages under `/categories/{slug}/`, and sortable list tabs (Newest/Hottest/Most Viewed/Top Rated) exposed from the home page.

### Fast implementation plan (same as Blowjobs.pro)

BlackPorn24 can be implemented as a near-clone of the `blowjobspro` scraper:

1. Copy `backend/app/scrapers/blowjobspro/` -> `backend/app/scrapers/blackporn24/`.
2. Rename host checks and defaults:
   - `blowjobs.pro` -> `blackporn24.com`
   - `sourceId/source` -> `blackporn24`
3. Keep the same core logic:
   - card parsing (`.title`, `.duration`, `.views`)
   - `get_file` -> signed CDN `remote_control.php` resolution
   - ad iframe filtering and native `/embed/{id}` preference
4. Replace `categories.json` with `blackporn24.com/categories` entries.
5. Register in dispatch, streaming service, explore source list, and schema allowlists.

Treat BlackPorn24 as the same scraper engine with site-specific configuration (host/base URLs/categories/favicons).

### Host aliases

- `blackporn24.com`
- `www.blackporn24.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "blackporn24.com" or h.endswith(".blackporn24.com")
```

### Listing and pagination (`list_videos`)

Recommended parser behavior:

- Accept only canonical video links matching `/videos/{numeric_id}/{slug}/`.
- Skip utility/auth/legal links (`/terms`, `/dmca`, `/2257`, login/signup pages).
- Prefer card fields by class selectors when available:
  - title from `.title`
  - duration from `.duration`
  - views/rating from `.views` and nearby text
- Keep fallback extraction in case selectors shift:
  - title: anchor text / `title` / image `alt`
  - duration: `mm:ss` / `hh:mm:ss` regex
  - views: compact counters (`919k`, `2.1m`)
- Page 1 should use `base_url` unchanged.
- For page > 1, follow the site pager format first (numeric page segment or query param); fallback to `?page={n}`.

Useful list base URLs:

- `https://blackporn24.com/`
- `https://blackporn24.com/categories/<category-slug>/`
- `https://blackporn24.com/models/<model-slug>/`
- `https://blackporn24.com/search/<term>/` (if the route is active in live markup)

### Metadata and streams (`scrape`)

For video detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible title/header fallback
- Stream extraction order:
  - direct download/player links (`.mp4`, `.m3u8`, `/get_file/...`)
  - `<video src>` / `<video><source src>`
  - inline scripts with escaped URLs
  - site-native embed URL fallback (`/embed/{id}`)
- If stream links use intermediate `/get_file/...` URLs, resolve redirects to signed CDN `remote_control.php` URLs before returning `video.default`/`video.streams`.
- Filter obvious ad-network iframes (promo/banners) and keep only playable/embed entries.
- Set default stream preference:
  1. resolved direct MP4
  2. HLS
  3. site-native embed

### Categories (`get_categories`)

Start `categories.json` from `https://blackporn24.com/categories/` entries, preserving scraper folder schema so `/api/v1/categories?source=blackporn24` returns valid `CategoryItem` objects.

### Registration checklist for BlackPorn24

Besides creating `backend/app/scrapers/blackporn24/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=blackporn24`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - per-quality flat fields behavior (same pattern as blowjobspro/tnaflix)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If your branch still validates URL domains using explicit allowlists, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### BlackPorn24 verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://blackporn24.com/videos/4551/lustful-stepmom-uses-her-stepson-s-big-cock-for-pleasure/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://blackporn24.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=blackporn24"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://blackporn24.com/videos/4551/lustful-stepmom-uses-her-stepson-s-big-cock-for-pleasure/"
```

## IndianPorn365 Implementation Notes

[Indian Porn 365](https://indianporn365.xyz/) is a WordPress-style clip index with:

- category routes from the top nav (for example: `bhabhi`, `leaked-amateur-porn`, `desi-sex-videos`, `tamil-porn`)
- post cards on the home/category pages
- numbered pagination (`1 2 ... Next`)
- detail pages per post slug that may expose direct or embedded playable sources

Use the existing `viralkand`, `bollywoodmaal`, and `hornysimp` scrapers as closest references.

### Host aliases

- `indianporn365.xyz`
- `www.indianporn365.xyz`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "indianporn365.xyz" or h.endswith(".indianporn365.xyz")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate item links from thumbnail/title anchors in the main post grid.
- Keep only same-domain detail URLs and skip utility/legal links such as:
  - `/contact-us`
  - `/privacy-policy`
  - `/cookie-policy`
  - `/18-u-s-c-2257`
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible anchor text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration/views: parse nearby card text when present; keep optional if absent
- Page 1 should use `base_url` unchanged.
- For page > 1, follow site pager links first (WordPress often uses `/page/{n}/`). If no pager URL can be inferred, fallback to adding `?paged={page}`.

Useful list base URLs:

- `https://indianporn365.xyz/`
- `https://indianporn365.xyz/bhabhi/`
- `https://indianporn365.xyz/leaked-amateur-porn/`
- `https://indianporn365.xyz/desi-sex-videos/`
- `https://indianporn365.xyz/tamil-porn/`
- `https://indianporn365.xyz/hd-porn/`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`)
  4. visible `h1` / `<title>` fallback
- Stream extraction order:
  - `<video src>` / `<video><source src>`
  - inline script URLs ending in `.mp4` or `.m3u8`
  - `iframe[src]` embeds as fallback
- Unescape script URLs before returning (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` entries as:
  - direct media: `format="mp4"` / `format="hls"`
  - embedded players: `format="embed"` with labels like `Server 1`, `Server 2`
- Set `video.default` preference:
  1. highest-quality direct MP4
  2. HLS URL
  3. first playable embed

If the page only exposes third-party embeds, return embed streams instead of fabricated direct media links.

### Categories (`get_categories`)

Seed `categories.json` from the site's public nav/category pages and keep the same schema as other scraper folders so `/api/v1/categories?source=indianporn365` returns valid `CategoryItem` entries.

### Registration checklist for IndianPorn365

Besides creating `backend/app/scrapers/indianporn365/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=indianporn365`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still relies on explicit domain allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### IndianPorn365 verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://indianporn365.xyz/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://indianporn365.xyz/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=indianporn365"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://indianporn365.xyz/<video-post-slug>/"
```

## MMSBro Implementation Notes

[MMSBro](https://mmsbro.com/) is a WordPress-style clip index with:

- homepage card grid linking to post slugs (`https://mmsbro.com/<post-slug>/`)
- category archives (`/category/<slug>/`)
- numbered pagination via path segments (`/page/{n}/`)
- detail pages that may expose direct media in `<video>` tags, `<source>` tags, inline script URLs, or embedded players

Use `indianporn365`, `viralkand`, and `bollywoodmaal` as closest implementation references.

### Host aliases

- `mmsbro.com`
- `www.mmsbro.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "mmsbro.com" or h.endswith(".mmsbro.com")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate item links from anchor cards on homepage/category pages.
- Keep only same-domain post URLs and skip utility routes such as:
  - `/contact`
  - `/privacy-policy`
  - `/cookie-policy`
  - `/18-u-s-c-2257`
  - feed/tag/author pages
- Prefer metadata in this order:
  - title: anchor text / `title`
  - thumbnail: card image `data-src`, `data-lazy-src`, `srcset`, then `src`
  - duration: parse `mm:ss` / `hh:mm:ss` near the card title
  - views/uploader: optional (extract if available, else keep `None`)
- Page 1 should use `base_url` unchanged.
- For page > 1, first follow path pagination (`/page/{n}/`). If search query style is used (`?s=query`), add `paged={n}` as query fallback.

Useful list base URLs:

- `https://mmsbro.com/`
- `https://mmsbro.com/page/2/`
- `https://mmsbro.com/category/desi-mms/`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with `quality` labels (`Server 1`, `Server 2`, ...)
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from live nav/category archives (for example `/category/desi-mms/`) and keep schema aligned with existing scraper folders so `/api/v1/categories?source=mmsbro` returns valid `CategoryItem` entries.

### Registration checklist for MMSBro

Besides creating `backend/app/scrapers/mmsbro/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=mmsbro`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host error text
  - per-quality response block where applicable
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit domain allowlists, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### MMSBro verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://mmsbro.com/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://mmsbro.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://mmsbro.com/category/desi-mms/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=mmsbro"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://mmsbro.com/<video-post-slug>/"
```

## KamaBaba Implementation Notes

[KamaBaba](https://www.thekamababa.com/) is a WordPress-style clip index with:

- sort tabs on listing pages (`Newest`, `Best`, `Most viewed`, `Longest`, `Random`)
- category and tag archive routes
- numbered pagination (`1 2 3 ... Next Last`)
- detail pages that may expose playable sources through native `<video>` tags, inline script URLs, or embedded players

Use `mmsbro`, `indianporn365`, and `viralkand` as the closest implementation references.

### Host aliases

- `thekamababa.com`
- `www.thekamababa.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "thekamababa.com" or h.endswith(".thekamababa.com")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate detail-page links from thumbnail/title anchors in the main listing grid.
- Keep only same-domain post URLs and skip obvious utility links such as:
  - `/contact-us`
  - `/video-removal`
  - `/privacy-policy`
  - `/18-usc-2257`
  - `/advertise`
  - `/jobs`
  - `/unblock-kmb`
  - auth/profile/reset-password pages
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible anchor text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration/views/rating: parse nearby card text when present; keep optional when absent
- Page 1 should use `base_url` unchanged.
- For page > 1, follow explicit pager links first. If no pager URL is detected, fallback to WordPress patterns (`/page/{n}/`, then `?paged={n}`).
- If `base_url` already contains a sort/search query, preserve existing query params when adding page params.

Useful list base URLs:

- `https://www.thekamababa.com/`
- `https://www.thekamababa.com/categories/`
- `https://www.thekamababa.com/tags/`
- `https://www.thekamababa.com/?s=<query>`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with qualities like `Server 1`, `Server 2`, ...
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from live category/tag pages and keep schema aligned with existing scraper folders so `/api/v1/categories?source=kamababa` returns valid `CategoryItem` entries.

### Registration checklist for KamaBaba

Besides creating `backend/app/scrapers/kamababa/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=kamababa`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### KamaBaba verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.thekamababa.com/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.thekamababa.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.thekamababa.com/categories/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=kamababa"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.thekamababa.com/<video-post-slug>/"
```

## DesiMMS2 Implementation Notes

[DesiMMS2](https://www.desimms2.site/) is a WordPress-style clip index with:

- sort tabs on listing pages (`Newest`, `Best`, `Most viewed`, `Longest`, `Random`)
- category and tag archive routes
- numbered pagination (`1 2 3 ... Next Last`)
- detail pages exposing playable sources via native `<video>` tags, inline script URLs, or embedded players

Use `kamababa`, `mmsbro`, and `indianporn365` as the closest implementation references.

### Host aliases

- `desimms2.site`
- `www.desimms2.site`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "desimms2.site" or h.endswith(".desimms2.site")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate detail-page links from card thumbnail/title anchors.
- Keep only same-domain post URLs and skip utility links such as:
  - `/report-content`
  - `/18-u-s-c-2257`
  - `/categories/`
  - `/tags/`
  - auth/login/reset-password/profile paths
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible anchor text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration/views/rating: parse nearby card text where available; keep optional if absent
- Page 1 should use `base_url` unchanged.
- For page > 1, follow explicit pager links first. If no pager URL is detected, fallback to WordPress patterns (`/page/{n}/`, then `?paged={n}`).
- If `base_url` includes a search query (`?s=`), preserve query params and append `paged={n}`.

Useful list base URLs:

- `https://www.desimms2.site/`
- `https://www.desimms2.site/categories/`
- `https://www.desimms2.site/tags/`
- `https://www.desimms2.site/?s=<query>`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with qualities like `Server 1`, `Server 2`, ...
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from live category/tag pages and keep schema aligned with existing scraper folders so `/api/v1/categories?source=desimms2` returns valid `CategoryItem` entries.

### Registration checklist for DesiMMS2

Besides creating `backend/app/scrapers/desimms2/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=desimms2`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### DesiMMS2 verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.desimms2.site/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.desimms2.site/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.desimms2.site/categories/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=desimms2"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.desimms2.site/<video-post-slug>/"
```

## ThotsPorn Implementation Notes

[Thots Porn](https://thotsporn.com/) is a WordPress-style clip index with:

- tabbed listing views (Latest videos, Longest videos, Random videos)
- taxonomy-driven navigation (Categories, Tags, Actors)
- numbered pagination (`1 2 3 ... Next Last`)
- detail pages that may expose playable sources via native `<video>`, iframe embeds, or inline script URLs

Use `desimms2`, `kamababa`, and `mmsbro` as the closest implementation references.

### Host aliases

- `thotsporn.com`
- `www.thotsporn.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "thotsporn.com" or h.endswith(".thotsporn.com")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate detail-page links from card thumbnail/title anchors in the main grid.
- Keep only same-domain post URLs and skip utility/account links such as:
  - login/reset-password/profile/auth pages
  - legal/compliance pages (`/dmca`, `/2257`, `/terms`, `/privacy`) when present
  - taxonomy root pages without a concrete video detail target
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible anchor text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration/views/rating: parse nearby card text where available; keep optional if absent
- Page 1 should use `base_url` unchanged.
- For page > 1, follow explicit pager links first. If no pager URL is detected, fallback to WordPress patterns (`/page/{n}/`, then `?paged={n}`).
- If `base_url` contains a sort/search query, preserve existing query parameters while adding page params.

Useful list base URLs to support:

- `https://thotsporn.com/`
- `https://thotsporn.com/categories/`
- `https://thotsporn.com/tags/`
- `https://thotsporn.com/actors/`
- `https://thotsporn.com/?s=<query>`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with qualities like `Server 1`, `Server 2`, ...
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from live category/tag/actor index pages and keep schema aligned with existing scraper folders so `/api/v1/categories?source=thotsporn` returns valid `CategoryItem` entries.

### Registration checklist for ThotsPorn

Besides creating `backend/app/scrapers/thotsporn/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=thotsporn`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### ThotsPorn verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://thotsporn.com/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://thotsporn.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://thotsporn.com/categories/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=thotsporn"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://thotsporn.com/<video-post-slug>/"
```

## LeakedAmateurPorn Implementation Notes

[Leaked Amateur Porn](https://leakedamateurporn.xyz/) is a WordPress-style clip index with:

- tabbed listing views (Latest videos, Longest videos, Random videos)
- taxonomy-driven navigation (Categories, Tags)
- numbered pagination (`1 2 3 ... Next Last`)
- detail pages that may expose playable sources via native `<video>`, iframe embeds, or inline script URLs

Use `thotsporn`, `desimms2`, and `kamababa` as the closest implementation references.

### Host aliases

- `leakedamateurporn.xyz`
- `www.leakedamateurporn.xyz`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "leakedamateurporn.xyz" or h.endswith(".leakedamateurporn.xyz")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse candidate detail-page links from card thumbnail/title anchors in the main grid.
- Keep only same-domain post URLs and skip utility/account links such as:
  - login/reset-password/profile/auth pages
  - legal/compliance pages (`/terms`, `/privacy`, `/2257`, `/contact`) when present
  - taxonomy root pages without a concrete video detail target
- Prefer metadata in this order:
  - title: anchor `title`, image `alt`, then visible anchor text
  - thumbnail: `data-src`, `data-lazy-src`, `srcset` first URL, then `src`
  - duration/views/rating: parse nearby card text where available; keep optional if absent
- Page 1 should use `base_url` unchanged.
- For page > 1, follow explicit pager links first. If no pager URL is detected, fallback to WordPress patterns (`/page/{n}/`, then `?paged={n}`).
- If `base_url` contains a sort/search query, preserve existing query parameters while adding page params.

Useful list base URLs to support:

- `https://leakedamateurporn.xyz/`
- `https://leakedamateurporn.xyz/categories/`
- `https://leakedamateurporn.xyz/tags/`
- `https://leakedamateurporn.xyz/?s=<query>`

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with qualities like `Server 1`, `Server 2`, ...
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from live category/tag index pages and keep schema aligned with existing scraper folders so `/api/v1/categories?source=leakedamateurporn` returns valid `CategoryItem` entries.

### Registration checklist for LeakedAmateurPorn

Besides creating `backend/app/scrapers/leakedamateurporn/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=leakedamateurporn`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### LeakedAmateurPorn verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://leakedamateurporn.xyz/<video-post-slug>/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://leakedamateurporn.xyz/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://leakedamateurporn.xyz/categories/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=leakedamateurporn"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://leakedamateurporn.xyz/<video-post-slug>/"
```

## Zeenite Implementation Notes

[Zeenite](https://zeenite.com/) is a tube-style index where canonical detail pages follow `/videos/{id}/{slug}/`. The site exposes feed/navigation views for New Videos, Top Videos, Most Viewed, Categories, Models, and search.

Use `desiporn`, `thotsporn`, and `xhamster2` as close implementation references.

### Host aliases

- `zeenite.com`
- `www.zeenite.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower()
    return h == "zeenite.com" or h.endswith(".zeenite.com")
```

### Listing and pagination (`list_videos`)

Recommended list strategy:

- Parse card links that match `/videos/{numeric_id}/{slug}/`.
- Keep only same-domain video detail URLs and skip utility pages such as `/terms` and `/2257`.
- Prefer metadata in this order:
  - title: anchor text, then `title`, then image `alt`
  - thumbnail: `data-src`, `data-original`, first `srcset` candidate, then `src`
  - duration/views/rating: parse compact values from nearby card text when available
- Page 1 should use `base_url` unchanged.
- For page > 1, follow any visible paginator route first; if not present, fallback to common patterns like `?page={n}`.
- If list endpoints are loaded incrementally ("Load more"), allow scraper fallback logic that can parse the first page reliably and advance by discovered links/params.

Useful list base URLs to support:

- `https://zeenite.com/`
- `https://zeenite.com/new-videos/` (or equivalent route used by live markup)
- `https://zeenite.com/top-videos/` (or equivalent route used by live markup)
- `https://zeenite.com/most-viewed/` (or equivalent route used by live markup)
- `https://zeenite.com/categories/`
- `https://zeenite.com/models/`
- `https://zeenite.com/search/<term>/` (or the query/search endpoint exposed by the page)

### Metadata and streams (`scrape`)

For detail pages:

- Metadata fallback order:
  1. `og:title`, `og:description`, `og:image`
  2. `twitter:title`, `twitter:description`, `twitter:image`
  3. JSON-LD `VideoObject`
  4. visible `h1` / page `<title>`
- Stream extraction order:
  - direct `<video src>` and `<video><source src>`
  - inline script URLs matching `.mp4` or `.m3u8`
  - iframe embeds as fallback
- Unescape script URLs before use (`\\/` -> `/`, `\\u0026` -> `&`).
- Build `video.streams` with:
  - direct media: `format="mp4"` / `format="hls"`
  - embeds: `format="embed"` with qualities like `Server 1`, `Server 2`, ...
- Set `video.default` preference:
  1. highest-priority direct MP4
  2. HLS URL
  3. first playable embed

If a page exposes only embedded players, return embed streams instead of manufacturing direct media URLs.

### Categories (`get_categories`)

Seed `categories.json` from the site's public Categories and Models indexes and keep schema aligned with existing scraper folders so `/api/v1/categories?source=zeenite` returns valid `CategoryItem` entries.

### Registration checklist for Zeenite

Besides creating `backend/app/scrapers/zeenite/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=zeenite`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Zeenite verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://zeenite.com/videos/215600/dance-kabyle-chaude-9a7ba-de-tizi-ouazou/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://zeenite.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=zeenite"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://zeenite.com/videos/215600/dance-kabyle-chaude-9a7ba-de-tizi-ouazou/"
```

## 85PO Implementation Notes

[85PO](https://www.85po.com/) is a KVS-style tube site (Chinese UI). Video pages use `/v/{id}/{slug}/` and expose progressive MP4 via same-origin `/get_file/...` URLs (often `_720p`, `_1080p`, and a basename `source` tier).

Use `zeenite` and `pimpbunny` as close implementation references (module folder name is `po85` because Python identifiers cannot start with a digit).

### Host aliases

- `85po.com`
- `www.85po.com`

Example:

```python
def can_handle(host: str) -> bool:
    h = (host or "").lower().split(":")[0]
    if h.startswith("www."):
        h = h[4:]
    return h == "85po.com" or h.endswith(".85po.com")
```

### Listing and pagination (`list_videos`)

- Video URLs: `https://www.85po.com/v/{id}/{slug}/`
- Embed player: `https://www.85po.com/embed/{id}` (iframe shell; also exposes `/get_file/` MP4 tiers inside)
- Parse only the main list block (not the “watching now” sidebar):
  - home / default: `#list_videos_most_recent_videos`
  - `/4k/`: `#list_videos_latest_videos_list`
  - `/tags/...`: `#list_videos_common_videos_list`
- Pagination uses query param `from` (page 2 → `?from=2`), not `?page=`. AJAX `#more` blocks exist but GET `?from={n}` is sufficient for the API list endpoint.

### Metadata and streams (`scrape`)

- Metadata: `og:*`, `h1`, visible duration (`mm:ss` / `hh:mm:ss`), views from `svg.icon-eye` parent (`.thumb-item` on cards, `.count-item` on detail).
- Streams: inline `/get_file/.../*.mp4` links in HTML; filter screenshot/preview assets (`preview_preview.mp4.jpg`, `/contents/videos_screenshots/`).
- Resolve each `get_file` URL with the video page as `Referer` (HEAD/GET + `Range`) to the signed CDN redirect before returning `video.streams` (same pattern as Zeenite).
- Prefer highest `NNNp` MP4 as `video.default`.

### Categories (`get_categories`)

Seed `categories.json` from public nav: Home, 4K (`/4k/`), Tags (`/tags/`), Random (`/random_video.php`).

### Registration checklist for 85PO

Besides creating `backend/app/scrapers/po85/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=po85` or `source=85po`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host/unsupported-host help text
  - stream quality map host checks for `85po.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="po85"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### 85PO verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.85po.com/v/30261/zi-cuo-ri--5/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.85po.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=po85"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.85po.com/v/30261/zi-cuo-ri--5/"
```

## CosXplay Implementation Notes

[CosXplay](https://cosxplay.com/) is a WordPress **kolortube** cosplay tube. Canonical video pages use `/{post_id}-{slug}/` (for example `/78642-furries-2022-.../`). Listings use `.video-block[data-post-id]` cards; pagination is WordPress-style `/page/{n}/` (including on category paths).

Use `hornysimp` for embed fallbacks and `zeenite` for JSON-LD + stream ordering patterns.

### Host aliases

- `cosxplay.com`
- `www.cosxplay.com`

### Listing and pagination (`list_videos`)

- Home: `https://cosxplay.com/` → page 2 is `https://cosxplay.com/page/2/`
- Category: `https://cosxplay.com/7841-nier-automata/` → `https://cosxplay.com/7841-nier-automata/page/2/`
- Parse cards via `div.video-block[data-post-id]` → `a.infos[href]` / `a.thumb[href]`; duration from `.video-datas span.duration.notranslate` (or `span.duration` on the card)
- Only accept single-segment `/{id}-{slug}/` URLs (exclude `/tag/`, `/categories/`, `/embed/`, etc.)

### Metadata and streams (`scrape`)

- Metadata: `og:*`, JSON-LD `VideoObject` (`name`, `description`, `thumbnailUrl`, `duration`, `contentUrl`, `embedUrl`, `interactionStatistic`), and inline `toStore` (`views`, `length`, `preview`).
- Streams: signed MP4 on `xcdn*.nosofiles.com` (`*_high.mp4`, `*_low.mp4`) from `<video><source>`, inline `videoHigh` / `videoLow` JS, and JSON-LD `contentUrl`. Skip `trailer.mp4` / poster assets.
- Optional embed stream from JSON-LD `embedUrl` (`https://cosxplay.com/embed/{id}`) when direct MP4 is unavailable.
- Cloudflare may challenge bare requests; send `Referer: https://cosxplay.com/` (homepage first helps for curl/manual tests).

### Categories (`get_categories`)

Seed from nav: Home, Categories, Cosplay Girls, Tags, plus popular character/genre hubs from the mobile category menu.

### Registration checklist for CosXplay

Besides creating `backend/app/scrapers/cosxplay/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=cosxplay` or `source=cosx`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `cosxplay.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="cosxplay"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### CosXplay verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://cosxplay.com/78642-furries-2022-fursuit-yiff-murrsuit-oral-butt-point-of-view-amaze-anal-cosplay-furry/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://cosxplay.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=cosxplay"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://cosxplay.com/78642-furries-2022-fursuit-yiff-murrsuit-oral-butt-point-of-view-amaze-anal-cosplay-furry/"
```

## MemoJav Implementation Notes

[MemoJav](https://memojav.com/) is a JAV catalog site. Canonical video pages use `/video/{CODE}` **without** a trailing slash (for example `/video/START-579` — `/video/START-579/` returns 404). Listings use `a.video-item` cards with `img.video-poster`; pagination is `page-{n}` under the current section path without a trailing slash (for example `/video/page-2`).

### Host aliases

- `memojav.com`
- `www.memojav.com`

### Listing and pagination (`list_videos`)

- Home: `https://memojav.com/`
- Best: `https://memojav.com/best/`
- New: `https://memojav.com/video/`
- Page 2 on new videos: `https://memojav.com/video/page-2` (no trailing slash — `/video/page-2/` is 404)
- Parse `a.video-item[href]` → title from `.video-title`, thumb from `img.video-poster`

### Metadata and streams (`scrape`)

- Metadata: `og:*`, `#title`, `#title-description`, `var mm = {type,id,vi}`, schema `itemprop="duration"` (`PT123M0S`), actress link, trailer `#preview-vid`.
- Full movie streams come from `/hls/get_video_info.php?id={CODE}&sig=...&sts=...` (same `video_sig()` algorithm as `static/main.js`). Response is JSON prefixed with `for (;;);`.
  - `type: "hls"` → `master.m3u8` on `video*.memojav.net` (preferred default).
  - `type: "mp4"` → base URL with `=m37` / `=m22` / `=m18` quality suffixes (JW Player convention).
- Always include embed fallback: `https://memojav.com/embed/{CODE}`.

### Categories (`get_categories`)

Seed from nav: Hot Videos (home), Best, New, Actress, Studio, Series, Categories, Label, Director.

### Registration checklist for MemoJav

Besides creating `backend/app/scrapers/memojav/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=memojav` or `source=memo`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `memojav.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="memojav"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### MemoJav verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://memojav.com/video/START-579\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://memojav.com/video/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=memojav"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://memojav.com/video/START-579"
```

## HoHoJ Implementation Notes

[HoHoJ](https://hohoj.tv/) (好好J) is a JAV catalog site in the GGJAV family (CDN thumbnails on `cdn-*.ggjav.com`, streams on `video-*.ggjav.com`). Video pages use numeric IDs: `/video?id={ID}` (not slug paths). The detail page embeds `/embed?id={ID}`, which exposes the HLS master URL in `<video src="...index.m3u8">` and `var videoSrc = "..."`.

### Host aliases

- `hohoj.tv`
- `www.hohoj.tv`

### Listing and pagination (`list_videos`)

- Home: `https://hohoj.tv/`
- Browse by type (query param `type`):
  - All: `https://hohoj.tv/search?type=all&p=1`
  - Censored: `https://hohoj.tv/search?type=censored&p=1`
  - Chinese subtitles: `https://hohoj.tv/search?type=chinese&p=1`
  - Uncensored: `https://hohoj.tv/search?type=uncensored&p=1`
  - Western: `https://hohoj.tv/search?type=europe&p=1`
- Sort order (optional `order`): `popular` (default), `latest`, `views`, `likes`
- Text search: `https://hohoj.tv/search?text={query}&p=1`
- Actresses index: `https://hohoj.tv/all_models`
- Parse cards in `div.video-item`; links are rendered as `{% if href="/video?id=123" %}` — extract with regex `/video?id=\d+`
- Pagination: set/replace query param `p` (page 2 → `p=2`)

### Metadata and streams (`scrape`)

- Metadata: `og:*`, `h5.mt-3`, `.info` (views/date), `.model` (actress), `.ctg a` (tags)
- Streams: fetch `https://hohoj.tv/embed?id={ID}`; read HLS from `#my-video[src]` or `videoSrc` in inline script
- Always include embed fallback: `https://hohoj.tv/embed?id={ID}`

### Categories (`get_categories`)

Seed from nav/browse: Home, All, Censored, Chinese Subtitles, Uncensored, Western, Actresses.

### Registration checklist for HoHoJ

Besides creating `backend/app/scrapers/hohoj/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=hohoj` or `source=hohojtv`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `hohoj.tv` and `ggjav.com` (CDN)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="hohoj"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### HoHoJ verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://hohoj.tv/video?id=51730\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://hohoj.tv/search?type=all&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=hohoj"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://hohoj.tv/video?id=51730"
```

## GGJAV Implementation Notes

[GGJAV](https://ggjav.com/) is the flagship JAV catalog in the same CDN/player family as [HoHoJ](https://hohoj.tv/) (`cdn-*.ggjav.com`, `video-*.ggjav.com`). Video pages use `/main/video?id={ID}` (numeric catalog id). Streams are not on the bare `/main/embed?id={ID}` page; they come from a base64 player map embedded in the video page.

### Host aliases

- `ggjav.com`
- `www.ggjav.com`
- `ggjav.tv` (mirror)

### Listing and pagination (`list_videos`)

- Home: `https://ggjav.com/`
- Section listings:
  - Censored: `https://ggjav.com/main/censored`
  - Uncensored: `https://ggjav.com/main/uncensored`
  - Amateur: `https://ggjav.com/main/amateur`
  - Chinese subtitles: `https://ggjav.com/main/chinese`
  - Western: `https://ggjav.com/main/europe`
  - Anime: `https://ggjav.com/main/cartoon`
- Text search: `https://ggjav.com/main/search?string={query}`
- Parse cards in `div.item` with `a[href*="/main/video?id="]`; title in `.item_title`, thumb `img.item_image`, views in `.item_views`
- Pagination: query param `page` (site sometimes emits `&&page` — normalize to `&page`)

### Metadata and streams (`scrape`)

- Metadata: `og:*`, `.title_text`, `.info img`, `.ctg_button` / `.ctg a`, optional `.model .model_name`
- Player map: `var l = "{base64}"` on the video page → decode (`b64` then subtract `0x58` per byte) → JSON object `links.{server}[]`
- Preferred HLS path: `links.ggjav[0]` is `/main/embed?u={base64_mp4_path}&poster=...` → decode `u` → append `/index.m3u8` to the `.mp4` base URL (e.g. `https://video-6.ggjav.com/video_1/...mp4/index.m3u8`)
- Alternate embed fallbacks: `mmfl04`, `mmsw02`, `embedrise`, `tapewithadblock`, etc. from the same `links` map
- Embed fallback: `https://ggjav.com/main/embed?id={ID}`

### Categories (`get_categories`)

Seed from nav: Home, Censored, Uncensored, Amateur, Chinese Subtitles, Western, Anime, All Actresses, Uncensored Actresses.

### Registration checklist for GGJAV

Besides creating `backend/app/scrapers/ggjav/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=ggjav` or `source=ggjavtv`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `ggjav.com`, `ggjav.tv`, and `video-*.ggjav.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="ggjav"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### GGJAV verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://ggjav.com/main/video?id=256833\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://ggjav.com/main/uncensored&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=ggjav"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://ggjav.com/main/video?id=256833"
```

## Porn87 Implementation Notes

[Porn87](https://porn87.com/) is a user-upload JAV/homemade catalog (GGJAV-family CDN: `cdn-*.porn87.com`, HLS on `cdn-*.porn87.com/media/video_*`). Video pages use `/main/html?id={ID}` (not `/main/video`). The player lives at `/main/embed?id={ID}` with direct HLS in `<video src="...index.m3u8">` / `videoSrc`.

### Host aliases

- `porn87.com`
- `www.porn87.com`
- `porn87.tv` (mirror)

### Listing and pagination (`list_videos`)

- Home: `https://porn87.com/`
- Latest: `https://porn87.com/main/tag?lineup=create_time`
- Popular: `https://porn87.com/main/tag?lineup=recent_views`
- Tag browse: `https://porn87.com/main/tag?name={tag}` (e.g. `高清日本AV`, `中港台`)
- Text search: `https://porn87.com/main/search?name={query}`
- Parse cards in `div.chunk > a[href*="/main/html?id="]`; thumb `img.video_thumbnail`, duration `.video_time`, views/likes via `fi-eye` / `fi-heart`
- Pagination: query param `page` is **1-based** (UI page 2 → `page=2`; API `page=1` omits the param)

### Metadata and streams (`scrape`)

- Metadata: `og:*`, title spans, `.video_time`, tag links (`/main/tag?name=`), optional model links
- Streams: fetch `https://porn87.com/main/embed?id={ID}` → read HLS from `#my-video[src]` or `var videoSrc`
- Optional multi-server map on the HTML page: same `var l = "{base64}"` decode as GGJAV (`b64` then subtract `0x58` per byte) for external embed fallbacks
- Embed fallback: `https://porn87.com/main/embed?id={ID}`

### Categories (`get_categories`)

Seed from nav: Home, Latest, Popular, HD Japanese AV, Asian Homemade (中港台), All Tags, Actresses.

### Registration checklist for Porn87

Besides creating `backend/app/scrapers/porn87/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=porn87` or `source=porn87tv`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `porn87.com`, `porn87.tv`, and `cdn-*.porn87.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="porn87"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Porn87 verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://porn87.com/main/html?id=5952\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://porn87.com/main/tag?lineup=create_time&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=porn87"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://porn87.com/main/html?id=5952"
```

## GoodAV (正妹AV) Implementation Notes

[GoodAV](http://goodav17.com/) (`goodav17.com`) is a JAV catalog in the GGJAV CDN family. Video pages use `/html/{ID}/`; playback is via an embedded `ggjav.com/main/embed?u={base64_mp4_path}&site=goodav` iframe (HLS on `video-*.ggjav.com` / `cdn-*.ggjav.com`).

### Host aliases

- `goodav17.com`
- `www.goodav17.com`

### Listing and pagination (`list_videos`)

- Home (latest): `http://goodav17.com/` — page *n* &gt; 1 is `http://goodav17.com/{n}/`
- Types: `http://goodav17.com/type/{name}/{page}/` (e.g. `/type/無碼/1/`, page 2 → `/type/無碼/2/`)
- Actresses: `http://goodav17.com/actor/{name}/{page}/`
- VR: `http://goodav17.com/vr/{page}/`
- Homemade: `http://goodav17.com/local/{page}/`
- Parse cards in `div.movie` → `a[href*="/html/"]`; thumbs from `img` (`src`, `large_image` on `cdn-*.ggjav.com`)

### Metadata and streams (`scrape`)

- Metadata: `og:*`, title, tag/actor links (`/type/`, `/actor/`)
- Streams: read `iframe#video_frame` → GGJAV embed URL → decode `u` query (base64 MP4 path) → `{path}/index.m3u8`, or fetch embed HTML for `videoSrc` (same helpers as `ggjav` scraper)
- Embed fallback: the GGJAV embed URL from the iframe

### Categories (`get_categories`)

Seed from nav: Home, sample types (無碼, 人妻, 巨乳, 中出), VR, Asian Homemade, sample actress.

### Registration checklist for GoodAV

Besides creating `backend/app/scrapers/goodav/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=goodav` or `source=goodav17`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks (`goodav17.com`; media CDN already covered via `ggjav.com` / `video-*.ggjav.com`)
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="goodav"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### GoodAV verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"http://goodav17.com/html/20818/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=http://goodav17.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=http://goodav17.com/type/%E7%84%A1%E7%A2%BC/1/&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=goodav"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=http://goodav17.com/html/20818/"
```

## KanAV Implementation Notes

[KanAV](https://kanav.ad/) (`kanav.ad`) is a MacCMS (苹果CMS) JAV site. Listings link to play pages; the player exposes `player_aaaa` JSON with `encrypt: 2` and a base64-encoded HLS URL (decoded per MacCMS `player.js`: base64 then `unescape`).

### Host aliases

- `kanav.ad`
- `www.kanav.ad`

### Listing and pagination (`list_videos`)

- Home: `https://kanav.ad/` (section grids; for page &gt; 1 prefer a type URL)
- Categories: `https://kanav.ad/index.php/vod/type/id/{type_id}.html`
- Page *n* &gt; 1: `https://kanav.ad/index.php/vod/type/id/{type_id}/page/{n}.html`
- Parse `a[href*="/index.php/vod/play/id/"]`; merge duplicate IDs; title from link text or `img[alt]`
- Thumbs on `img.11yun.xyz`

### Metadata and streams (`scrape`)

- Canonical play URL: `https://kanav.ad/index.php/vod/play/id/{ID}/sid/1/nid/1.html`
- Also accept `/index.php/vod/detail/id/{ID}.html` (same ID, fetches play page)
- Streams: parse `player_aaaa={...}` from play HTML → `"url"` field → base64 decode when `encrypt==2` → `.m3u8` on `*.11yun.space` / `*.11yun.xyz`
- Title from `vod_data.vod_name`, `og:title`, or `<title>`

### Categories (`get_categories`)

Seed from nav type links: Home, 中文字幕 (id=1), 日韩有码, 日韩无码, 国产AV, etc.

### Registration checklist for KanAV

Besides creating `backend/app/scrapers/kanav/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=kanav`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `kanav.ad`, `11yun.xyz`, `11yun.space`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="kanav"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### KanAV verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://kanav.ad/index.php/vod/play/id/111060/sid/1/nid/1.html\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://kanav.ad/index.php/vod/type/id/1.html&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://kanav.ad/index.php/vod/type/id/1.html&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=kanav"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://kanav.ad/index.php/vod/play/id/111060/sid/1/nid/1.html"
```

## MissAV Implementation Notes

[MissAV](https://missav.ai/) is a JAV catalog site. Video pages use a DVD-style slug (`fc2-ppv-1434674`, `ssni-123`, etc.) with optional locale prefix (`/en/`, `/ja/`, …). Thumbnails and previews are served from `fourhoi.com`; HLS playback uses obfuscated `surrit.com` URLs in an inline `eval(...)` player block.

### Host aliases

- `missav.ai`
- `www.missav.ai`

### Listing and pagination (`list_videos`)

- Browse URLs use a rotating `dm{id}` prefix, e.g. `https://missav.ai/dm428/fc2`, `https://missav.ai/dm539/new`
- Localized browse: `https://missav.ai/dm428/en/fc2`
- Parse cards in `div.thumbnail` → `a[href]` to `https://missav.ai/{slug}` or `https://missav.ai/en/{slug}`; thumb `img[data-src]` (`fourhoi.com/{slug}/cover-t.jpg`), duration in `span.absolute.bottom-1.right-1`
- Pagination: query param `page` (page 2 → `?page=2`). Preserve the full `dm{id}/…` path from `base_url` (the numeric `dm` segment can change between mirrors)

### Metadata and streams (`scrape`)

- Canonical page: `https://missav.ai/en/{dvd-slug}` (also accept `https://missav.ai/{dvd-slug}` and mirror paths like `https://missav.ai/dm1/en/{dvd-slug}`)
- Metadata: `og:title`, `og:image` (`fourhoi.com/{slug}/cover-n.jpg`), `og:video:duration` (seconds), `og:video:release_date`, `<h1>`, actress/genre links
- Streams: locate `eval(function(p,a,c,k,e,d){...}('e=\'...\';c=\'...\';b=\'...\';',15,15,'m3u8|...|surrit|https|...'.split('|'),0,{}))` → decode digit placeholders against the split array; `d` in the template is the `dvdId` slug → master HLS is variable `e`, e.g. `https://surrit.com/{hash}/{dvd-slug}.m3u8`
- `dvdId` is also exposed in Alpine `x-data` as `dvdId: 'fc2-ppv-1434674'`

### Categories (`get_categories`)

Seed from nav `dm*` links: New Releases, Recent Update, Uncensored Leak, Chinese Subtitle, FC2, hot lists, SIRO, LUXU, HEYZO, etc.

### Registration checklist for MissAV

Besides creating `backend/app/scrapers/missav/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=missav` or `source=missavai`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `missav.ai` and `surrit.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="missav"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### MissAV verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://missav.ai/en/fc2-ppv-1434674\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://missav.ai/dm428/fc2&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=missav"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://missav.ai/en/fc2-ppv-1434674"
```

## Jable Implementation Notes

[Jable.TV](https://jable.tv/) is a JAV catalog site. Video pages use slug URLs under `/videos/{code}/` (e.g. `start-579`). HLS is exposed inline as `var hlsUrl = '...m3u8'` on the player page (CDN hosts such as `*.mushroomtrack.com`).

### Host aliases

- `jable.tv`
- `www.jable.tv`

### Listing and pagination (`list_videos`)

- Latest: `https://jable.tv/latest-updates/`
- Hot: `https://jable.tv/hot/`
- New release: `https://jable.tv/new-release/`
- Categories: `https://jable.tv/categories/{slug}/`
- Tags: `https://jable.tv/tags/{slug}/`
- Parse cards in `div.video-img-box` → `.img-box a[href*='/videos/']`; title in `h6.title a`; thumb `img[data-src]`; duration in `span.label`; views in `p.sub-title`
- Pagination: append page segment — page 2 of latest is `https://jable.tv/latest-updates/2/`

### Metadata and streams (`scrape`)

- Canonical page: `https://jable.tv/videos/{slug}/` (also accept mirror paths like `https://jable.tv/s0/videos/{slug}/`)
- Metadata: `og:title`, `og:image`, `section.video-info h4`, actress links in `.models`, tags in `h5.tags a`, views in `h6 span`, release date in `.header-right span.inactive-color`
- Streams: `var hlsUrl = 'https://.../*.m3u8'` in inline script next to `#player` (Hls.js / Plyr)

### Categories (`get_categories`)

Seed from nav: Latest Updates, Hot, New Release, Categories index, sample category/tag pages (Roleplay, Chinese Subtitle, Uniform, Pantyhose, NTR).

### Registration checklist for Jable

Besides creating `backend/app/scrapers/jable/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=jable` or `source=jabletv`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `jable.tv`, `assets-cdn.jable.tv`, `mushroomtrack.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="jable"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Jable verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://jable.tv/videos/start-579/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://jable.tv/latest-updates/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=jable"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://jable.tv/videos/start-579/"
```

## Tianmei (天美影院 / 94mt.cc) Implementation Notes

[天美影院](https://www.94mt.cc/) (`94mt.cc`, easy domain `tianmei.one`) is a MacCMS (苹果CMS) Chinese adult catalog. Video pages use numeric IDs under `/index.php/vod/play/id/{ID}/sid/1/nid/1.html`. Streams come from inline `player_aaaa` JSON; this site typically uses `"encrypt":0` with a plain `"url"` HLS field (not base64 like some `encrypt:2` mirrors).

### Host aliases

- `94mt.cc`
- `www.94mt.cc`
- `tianmei.one` (alternate domain)

### Listing and pagination (`list_videos`)

- Home: `https://www.94mt.cc/`
- Categories: `https://www.94mt.cc/index.php/vod/type/id/{type_id}.html` (e.g. `1` = 麻豆视频)
- Parse `div.box-item` → `a.item-link` / `a.movie-name`; title from `a.movie-name` or `title` attr; thumb `img[src]`; optional `upload_date` from `em span`
- Pagination: `/index.php/vod/type/id/{type_id}/page/{n}.html` (page 2 → `.../page/2.html`)

### Metadata and streams (`scrape`)

- Canonical play URL: `https://www.94mt.cc/index.php/vod/play/id/{ID}/sid/1/nid/1.html`
- Also accept `/index.php/vod/detail/id/{ID}.html` (same ID, fetches play page)
- Streams: parse `player_aaaa={...}` → when `encrypt==0`, use `"url"` directly (e.g. `https://*.cdn2020.com/.../index.m3u8`); when `encrypt==2`, base64 decode then `unescape` (MacCMS `player.js`)
- Title from `vod_data.vod_name`, `<title>`, or meta keywords

### Categories (`get_categories`)

Seed from nav type links: Home, 麻豆视频, 91制片厂, 天美影院, 蜜桃传媒, etc. (`/index.php/vod/type/id/1.html` …).

### Registration checklist for Tianmei

Besides creating `backend/app/scrapers/tianmei/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=tianmei`, `source=94mt`, or `source=94mtcc`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `94mt.cc`, `cdn2020.com`, `tutu1.space`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="tianmei"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Tianmei verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.94mt.cc/index.php/vod/play/id/25106/sid/1/nid/1.html\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.94mt.cc/index.php/vod/type/id/1.html&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=tianmei"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.94mt.cc/index.php/vod/play/id/25106/sid/1/nid/1.html"
```

## BindasMood (bindasmood.com) Implementation Notes

[BindasMood](https://bindasmood.com/) is a WordPress site using the **UltimaTube** theme. Video posts use root-level slugs (not `/video/` paths). Listing uses `article.thumb-block` cards; streams are usually direct **MP4** URLs on CDN hosts (e.g. `ixifile.xyz`) embedded in post HTML, with an optional **clean-tube-player** iframe fallback.

### Host aliases

- `bindasmood.com`
- `www.bindasmood.com`

### Listing and pagination (`list_videos`)

- Home: `https://bindasmood.com/`
- Sort filters: `?filter=latest`, `?filter=popular`, `?filter=most-viewed`, `?filter=longest`, `?filter=random`
- Taxonomy indexes: `/categories/`, `/tags/`, `/actors/` (and `/category/{slug}/`, `/tag/{slug}/`, `/actor/{slug}/` for filtered lists)
- Parse `article.thumb-block` → link `a[href]`; title from `span.title a`; thumb `img`; `span.duration`, `span.views`
- Pagination: WordPress `/page/{n}/` (e.g. `https://bindasmood.com/page/2/`); query preserved on filtered home URLs

### Metadata and streams (`scrape`)

- Canonical post URL: `https://bindasmood.com/{slug}/` (single hyphenated slug segment)
- Reject reserved paths: `categories`, `tags`, `actors`, `category`, `tag`, `actor`, `page`, `wp-content`, etc.
- Streams: regex `.mp4` / `.m3u8` from post HTML; if none, fetch `clean-tube-player` iframe (`player-x.php`) and retry; last resort `format: embed` on iframe `src`
- Title/thumb from `og:title`, `og:image`, `h1`

### Categories (`get_categories`)

Home, Newest (`?filter=latest`), Best, Most Viewed, Longest, plus taxonomy index links in `categories.json`.

### Registration checklist for BindasMood

Besides creating `backend/app/scrapers/bindasmood/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=bindasmood` or `source=bindas`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `bindasmood.com`, `ixifile.xyz`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="bindasmood"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### BindasMood verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://bindasmood.com/valentine-date-2026-hindi-uncut-xxx-video/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://bindasmood.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://bindasmood.com/page/2/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=bindasmood"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://bindasmood.com/valentine-date-2026-hindi-uncut-xxx-video/"
```

## DOTMaal (dotmaal.com) Implementation Notes

[DOTMaal](https://dotmaal.com/) is a WordPress site using the **OGP** theme with the **xplayer** plugin. It aggregates Hindi/Indian OTT web series (ULLU, Atrangii, Pull, Desi Prime, Kahani Play, etc.). Episode pages use a two-segment path: `/{platform}/{episode-slug}/`. Listing cards use `div.vc-wrap` with thumb link `a.vc-thumb`, title `a.vc-title`, duration `span.vc-duration`, and OTT badge `span.vc-badge`.

### Host aliases

- `dotmaal.com`
- `www.dotmaal.com`

### Listing and pagination (`list_videos`)

- Home: `https://dotmaal.com/`
- Indexes: `/web-series/`, `/ott/`, `/models/`, `/tags/`
- Taxonomy: `/category/{slug}/` (OTT/network), `/tag/{slug}/`, `/model/{slug}/`, `/web-series/{series-slug}/`
- Parse `div.vc-wrap` → `a.vc-thumb` / `a.vc-title`; thumb `img`; `span.vc-duration`; `span.vc-badge` as `uploader_name`
- Pagination: WordPress `/page/{n}/` on any list path (e.g. `https://dotmaal.com/page/2/`, `https://dotmaal.com/category/ullu/page/2/`)

### Metadata and streams (`scrape`)

- Canonical episode URL: `https://dotmaal.com/{platform}/{episode-slug}/` (reject reserved first segments: `category`, `tag`, `model`, `web-series`, `page`, etc.)
- Streams: `<video><source src="...">` on the episode page (signed MP4 on `video.maalcdn.com`); HTML-entity decode URLs (`&#038;` → `&`); regex fallback for `.mp4` / `.m3u8`
- Title/thumb from `og:title`, `og:image`, `h1`, `video[poster]`

### Categories (`get_categories`)

Home, Web Series, OTT, Models, Tags, plus popular OTT networks (ULLU, Atrangii, Rabbit, ALTT, CinePrime, Kooku, Pull, Desi Prime, Kahani Play, Wow, Tru Uncut, Feel) in `categories.json`.

### Registration checklist for DOTMaal

Besides creating `backend/app/scrapers/dotmaal/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=dotmaal` or `source=dot`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `dotmaal.com`, `maalcdn.com`, `video.maalcdn.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="dotmaal"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### DOTMaal verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://dotmaal.com/pull/tadap-pull-episode-2/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://dotmaal.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://dotmaal.com/category/ullu/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=dotmaal"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://dotmaal.com/pull/tadap-pull-episode-2/"
```

## UncutMasti (uncutmasti.com) Implementation Notes

[UncutMasti](https://uncutmasti.com/) is a WordPress site using the **UltimaTube** theme (same stack as BindasMood). Video posts use a single root-level slug (`/{slug}/`). Listing uses `article.thumb-block` cards; streams are direct **MP4** URLs on CDN hosts (e.g. `cdn2.ixifile.xyz`) embedded in post HTML or resolved via the **clean-tube-player** iframe (`player-x.php`).

### Host aliases

- `uncutmasti.com`
- `www.uncutmasti.com`

### Listing and pagination (`list_videos`)

- Home: `https://uncutmasti.com/`
- Sort filters: `?filter=latest`, `?filter=popular`, `?filter=most-viewed`, `?filter=longest`, `?filter=random`
- Taxonomy indexes: `/categories/`, `/tags/`, `/actors/` (and `/category/{slug}/`, `/tag/{slug}/`, `/actor/{slug}/` for filtered lists)
- Parse `article.thumb-block` → link `a[href]`; title from `span.title a`; thumb `img`; `span.duration`, `span.views`
- Pagination: WordPress `/page/{n}/` (e.g. `https://uncutmasti.com/page/2/`); query preserved on filtered home URLs

### Metadata and streams (`scrape`)

- Canonical post URL: `https://uncutmasti.com/{slug}/` (single hyphenated slug segment)
- Reject reserved paths: `categories`, `tags`, `actors`, `category`, `tag`, `actor`, `page`, `wp-content`, etc.
- Streams: regex `.mp4` / `.m3u8` from post HTML; if none, fetch `clean-tube-player` iframe (`player-x.php`) and retry; last resort `format: embed` on iframe `src`
- Title/thumb from `og:title`, `og:image`, `h1`

### Categories (`get_categories`)

Home, Latest/Popular/Most viewed/Longest/Random filters, Categories/Tags/Actors indexes, plus popular OTT category links in `categories.json`.

### Registration checklist for UncutMasti

Besides creating `backend/app/scrapers/uncutmasti/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=uncutmasti` or `source=masti`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `uncutmasti.com`, `ixifile.xyz`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="uncutmasti"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### UncutMasti verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://uncutmasti.com/mona-darling-2026-moodx-hindi-xxx-web-series-episode-2/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://uncutmasti.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://uncutmasti.com/category/bindastimes-uncut-web-series/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=uncutmasti"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://uncutmasti.com/mona-darling-2026-moodx-hindi-xxx-web-series-episode-2/"
```

## ZMaal (zmaal.net) Implementation Notes

[ZMaal](https://zmaal.net/) is a WordPress site for Hindi uncut web series. The main video feed is at `/latest/`. Posts use a single root-level slug (`/{slug}/`). Listing uses `article.video` cards with `a.link`, `img`, and `span.rtitle`. Streams are signed **MP4** URLs on `video.maalcdn.com` (same CDN family as DOTMaal), embedded via `<video><source>` or HTML regex fallback.

### Host aliases

- `zmaal.net`
- `www.zmaal.net`

### Listing and pagination (`list_videos`)

- Primary feed: `https://zmaal.net/latest/`
- Indexes: `/model/`, `/web-series/`, `/hot-web-series/`
- Site search: `?s={query}` (e.g. `?s=Ullu`, `?s=Moodx`)
- Parse `article.video` → `a.link[href]`; title from `aria-label`, `title`, or `span.rtitle`; thumb `img`
- Pagination: `/latest/page/{n}/` (e.g. `https://zmaal.net/latest/page/2/`); works on any list path with WordPress-style `/page/{n}/` suffix

### Metadata and streams (`scrape`)

- Canonical post URL: `https://zmaal.net/{slug}/` (single hyphenated slug segment)
- Reject reserved paths: `latest`, `model`, `web-series`, `hot-web-series`, `page`, `wp-content`, etc.
- Streams: `<video><source src="...">` and regex `.mp4` / `.m3u8`; HTML-entity decode URLs (`&#038;` → `&`)
- Title/thumb from `og:title`, `og:image`, `h1`, `video[poster]`

### Categories (`get_categories`)

Latest feed, Home, Models, Web Series indexes, and popular keyword searches (`?s=Ullu`, etc.) in `categories.json`.

### Registration checklist for ZMaal

Besides creating `backend/app/scrapers/zmaal/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=zmaal`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `zmaal.net`, `maalcdn.com`, `video.maalcdn.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="zmaal"`, `baseUrl=https://zmaal.net/latest/`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### ZMaal verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://zmaal.net/husband-friend/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://zmaal.net/latest/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://zmaal.net/latest/page/2/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=zmaal"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://zmaal.net/pastry-episode-1/"
```

## Ullu Web Series (ulluwebseries.one) Implementation Notes

[ulluwebseries.one](https://ulluwebseries.one/) is a WordPress site (Astra + Content Views grid) for ULLU and other Hindi uncut OTT web series. Video posts live under `/hot-series/{slug}/`. The home page and taxonomy archives use **Content Views** cards (`div.pt-cv-content-item`) with thumb link, `h4.pt-cv-title`, and `images.ulluwebseries.one` thumbnails. Streams are direct **MP4** on `cdn.ulluwebseries.one` via `<video><source>` or HTML regex.

### Host aliases

- `ulluwebseries.one`
- `www.ulluwebseries.one`

### Listing and pagination (`list_videos`)

- Home: `https://ulluwebseries.one/`
- Indexes: `/categories/`, `/series/`, `/models/`, `/audio-sex-story/`
- OTT filters: `/series_category/{slug}/` (e.g. `/series_category/ullu/`, `/series_category/moodx/`)
- Parse `div.pt-cv-content-item` → `a.pt-cv-href-thumbnail` / `h4.pt-cv-title a`; thumb `img.pt-cv-thumbnail`
- Pagination: WordPress `/page/{n}/` (e.g. `https://ulluwebseries.one/page/2/`)

### Metadata and streams (`scrape`)

- Canonical watch URL: `https://ulluwebseries.one/hot-series/{slug}/`
- Reject non-video paths (`/series/`, `/categories/`, `/models/`, etc.) — only `/hot-series/` posts are scraped
- Streams: `<video><source src="...">` and regex `.mp4` / `.m3u8` on `cdn.ulluwebseries.one`
- Title/thumb from `og:title`, `og:image`, `h2`, `<title>`

### Categories (`get_categories`)

Home, Categories, Series, Models, Audio Sex Story, plus OTT `series_category` links (ULLU, MoodX, HotHit, etc.) in `categories.json`.

### Registration checklist for ulluwebseries.one

Besides creating `backend/app/scrapers/ulluwebseries/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=ulluwebseries` or `source=ulluws`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `ulluwebseries.one`, `cdn.ulluwebseries.one`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="ulluwebseries"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Ullu Web Series verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://ulluwebseries.one/hot-series/boss-malayalam-uncut-web-series-boomex-2025/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://ulluwebseries.one/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://ulluwebseries.one/series_category/ullu/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=ulluwebseries"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://ulluwebseries.one/hot-series/tu-haan-kar-ya-naa-kar-ullu-web-series-e06/"
```

## DesiThotHub (desithothub.com) Implementation Notes

[DesiThotHub](https://desithothub.com/) is a custom WordPress-style site for desi live/cam and MMS-style videos. Posts use a single root-level slug (`/{slug}/`). Listing uses `div.thumb` cards with `a.card`, `h2.card-title`, `img`, and `span.time-ago`. Playback uses a **server dropdown** (`button.srv-drop-item`) with one `div.video-unit` per host — only **embed** streams are returned (no direct `.mp4` extraction).

### Host aliases

- `desithothub.com`
- `www.desithothub.com`

### Listing and pagination (`list_videos`)

- Home (newest): `https://desithothub.com/`
- Feeds: `/popular/`, `/favourites/`
- Taxonomy: `/categories/`, `/categories/{slug}/` (e.g. `/categories/tamil/`, `/categories/mallu/`)
- Parse `div.thumb` → `a.card`; title `h2.card-title`; thumb `img`; `span.time-ago`
- Pagination: WordPress `/page/{n}/` (e.g. `https://desithothub.com/page/2/`)

### Metadata and streams (`scrape`)

- Canonical post URL: `https://desithothub.com/{slug}/` (single hyphenated slug segment)
- Reject reserved paths: `categories`, `popular`, `newest`, `tags`, `favourites`, `page`, etc.
- Streams: parse `button.srv-drop-item` labels paired with `div.video-unit` entries
  - Sendvid: `iframe.vid-max-iframe` `src` (e.g. `https://sendvid.com/embed/{id}`)
  - Other hosts: `a.vid-maxwrap[href]` watch URLs converted to embed where possible (`streamtape.com/v/…` → `/e/…`, `lulustream.com/…` → `/e/…`, `vinovo.to/d/…` → `/embed/…`, etc.); GoFile/VikingFile/Upfiles use page URL with `format: embed`
- All stream entries use `format: "embed"` only — do not regex-extract direct MP4 links from HTML
- Default stream prefers Sendvid embed
- Title/thumb from `og:title`, `og:image`, `h1`/`h2`

### Categories (`get_categories`)

Newest, Popular, Categories index, Favourites, plus popular tags (Tamil, Mallu, Bengali, Big Boobs, etc.) in `categories.json`.

### Registration checklist for DesiThotHub

Besides creating `backend/app/scrapers/desithothub/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=desithothub` or `source=thothub`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `desithothub.com`, `streamtape.com`, `sendvid.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="desithothub"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### DesiThotHub verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://desithothub.com/tamil-madhu-aunty-nude-premium-live-show/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://desithothub.com/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://desithothub.com/categories/tamil/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=desithothub"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://desithothub.com/tamil-madhu-aunty-nude-premium-live-show/"
```

## Eporner (eporner.com) Implementation Notes

[Eporner](https://www.eporner.com/) is a large tube site with mandatory age verification in some regions. Video pages use alphanumeric IDs under `/video-{id}/{slug}/` (legacy `/hd-porn/{id}/{slug}/`). **Embed player URLs** (`/embed/{id}/`, used in iframes) are first-class scrape targets and always expose an `format: "embed"` stream for WebView/iframe playback. Streams are resolved via the site XHR API (hash from page HTML), with fallbacks to the public v2 metadata API and HTML `<source>` / MP4 regex extraction.

### Host aliases

- `eporner.com`
- `www.eporner.com`

### Listing and pagination (`list_videos`)

- Home: `https://www.eporner.com/`
- Feeds: `/recent/`, `/popular/`, `/top-rated/`, `/longest/`, `/4k/`, `/cats/`
- Parse `div.mb` cards → `a[href*="/video-"]` or `/hd-porn/`; title from `.mbtit a`; thumb `img`; duration `.mbtim`; views `.mbvie`
- Pagination: append page number to path (e.g. `/recent/2/`, home page 2 → `/2/`)

### Metadata and streams (`scrape`)

- Canonical watch URL: `https://www.eporner.com/video-{id}/{slug}/`
- Embed player URL: `https://www.eporner.com/embed/{id}/` (iframe `src`, e.g. `https://www.eporner.com/embed/5avQdSA3oMK/`)
- When scraping an embed URL, the response `url` stays on `/embed/{id}/`; direct MP4/HLS are resolved from the embed page or the linked full video page; an embed stream is always included
- **Primary streams:** parse `hash` (32-char hex) from page → `GET /xhr/video/{id}?hash={calc_hash}&device=generic&domain=www.eporner.com&fallback=false` → `sources` dict (MP4 + HLS)
- **calc_hash:** split hash into four 8-char hex chunks, each encoded to base-36 (same as yt-dlp `EpornerIE`)
- **Fallback streams:** `GET /api/v2/video/search/?id={id}&per_page=1&thumbsize=big` → `all_qualities` MP4 URLs on `static.eporner.com`
- **HTML fallback:** `<video><source>` tags and `.mp4` / `.m3u8` regex
- Fetch uses `curl_cffi` impersonation when available (helps with age gate / blocks), then shared `pool.fetch_html`

### Categories (`get_categories`)

Home, Recent, Popular, Top Rated, Longest, 4K, Categories index (`categories.json`).

### Registration checklist for Eporner

Besides creating `backend/app/scrapers/eporner/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=eporner`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `eporner.com`, `static.eporner.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="eporner"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Eporner verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.eporner.com/video-FJsA19J3Y3H/one-of-the-greats/\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.eporner.com/recent/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.eporner.com/recent/2/&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=eporner"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.eporner.com/video-FJsA19J3Y3H/one-of-the-greats/"

curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.eporner.com/embed/5avQdSA3oMK/\"}"
```

## Motherless (motherless.com) Implementation Notes

[Motherless](https://motherless.com/) is a user-upload host. Single videos use a hex media code at the site root (e.g. `https://motherless.com/EE97006`). Category and tag browsing uses `/term/videos/{slug}`; feeds live under `/videos`, `/videos/recent`, etc. MP4 streams are served from `*.motherlessmedia.com` (SD and `-720p` variants).

### Host aliases

- `motherless.com`
- `www.motherless.com`
- `*.motherlessmedia.com` (CDN, for direct MP4 proxying)

### Listing and pagination (`list_videos`)

- Home: `https://motherless.com/`
- Videos hub: `https://motherless.com/videos`
- Feeds: `/videos/recent`, `/videos/favorited`, `/videos/viewed`, `/videos/commented`
- Categories/tags: `https://motherless.com/term/videos/{slug}` (e.g. `amateur`, `milf`)
- Parse `div.thumb-container.video` blocks: `data-codename`, full `href="https://motherless.com/{ID}"`, title in `a.caption.title`, duration in `span.size`, views in `span.hits .value`, uploader in `a.uploader`
- Fallbacks: loose `href=".../{ID}" title="..."` regex and `data-codename="ID"` attributes
- Pagination: `?page=N` query parameter (page 1 omits `page`)

### Metadata and streams (`scrape`)

- Canonical watch URL: `https://motherless.com/{HEX_ID}` (also `https://motherless.com/g/{group}/{HEX_ID}`, `/iframe/{ID}`)
- **Primary streams (signed):** `__fileurl = '...'` and `<video><source src="..." res="720p">` from the watch page (URLs include `validfrom` / `hash` query params)
- **Fallback:** unsigned `cdn{N}-videos.motherlessmedia.com/videos/{ID}.mp4` patterns only when HTML lacks sources
- Metadata: `.media-meta-title h1`, `og:image`, `.media-meta-info span.count` for views, `/m/{user}` uploader, `/term/videos/` tags
- Exclude gallery-only paths matching `G[VIGF]?[A-F0-9]+` (e.g. `/GV338999F`)

Send `Cookie: age_verified=1` on fetch to bypass the age gate when possible.

### Categories (`get_categories`)

Home, Videos, Recent, Favorited, Viewed, Commented, plus popular straight tags (Amateur, Homemade, Teen, MILF, Asian, etc.) in `categories.json`.

### Registration checklist for Motherless

Besides creating `backend/app/scrapers/motherless/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=motherless`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `motherless.com`, `motherlessmedia.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="motherless"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### Motherless verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://motherless.com/EE97006\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://motherless.com/videos/recent&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://motherless.com/term/videos/amateur&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=motherless"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://motherless.com/EE97006"
```

## YouJizz (youjizz.com) Implementation Notes

[YouJizz](https://www.youjizz.com/) is a mobile-oriented tube site. Watch URLs use a numeric ID in the path: `/videos/{slug}-{id}.html` or `/videos/-{id}.html`. Streams are exposed in a `dataEncodings` JSON array on the watch page (signed MP4/HLS CDN URLs on `*.youjizz.com`).

### Host aliases

- `youjizz.com`
- `www.youjizz.com`

### Listing and pagination (`list_videos`)

- Popular: `https://www.youjizz.com/most-popular/1.html`
- Newest: `https://www.youjizz.com/newest-clips/1.html`
- Top: `/top-rated/1.html`, `/top-rated-week/1.html`, `/top-rated-month/1.html` (not `/top-week/` or `/top/`)
- HD: `/highdefinition/1.html` (not `/hd/`)
- Tags: `/categories/{slug}-1.html` (not `/tags/{slug}/1.html`)
- Random: `https://www.youjizz.com/random` (no numbered pages)
- Parse `div.video-thumb[data-videoId]` → `.video-title a`, `span.time`, `.format-views`, `img[data-original]`
- Pagination: `/most-popular/2.html` for feeds; `/categories/milf-2.html` for tags (read `#urlPattern` from page 1 HTML when needed)

### Metadata and streams (`scrape`)

- Watch URL: `https://www.youjizz.com/videos/{slug}-{id}.html`
- Embed URL: `https://www.youjizz.com/videos/embed/{id}`
- **Primary streams:** parse `dataEncodings = [{ "quality", "filename", "name" }, ...]` (balanced-bracket JSON parse)
- Fallback: `<video><source src="...">` and `encodings = [...];` assignment
- Normalize `//cdn…` filenames to `https://`
- Metadata: `og:title`, `og:image`, `og:video:duration`, `meta keywords`, Runtime span, Uploaded By regex

Send `Cookie: age_verified=1` on fetch to bypass the age gate when possible.

### Categories (`get_categories`)

Popular, Newest, Top Week/Month/All, Trending, Random, HD, and sample tags in `categories.json`.

### Registration checklist for YouJizz

Besides creating `backend/app/scrapers/youjizz/`, update all of these:

- `backend/app/scrapers/__init__.py`
- `backend/app/main.py`
  - import list
  - `_scrape_dispatch`
  - `_list_dispatch`
  - `/api/v1/categories` source mapping (`source=youjizz`)
- `backend/app/services/video_streaming.py`
  - scraper selection branch
  - supported-host help text
  - stream quality map host checks for `youjizz.com`
- `backend/app/api/endpoints/explore.py`
  - add `ExploreSourceResponse` entry (`sourceId="youjizz"`)

If request URL validation still uses explicit host allowlists in your branch, also update:

- `backend/app/models/schemas.py`
  - scrape URL allowlist
  - list/base URL allowlist

### YouJizz verification examples

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scrapes \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://www.youjizz.com/videos/busty-redhead-filled-with-cum-77924611.html\"}"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.youjizz.com/most-popular/1.html&page=1&limit=20"

curl "http://127.0.0.1:8000/api/v1/videos?base_url=https://www.youjizz.com/most-popular/1.html&page=2&limit=20"

curl "http://127.0.0.1:8000/api/v1/categories?source=youjizz"

curl "http://127.0.0.1:8000/api/v1/videos/stream?url=https://www.youjizz.com/videos/busty-redhead-filled-with-cum-77924611.html"
```


