from __future__ import annotations

import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import starlette.exceptions
from starlette.exceptions import HTTPException as StarletteHTTPException
import asyncio
import re
# Logging
import logging

# Config
from app.config.settings import settings

# Core Modules
from app.core import cache, cache_cleanup, pool, rate_limit_middleware, rate_limit_cleanup

# Exception handlers
from app.exception_handlers import not_found_handler, internal_error_handler, general_exception_handler

# API Routers
from app.api.endpoints import hls, media, explore, thumbnails, sports, ads, notifications
# We will define new standardized routers here or import them if we moved them.
# For this refactor, we will define them inline or in a new api module. 
# To keep it clean, I will implement the Router structure within main.py for now, 
# ensuring they obey the /api/v1/ prefix.

from fastapi import APIRouter

# Scrapers & Models
from app.scrapers import masa49, xhamster, xnxx, xvideos, pornhub, youporn, redtube, beeg, spankbang, fapnut, pornxp, hqporner, xxxparodyhd, pornwex, tube8, pornhat, brazzpw, gosexpod, watcherotic, rule34video, haho, hanime, rouvideo, cg51, oppai, xmoviesforyou, tnaflix, hornysimp, pimpbunny, hentaiser, bollywoodmaal, viralkand, blowjobspro, blackporn24, lesbianporn8, milfporn8, indianporn365, mmsbro
from app.models.schemas import ScrapeResponse, VideoInfoResponse, ListItem, CategoryItem, ScrapeRequest, ListRequest

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    # Startup: intentionally empty — heavy startup can block first request under a2wsgi/cPanel.
    yield
    # Shutdown: close pooled aiohttp session (avoids "Unclosed client session" on Uvicorn exit).
    await pool.close()
    await asyncio.sleep(0.25)


# Create FastAPI app
app = FastAPI(
    title="AppHub API",
    description="Professional Standard API with Versioning, Plural Naming, and Queue Services",
    version="2.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=_app_lifespan,
)

# Register exception handlers
app.add_exception_handler(404, not_found_handler)
app.add_exception_handler(500, internal_error_handler)
app.add_exception_handler(StarletteHTTPException, general_exception_handler)
app.add_exception_handler(HTTPException, general_exception_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=settings.CORS_ALLOW_METHODS,
    allow_headers=settings.CORS_ALLOW_HEADERS,
)

if settings.ENABLE_GZIP:
    app.add_middleware(GZipMiddleware, minimum_size=settings.GZIP_MIN_SIZE)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def static_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/static/"):
        return response

    cache_control = f"public, max-age={settings.STATIC_CACHE_MAX_AGE}"
    for pattern in settings.STATIC_IMMUTABLE_PATTERNS:
        if re.search(pattern, request.url.path):
            cache_control = f"public, max-age={settings.STATIC_IMMUTABLE_MAX_AGE}, immutable"
            break

    response.headers["Cache-Control"] = cache_control
    response.headers["Vary"] = "Accept-Encoding"
    return response

# app.middleware("http")(rate_limit_middleware)

# ==============================================================================
# API V1 Router
# ==============================================================================
api_v1_router = APIRouter(prefix="/api/v1")

# --- Scraper / Resources Endpoints ---

from pydantic import BaseModel, HttpUrl, field_validator, Field

class ScrapeRequestV1(BaseModel):
    url: HttpUrl

class CrawlRequestV1(BaseModel):
    base_url: HttpUrl
    start_page: int = Field(1, ge=1)
    max_pages: int = Field(5, ge=1, le=20)
    per_page_limit: int = Field(0, ge=0, le=200)
    max_items: int = Field(500, ge=1, le=1000)

