"""
HTTP Connection Pooling for Scrapers
Reuse connections for 50-100ms performance boost
"""

import asyncio
import random
import aiohttp
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Realistic browser User-Agent pool for rotation
USER_AGENTS = [
    # Chrome Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    # Chrome Mac
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    # Firefox Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
    # Firefox Mac
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:123.0) Gecko/20100101 Firefox/123.0',
    # Safari Mac
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    # Edge Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0',
    # Chrome Linux
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    # Chrome Android
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.90 Mobile Safari/537.36',
]

def get_random_user_agent() -> str:
    """Return a random User-Agent string from the pool."""
    return random.choice(USER_AGENTS)


class ConnectionPool:
    """Singleton connection pool for all HTTP requests"""
    
    _instance: Optional['ConnectionPool'] = None
    _session: Optional[aiohttp.ClientSession] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def get_session(self) -> aiohttp.ClientSession:
        """
        Get or create aiohttp session with connection pooling
        
        Returns:
            Configured ClientSession
        """
        current_loop = asyncio.get_running_loop()
        
        # Create a new session if:
        # 1. We don't have one
        # 2. It is closed
        # 3. We are running in a DIFFERENT event loop (fixes 502s with our custom ASGI bridge)
        if (
            getattr(self, '_session', None) is None 
            or self._session.closed 
            or getattr(self, '_loop', None) is not current_loop
        ):
            old_session = self._session
            old_loop = getattr(self, '_loop', None)
            if (
                old_session is not None
                and not old_session.closed
                and old_loop is current_loop
            ):
                await old_session.close()
            # If the event loop changed, the previous session belonged to another loop;
            # do not await close() here (aiohttp requires same-loop close).

            # Connection pooling configuration
            connector = aiohttp.TCPConnector(
                limit=100,  # Max 100 concurrent connections total
                limit_per_host=10,  # Max 10 per host
                ttl_dns_cache=300,  # Cache DNS for 5 minutes
                enable_cleanup_closed=True,
                force_close=False  # Keep connections alive
            )
            
            # Connection timeout configuration
            timeout = aiohttp.ClientTimeout(
                total=30,  # Total timeout
                connect=10,  # Connection timeout
                sock_read=20  # Read timeout
            )
            
            # Create session (no static User-Agent — rotated per request)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate',  # Removed 'br' because python brotli is missing
                    'DNT': '1',
                }
            )
            
            # Save the loop reference to avoid recreating on the same request
            self._loop = current_loop
            logger.info("Created HTTP connection pool with 100 connections (loop updated)")
        
        return self._session

    
    async def close(self):
        """Close the session and cleanup connections"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("Closed HTTP connection pool")
        self._session = None
        self._loop = None


# Global pool instance
pool = ConnectionPool()


async def fetch_html(url: str, retries: int = 3, **kwargs) -> str:
    """
    Fetch HTML using connection pool with rotating User-Agent and exponential backoff.
    Retries on 429 (rate limited), 403 (blocked), or 5xx errors.
    """
    session = await pool.get_session()
    headers = kwargs.pop('headers', {})
    headers.setdefault('User-Agent', get_random_user_agent())

    last_error = None
    for attempt in range(retries):
        try:
            async with session.get(url, headers=headers, **kwargs) as response:
                if response.status in (429, 403, 503):
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(f"Blocked ({response.status}) on {url}, retrying in {wait}s (attempt {attempt+1}/{retries})")
                    await asyncio.sleep(wait)
                    headers['User-Agent'] = get_random_user_agent()  # Rotate on retry
                    continue
                response.raise_for_status()
                return await response.text()
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                wait = 2 ** attempt
                logger.warning(f"Fetch error on {url}: {e}, retrying in {wait}s")
                await asyncio.sleep(wait)
                headers['User-Agent'] = get_random_user_agent()

    raise last_error or Exception(f"Failed to fetch {url} after {retries} retries")


async def fetch_json(url: str, retries: int = 3, **kwargs) -> dict:
    """
    Fetch JSON using connection pool with rotating User-Agent and exponential backoff.
    Retries on 429 (rate limited), 403 (blocked), or 5xx errors.
    """
    session = await pool.get_session()
    headers = kwargs.pop('headers', {})
    headers.setdefault('User-Agent', get_random_user_agent())

    last_error = None
    for attempt in range(retries):
        try:
            async with session.get(url, headers=headers, **kwargs) as response:
                if response.status in (429, 403, 503):
                    wait = 2 ** attempt
                    logger.warning(f"Blocked ({response.status}) on {url}, retrying in {wait}s (attempt {attempt+1}/{retries})")
                    await asyncio.sleep(wait)
                    headers['User-Agent'] = get_random_user_agent()
                    continue
                response.raise_for_status()
                return await response.json()
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                wait = 2 ** attempt
                logger.warning(f"Fetch error on {url}: {e}, retrying in {wait}s")
                await asyncio.sleep(wait)
                headers['User-Agent'] = get_random_user_agent()

    raise last_error or Exception(f"Failed to fetch {url} after {retries} retries")


async def post_json(url: str, data: dict, **kwargs) -> dict:
    """
    POST JSON using connection pool
    
    Args:
        url: URL to post to
        data: JSON data to send
        **kwargs: Additional arguments for session.post()
        
    Returns:
        JSON response
    """
    session = await pool.get_session()
    
    async with session.post(url, json=data, **kwargs) as response:
        response.raise_for_status()
        return await response.json()