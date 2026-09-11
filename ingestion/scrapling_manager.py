"""
ingestion/scrapling_manager.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Multi-tier HTTP fetching for the job scrapers.

Three tiers, tried in order, each degrading into the next:

1. **curl_cffi TLS/HTTP2 impersonation** - replays a real Chrome's TLS
   fingerprint (JA3) and HTTP/2 settings. This is what gets past the
   Cloudflare bot check on Indeed India and Instahyre. Measured: Indeed
   returns 403 with an "enable javascript" challenge to plain httpx, and 200
   with 1.3 MB of listings to this tier.
2. **httpx with browser-like headers** - the ATS JSON APIs and static boards
   never needed more than this.
3. **Camoufox headless browser** - only for pages that hydrate client-side,
   and only behind ``ENABLE_STEALTH_SCRAPERS``. Off, uninstalled and unused
   in the deployed image; see below.

Why curl_cffi rather than scrapling
-----------------------------------
Scrapling's impersonation *is* curl_cffi underneath, but ``scrapling.fetchers``
imports playwright and patchright at module level, so taking its fetcher meant
taking ~300 MB of browser stack that a 512 MB instance cannot run anyway.
Binding to curl_cffi directly costs ~5 MB and yields the same fingerprint, so
impersonation is now part of the default deployment rather than an opt-in
extra it could never afford.

Every tier is optional. With curl_cffi absent the module falls back to httpx
and simply loses the Cloudflare-protected sources - an absent optional
dependency degrades a feature, never the deployment.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from config.settings import get_settings

logger = logging.getLogger(__name__)

# Tier 1: TLS/HTTP2 impersonation. ~5 MB, no browser, no JS engine.
try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession

    CURL_CFFI_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by the no-curl_cffi path
    _CurlAsyncSession = None
    CURL_CFFI_AVAILABLE = False
    logger.info(
        "curl_cffi is not installed; scraping falls back to httpx. "
        "Cloudflare-protected sources (Indeed India, Instahyre) will return nothing."
    )

# Backwards-compatible alias. Callers and tests asked "is impersonation
# available?" through this name long before the implementation changed.
SCRAPLING_AVAILABLE = CURL_CFFI_AVAILABLE

# Tier 3: headless browser, for client-side-hydrated pages only.
try:
    from camoufox.async_api import AsyncCamoufox

    CAMOUFOX_AVAILABLE = True
except ImportError:
    CAMOUFOX_AVAILABLE = False
    AsyncCamoufox = None

# The Chrome build curl_cffi impersonates. Bumping this occasionally matters:
# a fingerprint for a long-dead Chrome is itself a signal. Verified against
# Indeed India and Instahyre on 2026-09-10.
DEFAULT_IMPERSONATE = "chrome124"


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
        impersonate: str = DEFAULT_IMPERSONATE,
    ) -> tuple[int, str | dict[str, Any]]:
        """
        Fetch a URL, impersonating a real Chrome's TLS/HTTP2 fingerprint when
        curl_cffi is available and falling back to httpx when it is not.

        Returns ``(status_code, text_or_parsed_json)``.
        """
        # Tier 1: curl_cffi with TLS/HTTP2 impersonation.
        if CURL_CFFI_AVAILABLE and _CurlAsyncSession is not None:
            try:
                # curl_cffi sends the header set matching the browser it is
                # impersonating. Adding DEFAULT_BROWSER_HEADERS on top would
                # contradict that fingerprint - a header/TLS mismatch is
                # precisely what bot checks look for - so only headers a
                # caller asked for are passed through.
                extra_headers = dict(headers or {})

                async with _CurlAsyncSession() as session:
                    resp = await session.get(
                        url,
                        impersonate=impersonate,
                        headers=extra_headers or None,
                        params=params,
                        timeout=timeout,
                        # Certificates are verified. This was ``verify=False``,
                        # which accepted any certificate from any host and made
                        # every scrape trivially interceptable - the fetched HTML
                        # is parsed into job rows and shown to users, so a
                        # substituted response becomes content in the product.
                        verify=True,
                        allow_redirects=True,
                    )
                    raw_text = resp.text or ""
                    if resp.status_code == 200:
                        stripped = raw_text.lstrip()
                        if stripped.startswith(("{", "[")):
                            try:
                                return resp.status_code, json.loads(raw_text)
                            except ValueError:
                                return resp.status_code, raw_text
                        return resp.status_code, raw_text
                    logger.debug(
                        "Impersonated fetch of %s returned %s; trying httpx.",
                        url,
                        resp.status_code,
                    )
            except Exception as exc:
                logger.debug(f"curl_cffi fetch failed for {url}: {exc}. Trying HTTPX fallback.")

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

        The browser is used only when ``ENABLE_STEALTH_SCRAPERS`` is on. It is
        off by default for two reasons that both matter on a public, free
        deployment: each call launches a headless Firefox, which OOMs a 512 MB
        instance, and impersonating a browser is against the terms of the
        sites this reaches. With it off the call still returns — it degrades
        to the plain HTTP fetch below, which gets static pages and simply
        finds nothing on the JS-hydrated ones.
        """
        if get_settings().enable_stealth_scrapers and CAMOUFOX_AVAILABLE and AsyncCamoufox is not None:
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
                logger.warning(f"Camoufox stealth fetch failed on {url}: {exc}. Falling back to the impersonated fetch.")

        # Fall back to the curl_cffi impersonation tier. It cannot run the
        # page's JavaScript, so a client-side-hydrated board yields nothing
        # here - that is a coverage loss, not an error.
        status, content = await cls.fetch_html_or_json(url, timeout=timeout)
        return status, content if isinstance(content, str) else str(content)

    @classmethod
    async def close(cls):
        """Close shared network clients."""
        if cls._http_client and not cls._http_client.is_closed:
            await cls._http_client.aclose()
            cls._http_client = None
