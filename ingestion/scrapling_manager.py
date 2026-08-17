"""
ingestion/scrapling_manager.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Manager for Scrapling fetchers and stealth browser sessions.

Provides:
- AsyncFetcher with TLS/HTTP2 browser impersonation (bypasses Cloudflare on Indeed/LinkedIn/Instahyre)
- AsyncCamoufox / AsyncStealthyFetcher for anti-bot protected portals (Naukri Next.js hydration)
- Built-in resource blocking (images, media, fonts) for 3-5x lower latency
- Graceful multi-tier fallbacks ensuring 0 crashes even under network restrictions
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Try importing Scrapling components
try:
    from scrapling.fetchers import AsyncFetcher, Fetcher
    SCRAPLING_AVAILABLE = True
except ImportError:
    SCRAPLING_AVAILABLE = False
    AsyncFetcher = None
    Fetcher = None
    logger.warning("Scrapling is not importable. Falling back to HTTPX client.")

# Try importing Camoufox
try:
    from camoufox.async_api import AsyncCamoufox
    CAMOUFOX_AVAILABLE = True
except ImportError:
    CAMOUFOX_AVAILABLE = False
    AsyncCamoufox = None


# Common default browser headers for fast HTTP scraping
DEFAULT_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class ScraplingManager:
    """
    Singleton manager providing high-speed async HTTP and stealth browser sessions.
    """

    _http_client: httpx.AsyncClient | None = None

    @classmethod
    async def get_http_client(cls) -> httpx.AsyncClient:
        """Get or initialize a shared HTTPX async client with browser-like headers."""
        if cls._http_client is None or cls._http_client.is_closed:
            cls._http_client = httpx.AsyncClient(
                headers=DEFAULT_BROWSER_HEADERS,
                timeout=httpx.Timeout(8.0, connect=4.0),
                follow_redirects=True,
                limits=httpx.Limits(max_keepalive_connections=50, max_connections=100),
            )
        return cls._http_client

    @classmethod
    async def fetch_html_or_json(
        cls,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 6.0,
        impersonate: str = "chrome124",
    ) -> tuple[int, str | dict[str, Any]]:
        """
        Fast HTTP fetcher with browser TLS impersonation via Scrapling AsyncFetcher.
        Falls back to HTTPX if Scrapling is unavailable.
        Returns (status_code, text_or_json).
        """
        # Tier 1: Scrapling AsyncFetcher with TLS/HTTP2 impersonation
        if SCRAPLING_AVAILABLE and AsyncFetcher is not None:
            try:
                page = await AsyncFetcher.get(
                    url,
                    impersonate=impersonate,
                    headers=headers,
                    params=params,
                    timeout=int(timeout),
                    verify=False,
                )
                if page.status == 200:
                    raw_text = page.body.decode("utf-8", errors="ignore") if hasattr(page, "body") and isinstance(page.body, (bytes, bytearray)) else (page.text or "")
                    # Check if JSON
                    if raw_text.strip().startswith(("{", "[")):
                        try:
                            import json
                            return page.status, json.loads(raw_text)
                        except Exception:
                            return page.status, raw_text
                    return page.status, raw_text
                return page.status, page.text or ""
            except Exception as exc:
                logger.debug(f"Scrapling AsyncFetcher failed for {url}: {exc}. Trying HTTPX fallback.")

        # Tier 2: HTTPX client fallback
        client = await cls.get_http_client()
        req_headers = {**DEFAULT_BROWSER_HEADERS, **(headers or {})}

        try:
            resp = await client.get(url, headers=req_headers, params=params, timeout=timeout)
            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    return resp.status_code, resp.json()
                except Exception:
                    return resp.status_code, resp.text
            return resp.status_code, resp.text
        except httpx.TimeoutException:
            logger.warning(f"Timeout fetching {url} after {timeout}s")
            return 408, ""
        except Exception as exc:
            logger.error(f"Error fetching {url}: {exc}")
            return 500, ""

    @classmethod
    async def fetch_stealth(
        cls,
        url: str,
        timeout: float = 12.0,
        wait_for_selector: str | None = None,
        wait_seconds: float = 3.0,
    ) -> tuple[int, str]:
        """
        Stealth fetcher using Camoufox anti-detect browser with image/font blocking.
        Allows full JavaScript hydration for single-page applications (Naukri, etc.).
        """
        if CAMOUFOX_AVAILABLE and AsyncCamoufox is not None:
            try:
                async with AsyncCamoufox(headless=True) as browser:
                    page = await browser.new_page()
                    # Block heavy media to minimize latency & RAM
                    await page.route(
                        "**/*.{png,jpg,jpeg,gif,webp,mp4,avi,mov,svg,woff,woff2,ttf,eot}",
                        lambda route: route.abort(),
                    )
                    response = await page.goto(url, timeout=int(timeout * 1000))
                    status = response.status if response else 200

                    if wait_for_selector:
                        try:
                            await page.wait_for_selector(wait_for_selector, timeout=int(wait_seconds * 1000))
                        except Exception:
                            pass
                    elif wait_seconds > 0:
                        await page.wait_for_timeout(int(wait_seconds * 1000))

                    html_content = await page.content()
                    return status, html_content
            except Exception as exc:
                logger.warning(f"Camoufox stealth fetch failed on {url}: {exc}. Trying Scrapling AsyncFetcher.")

        # Fallback to Scrapling AsyncFetcher with browser impersonation
        status, content = await cls.fetch_html_or_json(url, timeout=timeout)
        return status, content if isinstance(content, str) else str(content)

    @classmethod
    async def close(cls):
        """Close shared network clients."""
        if cls._http_client and not cls._http_client.is_closed:
            await cls._http_client.aclose()
            cls._http_client = None
