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
        trusted_proxy_hops: int = 0,
    ) -> None:
        super().__init__(app)
        self._default = (default_requests, default_window)
        self._auth = (auth_requests, auth_window)
        self._hops = max(0, trusted_proxy_hops)
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._last_prune = time.monotonic()

    # -- helpers ------------------------------------------------------- #

    def _client_key(self, request: Request) -> str:
        """Identify the caller.

        ``X-Forwarded-For`` is attacker-controlled, so it is honoured only as
        far as the operator says there are proxies in front of the app
        (``TRUSTED_PROXY_HOPS``). With N trusted hops the Nth entry from the
        *right* is the address the outermost trusted proxy actually observed;
        everything further left was supplied by the client and is ignored.

        The default of 0 keeps the safe behaviour of using the peer address.
        That default is wrong on a PaaS, though: behind a platform router
        every request appears to come from the router, so all users share one
        bucket and the whole deployment starts returning 429 as soon as a
        handful of people browse at once. Set TRUSTED_PROXY_HOPS=1 there.
        """
        if self._hops:
            forwarded = request.headers.get("x-forwarded-for", "")
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            if len(parts) >= self._hops:
                return parts[-self._hops]
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
        # OPTIONS is the browser's CORS preflight, not a call the user made.
        # Counting it halved the effective budget for every cross-origin page.
        if path in EXEMPT_PATHS or request.method == "OPTIONS":
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
