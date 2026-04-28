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

## Blowjobs.pro Implementation Notes

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