# Import loose dispatch functions (re-using existing ones for now)
# Ideally these should be in services/scraper_service.py
async def _scrape_dispatch(url: str, host: str) -> dict[str, object]:
    if xhamster.can_handle(host): return await xhamster.scrape(url)
    if masa49.can_handle(host): return await masa49.scrape(url)
    if xnxx.can_handle(host): return await xnxx.scrape(url)
    if xvideos.can_handle(host): return await xvideos.scrape(url)
    if pornhub.can_handle(host): return await pornhub.scrape(url)
    if youporn.can_handle(host): return await youporn.scrape(url)
    if redtube.can_handle(host): return await redtube.scrape(url)
    if beeg.can_handle(host): return await beeg.scrape(url)
    if spankbang.can_handle(host): return await spankbang.scrape(url)
    if fapnut.can_handle(host): return await fapnut.scrape(url)
    if pornxp.can_handle(host): return await pornxp.scrape(url)
    if hqporner.can_handle(host): return await hqporner.scrape(url)
    if xxxparodyhd.can_handle(host): return await xxxparodyhd.scrape(url)
    if pornwex.can_handle(host): return await pornwex.scrape(url)
    if tube8.can_handle(host): return await tube8.scrape(url)
    if pornhat.can_handle(host): return await pornhat.scrape(url)
    if brazzpw.can_handle(host): return await brazzpw.scrape(url)
    if gosexpod.can_handle(host): return await gosexpod.scrape(url)
    if watcherotic.can_handle(host): return await watcherotic.scrape(url)
    if rule34video.can_handle(host): return await rule34video.scrape(url)
    if haho.can_handle(host): return await haho.scrape(url)
    if hanime.can_handle(host): return await hanime.scrape(url)
    if rouvideo.can_handle(host): return await rouvideo.scrape(url)
    if cg51.can_handle(host): return await cg51.scrape(url)
    if oppai.can_handle(host): return await oppai.scrape(url)
    if xmoviesforyou.can_handle(host): return await xmoviesforyou.scrape(url)
    if tnaflix.can_handle(host): return await tnaflix.scrape(url)
    if hornysimp.can_handle(host): return await hornysimp.scrape(url)
    if pimpbunny.can_handle(host): return await pimpbunny.scrape(url)
    if hentaiser.can_handle(host): return await hentaiser.scrape(url)
    if bollywoodmaal.can_handle(host): return await bollywoodmaal.scrape(url)
    if viralkand.can_handle(host): return await viralkand.scrape(url)
    if blowjobspro.can_handle(host): return await blowjobspro.scrape(url)
    if blackporn24.can_handle(host): return await blackporn24.scrape(url)
    if lesbianporn8.can_handle(host): return await lesbianporn8.scrape(url)
    if milfporn8.can_handle(host): return await milfporn8.scrape(url)
    if indianporn365.can_handle(host): return await indianporn365.scrape(url)
    if mmsbro.can_handle(host): return await mmsbro.scrape(url)
    raise HTTPException(status_code=400, detail="Unsupported host")

