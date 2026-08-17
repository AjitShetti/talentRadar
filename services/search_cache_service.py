"""
services/search_cache_service.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
High-speed query caching service with Redis and in-memory TTL fallback.

- 8-hour Time-To-Live (TTL) for job search queries
- SHA-256 / MD5 key generation across normalized query, location, and filters
- Handles serialization and deserialization of JobResponseSchema payloads
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from config.settings import Settings

logger = logging.getLogger(__name__)

# In-memory cache fallback: key -> (timestamp, data)
_MEMORY_CACHE: dict[str, tuple[float, str]] = {}
_DEFAULT_TTL_SECONDS = 8 * 3600  # 8 hours


class SearchCacheService:
    """
    Service for caching multi-source job search responses.
    """

    _redis_client: Any = None
    _redis_initialized: bool = False

    @classmethod
    async def _get_redis(cls):
        """Lazy-initialize async Redis client."""
        if not cls._redis_initialized:
            try:
                import redis.asyncio as aioredis
                settings = Settings()
                cls._redis_client = aioredis.from_url(
                    settings.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=2.0,
                )
                await cls._redis_client.ping()
                cls._redis_initialized = True
                logger.info("Connected to Redis for search caching.")
            except Exception as exc:
                logger.warning(f"Could not connect to Redis: {exc}. Using in-memory cache fallback.")
                cls._redis_client = None
                cls._redis_initialized = True
        return cls._redis_client

    @classmethod
    def make_cache_key(cls, query: str | None, location: str | None, is_remote: bool | None) -> str:
        """Constructs a deterministic cache key from search parameters."""
        raw_key = f"{(query or '').strip().lower()}|{(location or '').strip().lower()}|{bool(is_remote)}"
        hashed = hashlib.md5(raw_key.encode("utf-8")).hexdigest()
        return f"talentradar:search:{hashed}"

    @classmethod
    async def get_cached_search(
        cls, query: str | None, location: str | None, is_remote: bool | None
    ) -> dict[str, Any] | None:
        """
        Retrieves cached search results if present and unexpired.
        Returns parsed dict containing jobs list, sources stats, and cached_at timestamp.
        """
        key = cls.make_cache_key(query, location, is_remote)
        redis_client = await cls._get_redis()

        # Try Redis first
        if redis_client:
            try:
                cached_val = await redis_client.get(key)
                if cached_val:
                    return json.loads(cached_val)
            except Exception as exc:
                logger.warning(f"Redis get error: {exc}")

        # Fallback to in-memory cache
        if key in _MEMORY_CACHE:
            ts, payload_str = _MEMORY_CACHE[key]
            if time.time() - ts < _DEFAULT_TTL_SECONDS:
                return json.loads(payload_str)
            else:
                del _MEMORY_CACHE[key]

        return None

    @classmethod
    async def set_cached_search(
        cls,
        query: str | None,
        location: str | None,
        is_remote: bool | None,
        data: dict[str, Any],
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        """
        Saves search results to cache with TTL.
        """
        key = cls.make_cache_key(query, location, is_remote)
        payload_str = json.dumps(data, default=str)
        redis_client = await cls._get_redis()

        # Try Redis first
        if redis_client:
            try:
                await redis_client.setex(key, ttl_seconds, payload_str)
                return
            except Exception as exc:
                logger.warning(f"Redis set error: {exc}")

        # In-memory fallback
        _MEMORY_CACHE[key] = (time.time(), payload_str)
