"""
api/main.py
~~~~~~~~~~~
FastAPI application entry point for TalentRadar.

Wires up all routers, middleware, and lifecycle management.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import get_settings
from storage.database import close_engine

from api.rate_limit import RateLimitMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _error_response(request: Request, exc: BaseException) -> JSONResponse:
    """Log the failure with a correlation id and return an opaque 500.

    The exception text is never echoed to the caller: SQLAlchemy embeds the
    failing statement *and its bound parameters* in ``str(exc)``, so returning
    it leaked table/column names and row data (including password hashes) to
    anyone who could provoke an IntegrityError.
    """
    error_id = uuid.uuid4().hex[:12]
    logger.exception(
        "Unhandled error [%s] on %s %s",
        error_id,
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error_id": error_id},
    )


class ErrorEnvelopeMiddleware(BaseHTTPMiddleware):
    """Catch anything escaping the routers so CORS headers still get applied."""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            return _error_response(request, exc)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifecycle management."""
    # Startup
    logger.info("TalentRadar API starting up...")
    settings = get_settings()
    logger.info("Database: %s@%s/%s", settings.postgres_user, settings.postgres_host, settings.postgres_db)

    yield

    # Shutdown
    logger.info("TalentRadar API shutting down...")
    await close_engine()
    logger.info("Database connections closed")


# Create FastAPI app
app = FastAPI(
    title="TalentRadar API",
    description="AI-powered job intelligence platform with semantic search, market trends, and candidate matching.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Rate limiting. Added before CORS so that CORSMiddleware stays the outermost
# layer and 429 responses still carry CORS headers back to the browser.
settings = get_settings()
app.add_middleware(
    RateLimitMiddleware,
    default_requests=settings.rate_limit_default_requests,
    default_window=settings.rate_limit_default_window_seconds,
    auth_requests=settings.rate_limit_auth_requests,
    auth_window=settings.rate_limit_auth_window_seconds,
)

# Convert unhandled exceptions into a JSON 500 *inside* the CORS layer.
#
# Starlette routes an ``@app.exception_handler(Exception)`` into
# ServerErrorMiddleware, which is the outermost layer -- outside
# CORSMiddleware. Responses it produces therefore carry no
# Access-Control-Allow-Origin header, so the browser hides the real 500 behind
# "blocked by CORS policy" and the frontend logs a misleading CORS error. This
# middleware catches the exception one layer further in, so the 500 travels
# back out through CORSMiddleware like any other response.
app.add_middleware(ErrorEnvelopeMiddleware)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Unhandled errors must still travel back through CORSMiddleware, otherwise the
# browser reports a bare "Failed to fetch" and the real cause is invisible to
# the frontend. This handler converts any escaped exception into a JSON 500.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Return an opaque 500 and keep the diagnostics server-side.

    The exception text is never echoed to the caller: SQLAlchemy embeds the
    failing statement *and its bound parameters* in ``str(exc)``, so returning
    it leaked table/column names and row data (including password hashes) to
    anyone who could provoke an IntegrityError. Clients get a correlation id
    they can quote instead; the full traceback goes to the log under that id.
    """
    return _error_response(request, exc)


# Health check endpoints
@app.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "name": "TalentRadar API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "1.0.0",
    }


# Register routers
from api.routers import search, query, recommend, ingest, match, auth, applications  # noqa: E402
from api.routers import interview  # noqa: E402
from api.routers import profile, resumes, company_intel, career, agent, dashboard  # noqa: E402

app.include_router(auth.router)
app.include_router(search.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")
app.include_router(recommend.router, prefix="/api/v1")
app.include_router(ingest.router, prefix="/api/v1")
app.include_router(match.router, prefix="/api/v1")
app.include_router(interview.router, prefix="/api/v1")
app.include_router(applications.router, prefix="/api/v1")
app.include_router(profile.router, prefix="/api/v1")
app.include_router(resumes.router, prefix="/api/v1")
app.include_router(company_intel.router, prefix="/api/v1")
app.include_router(career.router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/api/v1")


# API documentation
@app.get("/api/v1")
async def api_root():
    """API v1 root with available endpoints."""
    return {
        "version": "v1",
        "endpoints": {
            "search": {
                "structured": "POST /api/v1/search/structured",
                "semantic": "POST /api/v1/search/semantic",
                "detail": "GET /api/v1/search/{job_id}",
                "live": "GET /api/v1/search/live",
                "stream": "GET /api/v1/search/stream",
            },
            "query": {
                "process": "POST /api/v1/query",
            },
            "recommend": {
                "match": "POST /api/v1/recommend/match",
                "skills": "POST /api/v1/recommend/analyze-skills",
            },
            "ingest": {
                "trigger": "POST /api/v1/ingest/trigger",
                "runs": "GET /api/v1/ingest/runs",
                "run_detail": "GET /api/v1/ingest/runs/{run_id}",
            },
            "interview": {
                "start":       "POST /api/v1/interview/sessions/start",
                "answer":      "POST /api/v1/interview/sessions/answer",
                "end":         "POST /api/v1/interview/sessions/end",
                "history":     "GET  /api/v1/interview/sessions/history",
                "transcribe":  "POST /api/v1/interview/voice/transcribe",
            },
            "profile": {
                "get":    "GET  /api/v1/profile",
                "upsert": "POST /api/v1/profile",
            },
            "resumes": {
                "analyze": "POST /api/v1/resumes/analyze",
                "tailor":  "POST /api/v1/resumes/tailor",
                "cover_letter": "POST /api/v1/resumes/cover-letter",
                "gaps":    "POST /api/v1/resumes/gaps",
            },
            "company_intel": {
                "by_id": "GET /api/v1/company-intel/{company_id}",
                "search": "GET /api/v1/company-intel?name=...",
            },
            "career": {
                "weaknesses": "GET  /api/v1/career/weaknesses",
                "recommend":  "POST /api/v1/career/recommend",
            },
            "agent": {
                "next_action": "GET  /api/v1/agent/next-action",
                "memories":    "GET/POST /api/v1/agent/memories",
            },
            "dashboard": {
                "overview": "GET /api/v1/dashboard/overview",
            },
        },
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
