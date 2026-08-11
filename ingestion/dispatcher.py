"""
ingestion/dispatcher.py
~~~~~~~~~~~~~~~~~~~~~~~
Orchestrates multi-source job discovery + shared persistence.

The dispatcher builds enabled sources from ``ingestion.sources``, runs each
one's ``discover()`` with shared role/location parameters, deduplicates by
URL, and hands the batch to ``ingestion.pipeline.run_pipeline`` for LLM
parsing and persistence.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ingestion.pipeline import run_pipeline
from ingestion.sources import JobSource, build_source, default_sources

logger = logging.getLogger(__name__)


async def dispatch_ingestion(
    *,
    roles: list[str] | None = None,
    locations: list[str] | None = None,
    sources: list[str] | None = None,
    max_results_per_query: int = 5,
    per_source: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Run discovery across the enabled sources and persist everything.

    Parameters
    ----------
    roles:
        Job titles to search for (shared across sources).
    locations:
        Locations to search (Tavily/Cutshort only; ATS APIs ignore it).
    sources:
        Source names to enable (defaults to ``default_sources()``).
    max_results_per_query:
        Result cap per Tavily-style query.
    per_source:
        Optional per-source constructor kwargs, e.g.
        ``{"greenhouse": {"tokens": ["stripe"]}}``.

    Returns a summary dict with per-source fetch counts and total DB counts.
    """
    roles = roles or ["Software Engineer", "Data Scientist"]
    locations = locations or []
    sources = sources or default_sources()
    run_id = str(uuid.uuid4())

    per_source = per_source or {}
    all_raw: list[Any] = []
    seen_urls: set[str] = set()
    source_stats: dict[str, int] = {}

    for name in sources:
        try:
            factory_kwargs = per_source.get(name, {})
            source: JobSource = build_source(name, **factory_kwargs)
            raw = source.discover(
                roles=roles,
                locations=locations,
                count=max_results_per_query,
            )
            logger.info("source %s returned %d raw results", name, len(raw))
            for result in raw:
                if not result.url:
                    continue
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)
                all_raw.append(result)
            source_stats[name] = len(raw)
        except Exception as exc:
            logger.warning("source %s failed: %s", name, exc)
            source_stats[name] = -1

    counts = await run_pipeline(
        all_raw,
        source=",".join(sources),
        run_id=run_id,
    )

    return {
        "run_id": run_id,
        "sources": sources,
        "source_stats": source_stats,
        "total_fetched": len(all_raw),
        **counts,
    }
