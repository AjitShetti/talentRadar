"""
services/cache_backend.py
~~~~~~~~~~~~~~~~~~~~~~~~~
One shared key-value backend: Redis when reachable, an in-process dict when not.

The deployment target is Render's free Key Value instance - 25 MB, no
persistence, evicted under pressure. That shapes the contract:

* **Nothing may require it.** Every method degrades to the in-memory fallback,
  and callers treat a miss as an ordinary outcome. This is the same rule the
  vector store follows.
* **Values are small and disposable.** Anything stored here must be
  reconstructible by doing the work again.
* **Keys are namespaced ``tr:``** so the instance can be shared.

The in-memory fallback is per-process and dies with it. On a free instance
that sleeps after 15 minutes idle, that means an unconfigured deployment
effectively has no cache at all - which is the state this module was written
to fix, not to paper over.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from config.settings import get_settings

logger = logging.getLogger(__name__)

# key -> (expires_at_epoch, raw_value)
_MEMORY: dict[str, tuple[float, str]] = {}

# The fallback is bounded. Unbounded, a busy instance would accumulate every
# search payload it ever served until the 512 MB container was killed.
_MEMORY_MAX_KEYS = 512


def _memory_evict_if_needed() -> None:
    if len(_MEMORY) <= _MEMORY_MAX_KEYS:
        return
    now = time.time()
    for key, (expires_at, _) in list(_MEMORY.items()):
        if expires_at <= now:
            _MEMORY.pop(key, None)
    if len(_MEMORY) > _MEMORY_MAX_KEYS:
        # Still over budget: drop the soonest-to-expire entries.
        for key, _ in sorted(_MEMORY.items(), key=lambda kv: kv[1][0])[: len(_MEMORY) - _MEMORY_MAX_KEYS]:
            _MEMORY.pop(key, None)


class CacheBackend:
    """Async key-value access with a Redis backend and a memory fallback."""

    _client: Any = None
    _initialised: bool = False

    @classmethod
    async def client(cls) -> Any:
        """The Redis client, or ``None`` when Redis is not reachable."""
        if cls._initialised:
            return cls._client

        cls._initialised = True
        settings = get_settings()
        url = getattr(settings, "redis_url", None)
        if not url:
            cls._client = None
            return None

        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(
                url,
                encoding="utf-8",
                decode_responses=True,
                # Short, because this runs inside a user-facing request. A
                # cache that takes two seconds to say "no" is worse than no
                # cache.
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                health_check_interval=30,
            )
            await client.ping()
            cls._client = client
            logger.info("Cache backend: Redis at %s", url.split("@")[-1])
        except Exception as exc:
            logger.warning(
                "Cache backend: Redis unavailable (%s). Falling back to in-process memory, "
                "which is lost whenever this instance restarts or sleeps.",
                exc,
            )
            cls._client = None
        return cls._client

    # -- primitives ---------------------------------------------------------

    @classmethod
    async def get(cls, key: str) -> str | None:
        client = await cls.client()
        if client is not None:
            try:
                value: str | None = await client.get(key)
                return value
            except Exception as exc:
                logger.debug("Cache get failed for %s: %s", key, exc)

        entry = _MEMORY.get(key)
        if entry is None:
            return None
        expires_at, raw = entry
        if expires_at <= time.time():
            _MEMORY.pop(key, None)
            return None
        return raw

    @classmethod
    async def set(cls, key: str, value: str, ttl_seconds: int) -> None:
        client = await cls.client()
        if client is not None:
            try:
                await client.set(key, value, ex=ttl_seconds)
                return
            except Exception as exc:
                logger.debug("Cache set failed for %s: %s", key, exc)

        _MEMORY[key] = (time.time() + ttl_seconds, value)
        _memory_evict_if_needed()

    @classmethod
    async def delete(cls, key: str) -> None:
        client = await cls.client()
        if client is not None:
            try:
                await client.delete(key)
            except Exception as exc:
                logger.debug("Cache delete failed for %s: %s", key, exc)
        _MEMORY.pop(key, None)

    @classmethod
    async def exists(cls, key: str) -> bool:
        return await cls.get(key) is not None

    # -- helpers ------------------------------------------------------------

    @classmethod
    async def get_json(cls, key: str) -> Any | None:
        raw = await cls.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            # A corrupt or half-written value is a cache miss, not an error.
            await cls.delete(key)
            return None

    @classmethod
    async def set_json(cls, key: str, value: Any, ttl_seconds: int) -> None:
        await cls.set(key, json.dumps(value, default=str, separators=(",", ":")), ttl_seconds)

    @classmethod
    async def incr(cls, key: str, ttl_seconds: int) -> int:
        """
        Increment a counter, creating it with the given TTL.

        Used for the daily LLM budget and the per-source health counters -
        both of which must never raise, so a backend failure returns the
        increment as if it had been the first.
        """
        client = await cls.client()
        if client is not None:
            try:
                pipe = client.pipeline()
                pipe.incr(key)
                pipe.expire(key, ttl_seconds)
                result = await pipe.execute()
                return int(result[0])
            except Exception as exc:
                logger.debug("Cache incr failed for %s: %s", key, exc)

        current = int(await cls.get(key) or 0) + 1
        _MEMORY[key] = (time.time() + ttl_seconds, str(current))
        _memory_evict_if_needed()
        return current

    @classmethod
    async def reset(cls) -> None:
        """Drop cached connection state. For tests."""
        cls._client = None
        cls._initialised = False
        _MEMORY.clear()
