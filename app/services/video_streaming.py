"""
Video Streaming Module
Extract and serve video streaming URLs
"""

from fastapi import HTTPException
from typing import Any, Optional
import logging
import re

logger = logging.getLogger(__name__)


def _normalize_quality_label(label: Optional[str]) -> str:
    """Normalize scraper quality labels for matching and API output (720 -> 720p)."""
    if label is None:
        return "unknown"
    text = str(label).strip()
    if not text:
        return "unknown"
    if text.isdigit():
        return f"{text}p"
    return text


def _quality_labels_match(stream_quality: Optional[str], requested: str) -> bool:
    """True when stream quality matches requested quality (720 == 720p, 720p_HD == 720p)."""
    sq = _normalize_quality_label(stream_quality)
    rq = _normalize_quality_label(requested)
    if sq == rq:
        return True
    sq_base = re.sub(r"[_-]?(hd|sd|uhd|4k)$", "", sq, flags=re.IGNORECASE)
    rq_base = re.sub(r"[_-]?(hd|sd|uhd|4k)$", "", rq, flags=re.IGNORECASE)
    return sq_base == rq_base


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
    from app.scrapers import xnxx, xhamster, xvideos, masa49, pornhub, youporn, redtube, beeg, spankbang, fapnut, pornxp, hqporner, xxxparodyhd, pornwex, tube8, pornhat, brazzpw, gosexpod, watcherotic, rule34video, haho, hanime, rouvideo, cg51, oppai, xmoviesforyou, tnaflix, hornysimp, pimpbunny, hentaiser, bollywoodmaal, viralkand, blowjobspro, blackporn24, lesbianporn8, milfporn8, indianporn365, mmsbro, kamababa, desimms2, desiporn, thotsporn, leakedamateurporn, zeenite, uncutmaza, mydesimms, po85, cosxplay, memojav, hohoj, ggjav, porn87, goodav, kanav, missav, jable, tianmei, bindasmood, eporner, porntrex, dotmaal, uncutmasti, zmaal
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
    elif zeenite.can_handle(host):
        scraper_module = zeenite
    elif uncutmaza.can_handle(host):
        scraper_module = uncutmaza
    elif mydesimms.can_handle(host):
        scraper_module = mydesimms
    elif po85.can_handle(host):
        scraper_module = po85
    elif cosxplay.can_handle(host):
        scraper_module = cosxplay
    elif memojav.can_handle(host):
        scraper_module = memojav
    elif hohoj.can_handle(host):
        scraper_module = hohoj
    elif ggjav.can_handle(host):
        scraper_module = ggjav
    elif porn87.can_handle(host):
        scraper_module = porn87
    elif goodav.can_handle(host):
        scraper_module = goodav
    elif kanav.can_handle(host):
        scraper_module = kanav
    elif missav.can_handle(host):
        scraper_module = missav
    elif jable.can_handle(host):
        scraper_module = jable
    elif tianmei.can_handle(host):
        scraper_module = tianmei
    elif bindasmood.can_handle(host):
        scraper_module = bindasmood
    elif eporner.can_handle(host):
        scraper_module = eporner
    elif porntrex.can_handle(host):
        scraper_module = porntrex
    elif dotmaal.can_handle(host):
        scraper_module = dotmaal
    elif uncutmasti.can_handle(host):
        scraper_module = uncutmasti
    elif zmaal.can_handle(host):
        scraper_module = zmaal
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported host: {host}. Supported: xnxx, xhamster, xvideos, masa49 (.org/.com/.cam), pornhub, youporn, redtube, beeg, spankbang, fapnut, pornxp, hqporner, xxxparodyhd, urshort.live (embed), pornwex, tube8, pornhat, brazzpw, gosexpod, watcherotic, rou.video, 51cg/chigua, oppai.stream, xmoviesforyou.com, tnaflix.com, hornysimp.com, pimpbunny.com, hentaiser.app, bollywoodmaal.com, viralkand.com, blowjobs.pro, blackporn24.com, lesbianporn8.net, milfporn8.net, indianporn365.xyz, mmsbro.com, thekamababa.com, desimms2.site, desiporn.one, thotsporn.com, leakedamateurporn.xyz, zeenite.com, uncutmazaa.com (uncutmaza.com/.cc rewrite), mydesimms.watch, 85po.com, cosxplay.com, memojav.com, hohoj.tv, ggjav.com, porn87.com, goodav17.com, kanav.ad, missav.ai, jable.tv, 94mt.cc, bindasmood.com, eporner.com, porntrex.com, dotmaal.com, uncutmasti.com, zmaal.net"
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
    streams = video_data.get("streams", [])
    matching: list[dict[str, Any]] = []
    stream_url: Optional[str] = None
    selected_quality = quality
    selected_stream: Optional[dict[str, Any]] = None

    if quality == "default":
        stream_url = video_data.get("default")
        selected_quality = "default"
        for s in streams:
            if s.get("url") == stream_url:
                selected_stream = s
                selected_quality = _normalize_quality_label(s.get("quality", "default"))
                break
    else:
        matching = [s for s in streams if _quality_labels_match(s.get("quality"), quality)]
        if matching:
            selected_stream = matching[0]
            stream_url = selected_stream.get("url")
            selected_quality = _normalize_quality_label(selected_stream.get("quality"))
        else:
            stream_url = video_data.get("default")
            selected_quality = "default"
            logger.warning(f"Quality {quality} not available, using default")
            for s in streams:
                if s.get("url") == stream_url:
                    selected_stream = s
                    break

    if not selected_stream and stream_url:
        for s in streams:
            if s.get("url") == stream_url:
                selected_stream = s
                break

    if not stream_url:
        raise HTTPException(
            status_code=404,
            detail="No playable stream URL found for this video.",
        )

    fmt = "mp4"
    if selected_stream and selected_stream.get("format"):
        fmt = str(selected_stream["format"])
        if fmt.lower() == "default":
            fmt = "embed"
    elif stream_url and ".m3u8" in stream_url:
        fmt = "hls"
        if selected_quality == "default":
            selected_quality = "adaptive"

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
        "leakedamateurporn.xyz" in parsed_url.netloc.lower() or
        "zeenite.com" in parsed_url.netloc.lower() or
        "uncutmaza.com" in parsed_url.netloc.lower() or
        "uncutmazaa.com" in parsed_url.netloc.lower() or
        "uncutmaza.cc" in parsed_url.netloc.lower() or
        "mydesimms.watch" in parsed_url.netloc.lower() or
        "85po.com" in parsed_url.netloc.lower() or
        "cosxplay.com" in parsed_url.netloc.lower() or
        "memojav.com" in parsed_url.netloc.lower() or
        "hohoj.tv" in parsed_url.netloc.lower() or
        "ggjav.com" in parsed_url.netloc.lower() or
        "ggjav.tv" in parsed_url.netloc.lower() or
        "porn87.com" in parsed_url.netloc.lower() or
        "porn87.tv" in parsed_url.netloc.lower() or
        "goodav17.com" in parsed_url.netloc.lower() or
        "kanav.ad" in parsed_url.netloc.lower() or
        "missav.ai" in parsed_url.netloc.lower() or
        "surrit.com" in parsed_url.netloc.lower() or
        "jable.tv" in parsed_url.netloc.lower() or
        "mushroomtrack.com" in parsed_url.netloc.lower() or
        "assets-cdn.jable.tv" in parsed_url.netloc.lower() or
        "94mt.cc" in parsed_url.netloc.lower() or
        "cdn2020.com" in parsed_url.netloc.lower() or
        "tutu1.space" in parsed_url.netloc.lower() or
        "11yun.xyz" in parsed_url.netloc.lower() or
        "11yun.space" in parsed_url.netloc.lower() or
        "bindasmood.com" in parsed_url.netloc.lower() or
        "ixifile.xyz" in parsed_url.netloc.lower() or
        "eporner.com" in parsed_url.netloc.lower() or
        "static.eporner.com" in parsed_url.netloc.lower() or
        "porntrex.com" in parsed_url.netloc.lower() or
        "cdntrex.com" in parsed_url.netloc.lower() or
        "dotmaal.com" in parsed_url.netloc.lower() or
        "maalcdn.com" in parsed_url.netloc.lower() or
        "video.maalcdn.com" in parsed_url.netloc.lower() or
        "uncutmasti.com" in parsed_url.netloc.lower() or
        "ixifile.xyz" in parsed_url.netloc.lower() or
        "zmaal.net" in parsed_url.netloc.lower()):
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
            or "zeenite.com" in host_l
            or "uncutmaza.com" in host_l
            or "uncutmazaa.com" in host_l
            or "uncutmaza.cc" in host_l
            or "mydesimms.watch" in host_l
            or "85po.com" in host_l
            or "cosxplay.com" in host_l
            or "memojav.com" in host_l
            or "hohoj.tv" in host_l
            or "ggjav.com" in host_l
            or "ggjav.tv" in host_l
            or "porn87.com" in host_l
            or "porn87.tv" in host_l
            or "cdn-1.porn87.com" in host_l
            or "cdn-2.porn87.com" in host_l
            or "cdn-3.porn87.com" in host_l
            or "kanav.ad" in host_l
            or "missav.ai" in host_l
            or "surrit.com" in host_l
            or "jable.tv" in host_l
            or "mushroomtrack.com" in host_l
            or "assets-cdn.jable.tv" in host_l
            or "94mt.cc" in host_l
            or "cdn2020.com" in host_l
            or "tutu1.space" in host_l
            or "11yun.xyz" in host_l
            or "11yun.space" in host_l
            or "bindasmood.com" in host_l
            or "ixifile.xyz" in host_l
            or "eporner.com" in host_l
            or "static.eporner.com" in host_l
            or "porntrex.com" in host_l
            or "cdntrex.com" in host_l
            or "dotmaal.com" in host_l
            or "maalcdn.com" in host_l
            or "video.maalcdn.com" in host_l
            or "uncutmasti.com" in host_l
            or "ixifile.xyz" in host_l
            or "zmaal.net" in host_l
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
            quality_label = _normalize_quality_label(s.get("quality", "unknown"))

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
