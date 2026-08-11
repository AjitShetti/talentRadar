"""
ingestion/sources/
~~~~~~~~~~~~~~~~~~
Multi-source job discovery.

Each module exposes a source class implementing the ``JobSource`` protocol
from ``ingestion.sources.base``. The ``SOURCE_REGISTRY`` maps stable names
to factory functions so the dispatcher can build them on demand.

Available sources:
    greenhouse  — public Greenhouse board JSON API (no key)
    lever       — public Lever postings JSON API (no key)
    ashby       — public Ashby posting API (no key)
    cutshort    — Cutshort (India) via Tavily domain-scoped discovery
    tavily      — generic web/Tavily discovery fallback
"""

from __future__ import annotations

import os
from typing import Callable

from ingestion.sources.ashby import AshbySource
from ingestion.sources.base import BaseJobSource, JobSource
from ingestion.sources.cutshort import CutshortSource
from ingestion.sources.greenhouse import GreenhouseSource
from ingestion.sources.lever import LeverSource
from ingestion.sources.tavily import TavilySource

SourceFactory = Callable[..., JobSource]

SOURCE_REGISTRY: dict[str, SourceFactory] = {
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "ashby": AshbySource,
    "cutshort": CutshortSource,
    "tavily": TavilySource,
}


def build_source(name: str, **kwargs: object) -> JobSource:
    """Instantiate a source by name, raising KeyError for unknown sources."""
    factory = SOURCE_REGISTRY[name]
    return factory(**kwargs)


def default_sources() -> list[str]:
    """Sources enabled by default (configurable via ENABLED_SOURCES)."""
    env = os.getenv("ENABLED_SOURCES")
    if env:
        return [s.strip() for s in env.split(",") if s.strip()]
    return ["greenhouse", "lever", "ashby", "cutshort"]


__all__ = [
    "AshbySource",
    "BaseJobSource",
    "CutshortSource",
    "GreenhouseSource",
    "JobSource",
    "LeverSource",
    "SOURCE_REGISTRY",
    "TavilySource",
    "build_source",
    "default_sources",
]
