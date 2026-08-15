"""
api/main.py
~~~~~~~~~~~
FastAPI application entry point for TalentRadar.

Wires up all routers, middleware, and lifecycle management.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import get_settings
from storage.database import close_engine

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize Rate Limiter (In-Memory for now, can be wired to Redis later)
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])

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

# Register rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS middleware
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
from api.routers import search, query, recommend, trends, ingest, match, auth, applications  # noqa: E402
from api.routers import interview  # noqa: E402
from api.routers import profile, resumes, company_intel, career, agent, dashboard  # noqa: E402

app.include_router(auth.router)
app.include_router(search.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")
app.include_router(recommend.router, prefix="/api/v1")
app.include_router(trends.router, prefix="/api/v1")
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
            },
            "query": {
                "process": "POST /api/v1/query",
            },
            "recommend": {
                "match": "POST /api/v1/recommend/match",
                "skills": "POST /api/v1/recommend/analyze-skills",
            },
            "trends": {
                "trends": "POST /api/v1/trends",
                "skills": "GET /api/v1/trends/skills",
                "salaries": "GET /api/v1/trends/salaries",
                "locations": "GET /api/v1/trends/locations",
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