async def _list_dispatch(base_url: str, host: str, page: int, limit: int) -> list[dict[str, object]]:
    if xhamster.can_handle(host): return await xhamster.list_videos(base_url=base_url, page=page, limit=limit)
    if masa49.can_handle(host): return await masa49.list_videos(base_url=base_url, page=page, limit=limit)
    if xnxx.can_handle(host): return await xnxx.list_videos(base_url=base_url, page=page, limit=limit)
    if xvideos.can_handle(host): return await xvideos.list_videos(base_url=base_url, page=page, limit=limit)
    if pornhub.can_handle(host): return await pornhub.list_videos(base_url=base_url, page=page, limit=limit)
    if youporn.can_handle(host): return await youporn.list_videos(base_url=base_url, page=page, limit=limit)
    if redtube.can_handle(host): return await redtube.list_videos(base_url=base_url, page=page, limit=limit)
    if beeg.can_handle(host): return await beeg.list_videos(base_url=base_url, page=page, limit=limit)
    if spankbang.can_handle(host): return await spankbang.list_videos(base_url=base_url, page=page, limit=limit)
    if fapnut.can_handle(host): return await fapnut.list_videos(base_url=base_url, page=page, limit=limit)
    if pornxp.can_handle(host): return await pornxp.list_videos(base_url=base_url, page=page, limit=limit)
    if hqporner.can_handle(host): return await hqporner.list_videos(base_url=base_url, page=page, limit=limit)
    if xxxparodyhd.can_handle(host): return await xxxparodyhd.list_videos(base_url=base_url, page=page, limit=limit)
    if pornwex.can_handle(host): return await pornwex.list_videos(base_url=base_url, page=page, limit=limit)
    if tube8.can_handle(host): return await tube8.list_videos(base_url=base_url, page=page, limit=limit)
    if pornhat.can_handle(host): return await pornhat.list_videos(base_url=base_url, page=page, limit=limit)
    if brazzpw.can_handle(host): return await brazzpw.list_videos(base_url=base_url, page=page, limit=limit)
    if gosexpod.can_handle(host): return await gosexpod.list_videos(base_url=base_url, page=page, limit=limit)
    if watcherotic.can_handle(host): return await watcherotic.list_videos(base_url=base_url, page=page, limit=limit)
    if rule34video.can_handle(host): return await rule34video.list_videos(base_url=base_url, page=page, limit=limit)
    if haho.can_handle(host): return await haho.list_videos(base_url=base_url, page=page, limit=limit)
    if hanime.can_handle(host): return await hanime.list_videos(base_url=base_url, page=page, limit=limit)
    if rouvideo.can_handle(host): return await rouvideo.list_videos(base_url=base_url, page=page, limit=limit)
    if cg51.can_handle(host): return await cg51.list_videos(base_url=base_url, page=page, limit=limit)
    if oppai.can_handle(host): return await oppai.list_videos(base_url=base_url, page=page, limit=limit)
    if xmoviesforyou.can_handle(host): return await xmoviesforyou.list_videos(base_url=base_url, page=page, limit=limit)
    if tnaflix.can_handle(host): return await tnaflix.list_videos(base_url=base_url, page=page, limit=limit)
    if hornysimp.can_handle(host): return await hornysimp.list_videos(base_url=base_url, page=page, limit=limit)
    if pimpbunny.can_handle(host): return await pimpbunny.list_videos(base_url=base_url, page=page, limit=limit)
    if hentaiser.can_handle(host): return await hentaiser.list_videos(base_url=base_url, page=page, limit=limit)
    if bollywoodmaal.can_handle(host): return await bollywoodmaal.list_videos(base_url=base_url, page=page, limit=limit)
    if viralkand.can_handle(host): return await viralkand.list_videos(base_url=base_url, page=page, limit=limit)
    if blowjobspro.can_handle(host): return await blowjobspro.list_videos(base_url=base_url, page=page, limit=limit)
    if blackporn24.can_handle(host): return await blackporn24.list_videos(base_url=base_url, page=page, limit=limit)
    if lesbianporn8.can_handle(host): return await lesbianporn8.list_videos(base_url=base_url, page=page, limit=limit)
    if milfporn8.can_handle(host): return await milfporn8.list_videos(base_url=base_url, page=page, limit=limit)
    if indianporn365.can_handle(host): return await indianporn365.list_videos(base_url=base_url, page=page, limit=limit)
    if mmsbro.can_handle(host): return await mmsbro.list_videos(base_url=base_url, page=page, limit=limit)
    raise HTTPException(status_code=400, detail="Unsupported host")

async def _crawl_dispatch(base_url: str, host: str, start_page: int, max_pages: int, per_page_limit: int, max_items: int) -> list[dict[str, object]]:
    if xhamster.can_handle(host):
        return await xhamster.crawl_videos(base_url=base_url, start_page=start_page, max_pages=max_pages, per_page_limit=per_page_limit, max_items=max_items)
    raise HTTPException(status_code=400, detail="Unsupported host")


@api_v1_router.post(
    "/scrapes",
    response_model=ScrapeResponse,
    response_model_exclude_none=True,
    response_model_exclude_defaults=True,
    tags=["Scraping"],
)
async def create_scrape(request: Request, body: ScrapeRequestV1) -> ScrapeResponse:
    """
    Scrape a single video URL.
    Renamed from /scrape to POST /scrapes (create a scrape).
    """
    from app.config.settings import settings
    api_base = settings.BASE_URL or str(request.base_url)
    cache_key = f"scrape:{str(body.url)}"
    cached_result = await cache.get(cache_key)
    if cached_result:
        logging.info(f"⚡ Cache HIT for scrape {body.url}")
        return ScrapeResponse(**cached_result)
    try:
        data = await _scrape_dispatch(str(body.url), body.url.host or "")
        if "thumbnail_url" in data:
            data["thumbnail_url"] = thumbnails.wrap_thumbnail_url(data["thumbnail_url"], api_base)
        await cache.set(cache_key, data, ttl_seconds=7200)  # Cache scrapes for 2 hours
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to fetch url") from e
    return ScrapeResponse(**data)

