"""
api/rate_limit.py
~~~~~~~~~~~~~~~~~
Fixed-window rate limiting middleware.

Why this exists instead of ``slowapi``'s ``SlowAPIMiddleware``:
``slowapi`` resolves the matching route by walking ``app.routes`` looking for
an object exposing ``.endpoint``. Since FastAPI 0.141 an ``include_router()``
call leaves a single ``fastapi.routing._IncludedRouter`` in ``app.routes``
rather than flattening the child routes, so that lookup returns ``None`` for
every router-mounted path -- and ``slowapi`` treats "no handler found" as
"exempt from limiting". The practical effect was that only the handful of
routes declared directly on ``app`` (``/``, ``/health``) were ever limited and
every real endpoint, including ``/api/auth/login``, was wide open.

This middleware keys off the request path directly, so it does not care how the
route was registered.

Storage is per-process and in-memory, which is correct for a single API
container. Running multiple workers/replicas needs a shared backend (Redis)
for the counters to be global rather than per-process.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

# Paths that must never be throttled: health checks feed container
# orchestration, and the docs are static.
EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})

# Credential endpoints get the strict budget.
AUTH_PATHS = frozenset({"/api/auth/login", "/api/auth/signup"})


@dataclass
class _Bucket:
    count: int
    reset_at: float


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window limiter keyed on (client IP, path)."""

    def __init__(
        self,
        app,
        *,
        default_requests: int,
        default_window: int,
        auth_requests: int,
        auth_window: int,
    ) -> None:
        super().__init__(app)
        self._default = (default_requests, default_window)
        self._auth = (auth_requests, auth_window)
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._last_prune = time.monotonic()

    # -- helpers ------------------------------------------------------- #

    @staticmethod
    def _client_key(request: Request) -> str:
        """Identify the caller.

        Deliberately uses the peer address and *not* ``X-Forwarded-For``:
        that header is attacker-controlled unless a trusted proxy overwrites
        it, and honouring it blindly would let anyone bypass the limit by
        rotating a header value. Behind a real proxy, terminate XFF there or
        run uvicorn with ``--proxy-headers`` and a trusted-host list.
        """
        client = request.client
        return client.host if client else "unknown"

    def _prune(self, now: float) -> None:
        """Drop expired buckets so the map cannot grow without bound."""
        if now - self._last_prune < 60.0:
            return
        self._last_prune = now
        expired = [k for k, b in self._buckets.items() if b.reset_at <= now]
        for k in expired:
            del self._buckets[k]

    # -- middleware ---------------------------------------------------- #

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in EXEMPT_PATHS:
            return await call_next(request)

        limit, window = self._auth if path in AUTH_PATHS else self._default
        now = time.monotonic()
        self._prune(now)

        key = (self._client_key(request), path)
        bucket = self._buckets.get(key)
        if bucket is None or bucket.reset_at <= now:
            bucket = _Bucket(count=0, reset_at=now + window)
            self._buckets[key] = bucket

        bucket.count += 1
        retry_after = max(1, int(bucket.reset_at - now))

        if bucket.count > limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please retry later."},
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - bucket.count))
        return response
