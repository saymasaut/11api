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

_RETRYABLE_STATUSES = frozenset({429, 403, 503})


def get_random_user_agent() -> str:
    """Return a random User-Agent string from the pool."""
    return random.choice(USER_AGENTS)


def _rotate_user_agent(headers: dict) -> None:
    headers["User-Agent"] = get_random_user_agent()


def _log_fetch_failure(
    *,
    url: str,
    retries: int,
    quiet: bool,
    blocked_status: int | None,
    error: Exception | None,
) -> None:
    if blocked_status is not None:
        msg = f"Blocked ({blocked_status}) on {url} after {retries} attempt(s)"
        if quiet:
            logger.debug(msg)
        else:
            logger.warning(msg)
        return
    if error is None:
        return
    msg = f"Fetch failed on {url} after {retries} attempt(s): {error}"
    if quiet:
        logger.debug(msg)
    else:
        logger.warning(msg)


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

            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=10,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                force_close=False,
            )

            timeout = aiohttp.ClientTimeout(
                total=30,
                connect=10,
                sock_read=20,
            )

            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate',
                    'DNT': '1',
                },
            )

            self._loop = current_loop
            logger.debug("Created HTTP connection pool (loop updated)")

        return self._session

    async def close(self):
        """Close the session and cleanup connections"""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("Closed HTTP connection pool")
        self._session = None
        self._loop = None


# Global pool instance
pool = ConnectionPool()


async def fetch_html(
    url: str,
    retries: int = 3,
    *,
    quiet: bool = False,
    retry_statuses: frozenset[int] | None = None,
    **kwargs,
) -> str:
    """
    Fetch HTML using connection pool with rotating User-Agent and exponential backoff.

    Retries on 429 (rate limited), 403 (blocked), or 503 by default.
    Intermediate retries are logged at DEBUG; the final failure uses WARNING unless quiet=True.
    """
    retry_statuses = retry_statuses or _RETRYABLE_STATUSES
    retries = max(1, int(retries))
    session = await pool.get_session()
    headers = kwargs.pop('headers', {})
    headers.setdefault('User-Agent', get_random_user_agent())

    last_error: Exception | None = None
    last_blocked_status: int | None = None

    for attempt in range(retries):
        try:
            async with session.get(url, headers=headers, **kwargs) as response:
                if response.status in retry_statuses:
                    last_blocked_status = response.status
                    last_error = aiohttp.ClientResponseError(
                        response.request_info,
                        response.history,
                        status=response.status,
                        message=response.reason or str(response.status),
                    )
                    if attempt < retries - 1:
                        wait = 2 ** attempt
                        logger.debug(
                            "Blocked (%s) on %s, retrying in %ss (%s/%s)",
                            response.status,
                            url,
                            wait,
                            attempt + 1,
                            retries,
                        )
                        await asyncio.sleep(wait)
                        _rotate_user_agent(headers)
                        continue
                    break

                response.raise_for_status()
                return await response.text()
        except aiohttp.ClientResponseError as e:
            last_error = e
            if e.status in retry_statuses:
                last_blocked_status = e.status
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.debug(
                        "Blocked (%s) on %s, retrying in %ss (%s/%s)",
                        e.status,
                        url,
                        wait,
                        attempt + 1,
                        retries,
                    )
                    await asyncio.sleep(wait)
                    _rotate_user_agent(headers)
                    continue
                break
            if attempt < retries - 1:
                wait = 2 ** attempt
                logger.debug(
                    "Fetch error on %s: %s, retrying in %ss (%s/%s)",
                    url,
                    e,
                    wait,
                    attempt + 1,
                    retries,
                )
                await asyncio.sleep(wait)
                _rotate_user_agent(headers)
                continue
            break
        except Exception as e:
            last_error = e
            last_blocked_status = None
            if attempt < retries - 1:
                wait = 2 ** attempt
                logger.debug(
                    "Fetch error on %s: %s, retrying in %ss (%s/%s)",
                    url,
                    e,
                    wait,
                    attempt + 1,
                    retries,
                )
                await asyncio.sleep(wait)
                _rotate_user_agent(headers)
                continue
            break

    _log_fetch_failure(
        url=url,
        retries=retries,
        quiet=quiet,
        blocked_status=last_blocked_status,
        error=last_error,
    )
    raise last_error or Exception(f"Failed to fetch {url} after {retries} retries")


async def fetch_json(
    url: str,
    retries: int = 3,
    *,
    quiet: bool = False,
    retry_statuses: frozenset[int] | None = None,
    **kwargs,
) -> dict:
    """
    Fetch JSON using connection pool with rotating User-Agent and exponential backoff.
    """
    retry_statuses = retry_statuses or _RETRYABLE_STATUSES
    retries = max(1, int(retries))
    session = await pool.get_session()
    headers = kwargs.pop('headers', {})
    headers.setdefault('User-Agent', get_random_user_agent())

    last_error: Exception | None = None
    last_blocked_status: int | None = None

    for attempt in range(retries):
        try:
            async with session.get(url, headers=headers, **kwargs) as response:
                if response.status in retry_statuses:
                    last_blocked_status = response.status
                    last_error = aiohttp.ClientResponseError(
                        response.request_info,
                        response.history,
                        status=response.status,
                        message=response.reason or str(response.status),
                    )
                    if attempt < retries - 1:
                        wait = 2 ** attempt
                        logger.debug(
                            "Blocked (%s) on %s, retrying in %ss (%s/%s)",
                            response.status,
                            url,
                            wait,
                            attempt + 1,
                            retries,
                        )
                        await asyncio.sleep(wait)
                        _rotate_user_agent(headers)
                        continue
                    break

                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientResponseError as e:
            last_error = e
            if e.status in retry_statuses:
                last_blocked_status = e.status
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.debug(
                        "Blocked (%s) on %s, retrying in %ss (%s/%s)",
                        e.status,
                        url,
                        wait,
                        attempt + 1,
                        retries,
                    )
                    await asyncio.sleep(wait)
                    _rotate_user_agent(headers)
                    continue
                break
            if attempt < retries - 1:
                wait = 2 ** attempt
                logger.debug(
                    "Fetch error on %s: %s, retrying in %ss (%s/%s)",
                    url,
                    e,
                    wait,
                    attempt + 1,
                    retries,
                )
                await asyncio.sleep(wait)
                _rotate_user_agent(headers)
                continue
            break
        except Exception as e:
            last_error = e
            last_blocked_status = None
            if attempt < retries - 1:
                wait = 2 ** attempt
                logger.debug(
                    "Fetch error on %s: %s, retrying in %ss (%s/%s)",
                    url,
                    e,
                    wait,
                    attempt + 1,
                    retries,
                )
                await asyncio.sleep(wait)
                _rotate_user_agent(headers)
                continue
            break

    _log_fetch_failure(
        url=url,
        retries=retries,
        quiet=quiet,
        blocked_status=last_blocked_status,
        error=last_error,
    )
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