@api_v1_router.get(
    "/videos",
    response_model=list[ListItem],
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    response_model_exclude_defaults=True,
    tags=["Videos"],
)
async def list_videos(request: Request, base_url: str, page: int = 1, limit: int = 100) -> list[ListItem]:
    """
    List videos from a category/channel URL.
    Renamed from /list to GET /videos.
    """
    if page < 1: page = 1
    if limit < 1: limit = 1
    if limit > 200: limit = 200
    
    # Check cache (v2 optimization)
    cache_key = f"list:{base_url}:p{page}:l{limit}"
    cached_items = await cache.get(cache_key)
    if cached_items:
        logging.info(f"⚡ Cache HIT for list {base_url} page {page}")
        return [ListItem(**it) for it in cached_items]

    host = ""
    try:
        parsed = HttpUrl(base_url)
        host = parsed.host or ""
    except:
        pass 

    try:
        items = await _list_dispatch(base_url, host, page, limit)
        
        if items:
            # Wrap thumbnails in proxy for certain sources (like HQPorner)
            from app.config.settings import settings
            api_base = settings.BASE_URL or str(request.base_url)
            for it in items:
                if "thumbnail_url" in it:
                    it["thumbnail_url"] = thumbnails.wrap_thumbnail_url(it["thumbnail_url"], api_base)
            await cache.set(cache_key, items, ttl_seconds=3600)  # Cache for 1 hour (aggressive)
        
        return [ListItem(**it) for it in items]
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch url: {e}") from e

