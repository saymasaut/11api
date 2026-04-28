"""
Video Streaming Module
Extract and serve video streaming URLs
"""

from fastapi import HTTPException
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)


async def get_video_info(url: str, api_base_url: str = "http://localhost:8000") -> dict:
    """
    Get video streaming information for a given URL
    
    Args:
        url: Video page URL (e.g., https://xnxx.com/video-123)
        api_base_url: Base URL of the API for proxy links (e.g., https://my-api.com)
        
    Returns:
        {
            ...
        }
    """
    # Import here to avoid circular dependency
    from app.scrapers import xnxx, xhamster, xvideos, masa49, pornhub, youporn, redtube, beeg, spankbang, fapnut, pornxp, hqporner, xxxparodyhd, pornwex, tube8, pornhat, brazzpw, gosexpod, watcherotic, rule34video, haho, hanime, rouvideo, cg51, oppai, xmoviesforyou, tnaflix, hornysimp, pimpbunny, hentaiser, bollywoodmaal, viralkand, blowjobspro, blackporn24, lesbianporn8, milfporn8, indianporn365, mmsbro, kamababa, desimms2, desiporn, thotsporn, leakedamateurporn
    from app.api.endpoints import thumbnails
    from urllib.parse import urlparse
    
    # Parse URL to get host
    parsed = urlparse(url)
    host = parsed.netloc
    
    logger.info(f"Getting video info for: {url}")
    
    # Determine which scraper to use
    scraper_module = None
    if xnxx.can_handle(host):
        scraper_module = xnxx
    elif xhamster.can_handle(host):
        scraper_module = xhamster
    elif xvideos.can_handle(host):
        scraper_module = xvideos
    elif masa49.can_handle(host):
        scraper_module = masa49
    elif pornhub.can_handle(host):
        scraper_module = pornhub
    elif youporn.can_handle(host):
        scraper_module = youporn
    elif redtube.can_handle(host):
        scraper_module = redtube
    elif beeg.can_handle(host):
        scraper_module = beeg
    elif spankbang.can_handle(host):
        scraper_module = spankbang
    elif fapnut.can_handle(host):
        scraper_module = fapnut
    elif pornxp.can_handle(host):
        scraper_module = pornxp
    elif hqporner.can_handle(host):
        scraper_module = hqporner
    elif xxxparodyhd.can_handle(host):
        scraper_module = xxxparodyhd
    elif pornwex.can_handle(host):
        scraper_module = pornwex
    elif tube8.can_handle(host):
        scraper_module = tube8
    elif pornhat.can_handle(host):
        scraper_module = pornhat
    elif brazzpw.can_handle(host):
        scraper_module = brazzpw
    elif gosexpod.can_handle(host):
        scraper_module = gosexpod
    elif watcherotic.can_handle(host):
        scraper_module = watcherotic
    elif rule34video.can_handle(host):
        scraper_module = rule34video
    elif haho.can_handle(host):
        scraper_module = haho
    elif hanime.can_handle(host):
        scraper_module = hanime
    elif rouvideo.can_handle(host):
        scraper_module = rouvideo
    elif cg51.can_handle(host):
        scraper_module = cg51
    elif oppai.can_handle(host):
        scraper_module = oppai
    elif xmoviesforyou.can_handle(host):
        scraper_module = xmoviesforyou
    elif tnaflix.can_handle(host):
        scraper_module = tnaflix
    elif hornysimp.can_handle(host):
        scraper_module = hornysimp
    elif pimpbunny.can_handle(host):
        scraper_module = pimpbunny
    elif hentaiser.can_handle(host):
        scraper_module = hentaiser
    elif bollywoodmaal.can_handle(host):
        scraper_module = bollywoodmaal
    elif viralkand.can_handle(host):
        scraper_module = viralkand
    elif blowjobspro.can_handle(host):
        scraper_module = blowjobspro
    elif blackporn24.can_handle(host):
        scraper_module = blackporn24
    elif lesbianporn8.can_handle(host):
        scraper_module = lesbianporn8
    elif milfporn8.can_handle(host):
        scraper_module = milfporn8
    elif indianporn365.can_handle(host):
        scraper_module = indianporn365
    elif mmsbro.can_handle(host):
        scraper_module = mmsbro
    elif kamababa.can_handle(host):
        scraper_module = kamababa
    elif desimms2.can_handle(host):
        scraper_module = desimms2
    elif desiporn.can_handle(host):
        scraper_module = desiporn
    elif thotsporn.can_handle(host):
        scraper_module = thotsporn
    elif leakedamateurporn.can_handle(host):
        scraper_module = leakedamateurporn
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported host: {host}. Supported: xnxx, xhamster, xvideos, masa49 (.org/.com/.cam), pornhub, youporn, redtube, beeg, spankbang, fapnut, pornxp, hqporner, xxxparodyhd, urshort.live (embed), pornwex, tube8, pornhat, brazzpw, gosexpod, watcherotic, rou.video, 51cg/chigua, oppai.stream, xmoviesforyou.com, tnaflix.com, hornysimp.com, pimpbunny.com, hentaiser.app, bollywoodmaal.com, viralkand.com, blowjobs.pro, blackporn24.com, lesbianporn8.net, milfporn8.net, indianporn365.xyz, mmsbro.com, thekamababa.com, desimms2.site, desiporn.one, thotsporn.com, leakedamateurporn.xyz"
        )
    
    try:
        # Scrape the page (now includes video URLs)
        metadata = await scraper_module.scrape(url)
    except Exception as e:
        logger.error(f"Failed to scrape video info: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to extract video info: {str(e)}"
        )
    
    # Check if video URLs were extracted
    video_data = metadata.get("video", {})
    if not video_data.get("has_video"):
        raise HTTPException(
            status_code=404,
            detail="No video streams found for this URL. Video may be premium or removed."
        )
    
    # Build response with consistent field order
    # For SpankBang, exclude metadata fields as they're not reliably extracted
    if scraper_module == spankbang:
        # SpankBang: minimal metadata
        response = {
            "url": url,
            "tags": metadata.get("tags", []),
            "related_videos": metadata.get("related_videos", []),
            "video": video_data,
            "playable": True,
        }
    else:
        # All other sources: full metadata
        thumbnail_url = metadata.get("thumbnail_url")
        if thumbnail_url:
            thumbnail_url = thumbnails.wrap_thumbnail_url(thumbnail_url, api_base_url)
            
        response = {
            "url": url,
            "title": metadata.get("title"),
            "description": metadata.get("description"),
            "thumbnail_url": thumbnail_url,
            "duration": metadata.get("duration"),
            "views": metadata.get("views"),
            "uploader_name": metadata.get("uploader_name"),
            "category": metadata.get("category"),
            "tags": metadata.get("tags", []),
            "upload_date": metadata.get("upload_date"),
            "related_videos": metadata.get("related_videos", []),
            "preview_url": metadata.get("preview_url"),
            "video": video_data,
            "playable": True,
        }
    
    return response


