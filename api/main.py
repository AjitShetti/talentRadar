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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
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
from api.routers import search, query, recommend, trends, ingest  # noqa: E402

app.include_router(search.router, prefix="/api/v1")
app.include_router(query.router, prefix="/api/v1")
app.include_router(recommend.router, prefix="/api/v1")
app.include_router(trends.router, prefix="/api/v1")
app.include_router(ingest.router, prefix="/api/v1")


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