@api_v1_router.post(
    "/crawls",
    response_model=list[ListItem],
    response_model_exclude_none=True,
    response_model_exclude_defaults=True,
    tags=["Crawling"],
)
async def create_crawl(request: Request, body: CrawlRequestV1) -> list[ListItem]:
    """
    Crawl a site for videos.
    Renamed from /crawl to POST /crawls.
    """
    try:
        items = await _crawl_dispatch(
            str(body.base_url),
            body.base_url.host or "",
            body.start_page,
            body.max_pages,
            body.per_page_limit,
            body.max_items,
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Upstream returned error") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail="Failed to fetch url") from e

    if items:
        # Wrap thumbnails in proxy for certain sources (like HQPorner)
        from app.config.settings import settings
        api_base = settings.BASE_URL or str(request.base_url)
        for it in items:
            if "thumbnail_url" in it:
                it["thumbnail_url"] = thumbnails.wrap_thumbnail_url(it["thumbnail_url"], api_base)

    return [ListItem(**it) for it in items]

# --- Categories ---
# Aggregating categories into a cleaned up endpoint
# GET /api/v1/categories?source=xnxx
@api_v1_router.get(
    "/categories",
    response_model=list[CategoryItem],
    response_model_exclude_none=True,
    response_model_exclude_defaults=True,
    tags=["Categories"],
)
async def get_categories(source: str) -> list[CategoryItem]:
    """
    Get categories for a specific source.
    """
    s = source.lower()
    try:
        if s == "xnxx": return [CategoryItem(**c) for c in xnxx.get_categories()]
        if s == "masa": return [CategoryItem(**c) for c in masa49.get_categories()]
        if s == "xvideos": return [CategoryItem(**c) for c in xvideos.get_categories()]
        if s == "xhamster": return [CategoryItem(**c) for c in xhamster.get_categories()]
        if s == "youporn": return [CategoryItem(**c) for c in youporn.get_categories()]
        if s == "pornhub": return [CategoryItem(**c) for c in pornhub.get_categories()]
        if s == "redtube": return [CategoryItem(**c) for c in redtube.get_categories()]
        if s == "beeg": return [CategoryItem(**c) for c in beeg.get_categories()]
        if s == "spankbang": return [CategoryItem(**c) for c in spankbang.get_categories()]
        if s == "onlyfans" or s == "fapnut": return [CategoryItem(**c) for c in await fapnut.get_categories()]
        if s == "pornxp": return [CategoryItem(**c) for c in pornxp.get_categories()]
        if s == "hqporner": return [CategoryItem(**c) for c in hqporner.get_categories()]
        if s == "xxxparodyhd": return [CategoryItem(**c) for c in xxxparodyhd.get_categories()]
        if s == "pornwex": return [CategoryItem(**c) for c in pornwex.get_categories()]
        if s == "tube8": return [CategoryItem(**c) for c in tube8.get_categories()]
        if s == "pornhat": return [CategoryItem(**c) for c in pornhat.get_categories()]
        if s == "brazzpw": return [CategoryItem(**c) for c in brazzpw.get_categories()]
        if s == "gosexpod": return [CategoryItem(**c) for c in gosexpod.get_categories()]
        if s == "watcherotic": return [CategoryItem(**c) for c in watcherotic.get_categories()]
        if s == "rule34video": return [CategoryItem(**c) for c in rule34video.get_categories()]
        if s == "haho": return [CategoryItem(**c) for c in haho.get_categories()]
        if s == "hanime": return [CategoryItem(**c) for c in hanime.get_categories()]
        if s == "rouvideo": return [CategoryItem(**c) for c in rouvideo.get_categories()]
        if s == "cg51" or s == "51cg": return [CategoryItem(**c) for c in cg51.get_categories()]
        if s == "oppai": return [CategoryItem(**c) for c in oppai.get_categories()]
        if s == "xmoviesforyou" or s == "xmovies": return [CategoryItem(**c) for c in xmoviesforyou.get_categories()]
        if s == "tnaflix": return [CategoryItem(**c) for c in tnaflix.get_categories()]
        if s == "hornysimp": return [CategoryItem(**c) for c in hornysimp.get_categories()]
        if s == "pimpbunny": return [CategoryItem(**c) for c in pimpbunny.get_categories()]
        if s == "hentaiser": return [CategoryItem(**c) for c in hentaiser.get_categories()]
        if s == "bollywoodmaal": return [CategoryItem(**c) for c in bollywoodmaal.get_categories()]
        if s == "viralkand": return [CategoryItem(**c) for c in viralkand.get_categories()]
        if s == "blowjobspro" or s == "blowjobs": return [CategoryItem(**c) for c in blowjobspro.get_categories()]
        if s == "blackporn24" or s == "blackporn": return [CategoryItem(**c) for c in blackporn24.get_categories()]
        if s == "lesbianporn8" or s == "lesbianporn": return [CategoryItem(**c) for c in lesbianporn8.get_categories()]
        if s == "milfporn8" or s == "milf8" or s == "milfporn": return [CategoryItem(**c) for c in milfporn8.get_categories()]
        if s == "indianporn365" or s == "indianporn": return [CategoryItem(**c) for c in indianporn365.get_categories()]
        if s == "mmsbro": return [CategoryItem(**c) for c in mmsbro.get_categories()]
        raise HTTPException(status_code=400, detail="Unknown source")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load categories: {str(e)}")

# --- Video Streaming Info ---
from app.services.video_streaming import get_video_info, get_stream_url

@api_v1_router.get("/videos/info", response_model=VideoInfoResponse, response_model_exclude_none=True, tags=["Streaming"])
async def video_info_endpoint(request: Request, url: str = Query(..., description="Video page URL")):
    from app.config.settings import settings
    api_base = settings.BASE_URL or str(request.base_url)
    try:
        return await get_video_info(url, api_base_url=api_base)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch video info: {str(e)}")

@api_v1_router.get("/videos/stream", response_model_exclude_none=True, tags=["Streaming"])
async def direct_stream_endpoint(
    request: Request,
    url: str = Query(..., description="Video page URL"),
    quality: str = Query("default")
):
    from app.config.settings import settings
    api_base = settings.BASE_URL or str(request.base_url)
    try:
        return await get_stream_url(url, quality, api_base_url=api_base)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch stream URL: {str(e)}")


@api_v1_router.get("/videos/related", response_model=list[ListItem], response_model_exclude_none=True, tags=["Videos"])
async def related_videos_endpoint(request: Request, url: str = Query(..., description="Video page URL")):
    """
    Returns related videos (episodes) for a given video URL.
    """
    from app.core import cache
    
    # Normalize URL to base series URL for better cache sharing across episodes
    import re
    normalized_url = url
    series_id = None
    is_haho = "haho.moe/anime/" in url
    if is_haho:
        base_match = re.search(r"https?://haho\.moe/anime/([^/]+)", url)
        if base_match:
            series_id = base_match.group(1)
            normalized_url = f"https://haho.moe/anime/{series_id}"
            
    # Check cache first (shared key for all episodes of the same series)
    cache_key = f"related_videos:{normalized_url}"
    cached_data = await cache.get(cache_key)
    if cached_data:
        return cached_data

    from app.config.settings import settings
    api_base = settings.BASE_URL or str(request.base_url)
    try:
        related = []
        
        # Fast path: for Haho, call the lightweight get_episode_list directly
        # (1 network fetch vs. 2 in the full get_video_info path)
        if is_haho and series_id:
            from app.scrapers.haho.scraper import get_episode_list
            related = await get_episode_list(series_id)
        else:
            from app.services.video_streaming import get_video_info
            info = await get_video_info(url, api_base_url=api_base)
            related = info.get("related_videos", [])
        
        result = [ListItem(**it).model_dump(exclude_none=True) for it in related]
        
        # Cache the result for 1 hour (3600 seconds) if it's not empty
        if result:
            await cache.set(cache_key, result, ttl_seconds=3600)
            
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch related videos: {str(e)}")


@api_v1_router.get("/videos/download", tags=["Streaming"])
async def video_download_endpoint(request: Request, url: str = Query(..., description="Video page URL")):
    """
    Returns only MP4 download links for a given video URL.
    Filters out HLS/adaptive streams.
    """
    from app.config.settings import settings
    api_base = settings.BASE_URL or str(request.base_url)
    try:
        info = await get_video_info(url, api_base_url=api_base)
        video_data = info.get("video", {})
        streams = video_data.get("streams", [])
        
        # Filter for MP4 only
        mp4_links = []
        for s in streams:
            fmt = s.get("format", "").lower()
            stream_url = s.get("url", "")
            
            # Skip explicit HLS streams or m3u8 playlists, which may contain .mp4 in path
            if fmt == "hls" or ".m3u8" in stream_url.lower():
                continue
                
            if fmt == "mp4" or ".mp4" in stream_url.lower():
                mp4_links.append({
                    "quality": s.get("quality", "unknown"),
                    "url": stream_url,
                    "format": "mp4"
                })
        
        return {
            "status": "success",
            "url": url,
            "title": info.get("title"),
            "downloads": mp4_links
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch download links: {str(e)}")


# include routers
api_v1_router.include_router(explore.router)
api_v1_router.include_router(hls.router, prefix="/hls", tags=["HLS Proxy"])
api_v1_router.include_router(thumbnails.router, prefix="/thumbnails", tags=["Thumbnail Proxy"])
api_v1_router.include_router(media.router)
api_v1_router.include_router(sports.router)
api_v1_router.include_router(ads.router)
api_v1_router.include_router(notifications.router)


# --- AppHub Version ---
import importlib
from app import apphub_version

@app.get("/api/apphub/version", tags=["System"])
async def get_apphub_version():
    importlib.reload(apphub_version)
    return {
        "version": apphub_version.VERSION,
        "buildNumber": apphub_version.BUILD_NUMBER,
        "minSupportedBuild": getattr(apphub_version, "MIN_SUPPORTED_BUILD", 1),
        "releaseDate": getattr(apphub_version, "RELEASE_DATE", ""),
        "downloadUrl": apphub_version.DOWNLOAD_URL,
        "downloadUrls": getattr(apphub_version, "DOWNLOAD_URLS", {}),
        "apkHash": getattr(apphub_version, "APK_HASH", ""),
        "changelog": apphub_version.CHANGELOG.strip(),
        "changelogTitle": apphub_version.CHANGELOG_TITLE,
        "isMandatory": apphub_version.IS_MANDATORY,
        "sizeBytes": apphub_version.SIZE_BYTES,
        "downloadSizes": getattr(apphub_version, "DOWNLOAD_SIZES", {}),
        "telegramChannel": getattr(apphub_version, "TELEGRAM_CHANNEL", ""),
    }


# Include Main V1 Router
app.include_router(api_v1_router)


@app.get("/health", tags=["System"])
async def health() -> dict[str, str]:
    logging.info("Health route CALLED successfully")
    return {"status": "ok", "mode": "safe"}