async def get_stream_url(url: str, quality: str = "default", api_base_url: str = "http://localhost:8000") -> dict:
    """
    Get direct stream URL for a specific quality
    
    Args:
        url: Video page URL
        quality: Desired quality (1080p, 720p, 480p, or "default")
        api_base_url: Base URL for proxy links
        
    Returns:
        {"stream_url": "https://...mp4", "quality": "1080p", "format": "mp4"}
    """
    # Note: get_video_info is async, so this needs to be awaited if called directly.
    # But usually this is called by endpoint which calls get_video_info first.
    # Refactoring: we'll just call get_video_info here too.
    # Using default localhost for this low-level helper as it returns raw data
    info = await get_video_info(url, api_base_url=api_base_url) 
    video_data = info["video"]
    
    if quality == "default":
        stream_url = video_data["default"]
        selected_quality = "default"
        
        # Try to find the quality metadata of the default stream
        streams = video_data.get("streams", [])
        for s in streams:
            if s.get("url") == stream_url:
                found_quality = s.get("quality", "default")
                # Normalize quality: add 'p' suffix if missing
                if found_quality and found_quality.isdigit():
                    selected_quality = f"{found_quality}p"
                else:
                    selected_quality = found_quality
                break
    else:
        # Find matching quality
        streams = video_data["streams"]
        matching = [s for s in streams if s["quality"] == quality]
        
        if matching:
            stream_url = matching[0]["url"]
            selected_quality = matching[0]["quality"]
        else:
            # Fallback to default
            stream_url = video_data["default"]
            selected_quality = "default"
            logger.warning(f"Quality {quality} not available, using default")
    
    # Determine format and refine quality label
    # First, check if the scraper provided a specific format
    fmt = "mp4"
    selected_stream = matching[0] if (quality != "default" and matching) else None
    if not selected_stream and quality == "default":
        # Try to find the stream object for the default URL
        streams = video_data.get("streams", [])
        for s in streams:
            if s.get("url") == stream_url:
                selected_stream = s
                break
    
    if selected_stream and selected_stream.get("format"):
        fmt = selected_stream["format"]
        # Preferred mirror is tagged "default" in some scrapers; playback is still embed/WebView.
        if str(fmt).lower() == "default":
            fmt = "embed"
    elif ".m3u8" in stream_url:
        fmt = "hls"
        if selected_quality == "default":
            selected_quality = "adaptive"

    # Build base response
    response = {
        "stream_url": stream_url,
        "quality": selected_quality,
        "format": fmt,
    }
    
    # Add available_qualities for Pornhub, YouPorn, and RedTube
    from urllib.parse import urlparse
    parsed_url = urlparse(url)
    if ("pornhub.com" in parsed_url.netloc.lower() or 
        "youporn.com" in parsed_url.netloc.lower() or
        "redtube.com" in parsed_url.netloc.lower() or
        "redtube.net" in parsed_url.netloc.lower() or
        "tube8.com" in parsed_url.netloc.lower() or
        "xxxparodyhd.net" in parsed_url.netloc.lower() or
        "xparody.com" in parsed_url.netloc.lower() or 
        "pornhat.com" in parsed_url.netloc.lower() or
        "oppai.stream" in parsed_url.netloc.lower() or
        "xmoviesforyou.com" in parsed_url.netloc.lower() or
        "tnaflix.com" in parsed_url.netloc.lower() or
        "hornysimp.com" in parsed_url.netloc.lower() or
        "pimpbunny.com" in parsed_url.netloc.lower() or
        "hentaiser.app" in parsed_url.netloc.lower() or
        "hentaiser.com" in parsed_url.netloc.lower() or
        "bollywoodmaal.com" in parsed_url.netloc.lower() or
        "viralkand.com" in parsed_url.netloc.lower() or
        "blowjobs.pro" in parsed_url.netloc.lower() or
        "blackporn24.com" in parsed_url.netloc.lower() or
        "lesbianporn8.net" in parsed_url.netloc.lower() or
        "milfporn8.net" in parsed_url.netloc.lower() or
        "indianporn365.xyz" in parsed_url.netloc.lower() or
        "mmsbro.com" in parsed_url.netloc.lower() or
        "thekamababa.com" in parsed_url.netloc.lower() or
        "desimms2.site" in parsed_url.netloc.lower() or
        "desiporn.one" in parsed_url.netloc.lower() or
        "thotsporn.com" in parsed_url.netloc.lower() or
        "leakedamateurporn.xyz" in parsed_url.netloc.lower()):
        qualities: dict[str, Any] = {}
        all_streams = video_data.get("streams", [])
        host_l = parsed_url.netloc.lower()
        per_stream_format_keys = (
            "xmoviesforyou.com" in host_l
            or "xxxparodyhd.net" in host_l
            or "hornysimp.com" in host_l
            or "pimpbunny.com" in host_l
            or "bollywoodmaal.com" in host_l
            or "viralkand.com" in host_l
            or "blowjobs.pro" in host_l
            or "blackporn24.com" in host_l
            or "lesbianporn8.net" in host_l
            or "milfporn8.net" in host_l
            or "indianporn365.xyz" in host_l
            or "mmsbro.com" in host_l
            or "thekamababa.com" in host_l
            or "desimms2.site" in host_l
            or "desiporn.one" in host_l
            or "thotsporn.com" in host_l
            or "leakedamateurporn.xyz" in host_l
        )
        
        # Debug logging for RedTube
        if "redtube.com" in parsed_url.netloc.lower():
            logger.info(f"RedTube: Found {len(all_streams)} total streams")
            for idx, s in enumerate(all_streams):
                logger.info(f"  Stream {idx}: format={s.get('format')}, quality={s.get('quality')}, url={s.get('url')[:60]}...")
        
        for s in all_streams:
            # For Tube8, we exclusively want to serve HLS streams in the stream endpoint to support all qualities
            if "tube8.com" in parsed_url.netloc.lower() and s.get("format", "").lower() == "mp4":
                continue

            # Include both HLS and MP4 for these sites to support both streaming and download options
            # Also include 'embed' format for sites like xxxparodyhd
            quality_label = s.get("quality", "unknown")
            
            # Normalize quality label: ensure it has 'p' suffix (e.g., "720" -> "720p")
            if quality_label and str(quality_label).isdigit():
                quality_label = f"{quality_label}p"
                
            qualities[quality_label] = s.get("url")
            if per_stream_format_keys:
                sf = s.get("format")
                if sf is not None and str(sf).strip():
                    if str(sf).lower() == "default":
                        sf = "embed"
                    qualities[f"{quality_label}_format"] = sf
        
        if "redtube.com" in parsed_url.netloc.lower():
            logger.info(f"RedTube: Found {len(qualities)} HLS quality streams")
        
        # Add qualities as flat fields in response
        for quality_label, quality_url in qualities.items():
            response[quality_label] = quality_url
            
    return response
