import httpx
from fastapi import APIRouter, HTTPException, Query, Response, Request
from fastapi.responses import StreamingResponse
from urllib.parse import urljoin, quote
import logging
import re

router = APIRouter()
logger = logging.getLogger(__name__)

# Pattern to find URLs in m3u8 files
URL_PATTERN = re.compile(r'(https?://[^\s]+)')

@router.get("/proxy", summary="HLS Proxy")
async def hls_proxy(
    url: str = Query(..., description="Target HLS URL"),
    referer: str = Query(None, description="Referer header to send"),
    origin: str = Query(None, description="Origin header to send"),
    user_agent: str = Query(None, description="User-Agent header to send"),
    request: Request = None
):
    """
    Proxy HLS manifests and segments to bypass CORS/Referer restrictions.
    Rewrites URLs in m3u8 files to point back to this proxy.
    Streams video chunks efficiently without memory buffering.
    Handles BrazzPW-style meta-refreshes and masked MIME types.
    """
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")
    
    headers = {}
    ua = user_agent if user_agent else request.headers.get("user-agent", "Mozilla/5.0")
    if ua:
        headers["User-Agent"] = ua
    if referer:
        headers["Referer"] = referer
    if origin:
        headers["Origin"] = origin
    
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header
        
    try:
        from starlette.background import BackgroundTask
        
        client = httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=15.0)
        req = client.build_request("GET", url)
        resp = await client.send(req, stream=True)
        
        # 1. Handle Meta-Refresh or session initialization (common in BrazzPW manifests)
        content_type = resp.headers.get("content-type", "").lower()
        is_html = "text/html" in content_type
        
        url_lower = url.lower()
        is_manifest = "mpegurl" in content_type or url_lower.endswith(".m3u8") or ".m3u8" in url_lower

        if (resp.status_code == 403 or is_html) and is_manifest:
            await resp.aread() # We must read the body to check for meta-refresh
            if "#EXTM3U" not in resp.text:
                m = re.search(r'url=([^"\']*)', resp.text, re.I)
                if m:
                    refresh_url = urljoin(url, m.group(1))
                    logger.info(f"Following meta-refresh to: {refresh_url}")
                    await client.get(refresh_url) # Hit to get cookies
                    resp = await client.send(req, stream=True) # Retry original stream
                else:
                    logger.info("Retrying request to handle potential session initialization...")
                    resp = await client.send(req, stream=True)
            
            # Re-evaluate content type after refresh
            content_type = resp.headers.get("content-type", "").lower()
            is_manifest = "mpegurl" in content_type or url_lower.endswith(".m3u8") or ".m3u8" in url_lower

        if resp.status_code >= 400:
            await resp.aread()
            await client.aclose()
            raise HTTPException(status_code=resp.status_code, detail=f"Upstream error: {resp.status_code}")
        
        # 2. Manifest Rewriting
        if is_manifest:
            await resp.aread() # Read full manifest into memory
            content = resp.text
            await client.aclose() # Close immediately as we are done
            
            base_url = str(request.base_url).rstrip("/")
            proxy_base = f"{base_url}/api/v1/hls/proxy"
            
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    if line.startswith("#EXT-X-KEY") and 'URI="' in line:
                        # Find URI attribute and rewrite it
                        match = re.search(r'URI="([^"]+)"', line)
                        if match:
                            target = urljoin(url, match.group(1))
                            params = f"?url={quote(target)}"
                            if referer: params += f"&referer={quote(referer)}"
                            if origin: params += f"&origin={quote(origin)}"
                            if user_agent: params += f"&user_agent={quote(user_agent)}"
                            proxy_url = f"{proxy_base}{params}"
                            line = line.replace(f'URI="{match.group(1)}"', f'URI="{proxy_url}"')
                    new_lines.append(line)
                else:
                    # It's a URI line
                    target = urljoin(url, line)
                    params = f"?url={quote(target)}"
                    if referer: params += f"&referer={quote(referer)}"
                    if origin: params += f"&origin={quote(origin)}"
                    if user_agent: params += f"&user_agent={quote(user_agent)}"
                    new_lines.append(f"{proxy_base}{params}")
            
            return Response(
                content="\n".join(new_lines),
                media_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*"}
            )
        
        # 3. Stream Segment without buffering
        else:
            response_headers = {"Access-Control-Allow-Origin": "*"}
            for h in ["Content-Range", "Content-Length", "Accept-Ranges"]:
                if h.lower() in resp.headers:
                    response_headers[h] = resp.headers[h.lower()]
            
            final_media_type = content_type
            if "brazzpw.com" in url and "image/" in content_type:
                final_media_type = "video/mp2t"

            return StreamingResponse(
                resp.aiter_bytes(),
                status_code=resp.status_code,
                media_type=final_media_type,
                headers=response_headers,
                background=BackgroundTask(client.aclose)
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"HLS Proxy error: {e}")
        try:
            await client.aclose()
        except:
            pass
        raise HTTPException(status_code=500, detail=str(e))

