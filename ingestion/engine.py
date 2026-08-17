"""
ingestion/engine.py
~~~~~~~~~~~~~~~~~~~
Central orchestration engine for real-time, multi-source job scraping.

Coordinates:
- ATS platforms (Greenhouse, Ashby, Lever)
- Indian boards (LinkedIn Guest, Foundit, Freshersworld)
- Stealth boards (Naukri, Indeed India)

Features:
- Parallel dispatch via asyncio.as_completed
- Per-source latency timing & circuit breaker timeouts
- Cross-source job deduplication
- Asynchronous generator for Server-Sent Events (SSE) streaming
- 8-hour search query caching
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any, AsyncGenerator

from domain.entities import Job
from ingestion.scrapers.ats_scraper import ATSScraper
from ingestion.scrapers.indian_boards_scraper import IndianBoardsScraper
from ingestion.scrapers.stealth_boards_scraper import StealthBoardsScraper
from services.search_cache_service import SearchCacheService

logger = logging.getLogger(__name__)


def compute_job_dedup_hash(job: Job) -> str:
    """Creates a deterministic hash for deduplicating identical job postings across boards."""
    company = (job.extra_metadata or {}).get("company_name", "") or str(job.company_id)
    raw_str = f"{company.strip().lower()}:{job.title.strip().lower()}:{(job.city or '').strip().lower()}"
    return hashlib.md5(raw_str.encode("utf-8")).hexdigest()


def job_to_dict(job: Job) -> dict[str, Any]:
    """Converts a domain Job entity to a serializable dictionary matching JobResponseSchema."""
    company_name = (job.extra_metadata or {}).get("company_name", "Company")
    return {
        "id": str(job.id),
        "title": job.title,
        "company_id": str(job.company_id),
        "company_name": company_name,
        "company": company_name,
        "source": job.source,
        "source_url": job.source_url,
        "location_raw": job.location_raw,
        "country": job.country,
        "city": job.city,
        "is_remote": job.is_remote,
        "seniority": job.seniority.value if job.seniority else None,
        "employment_type": job.employment_type.value if job.employment_type else None,
        "salary_raw": job.salary_raw,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "skills": job.skills or [],
        "tags": job.tags or [],
        "description_clean": job.description_clean,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "match_score": None,
    }


class RealtimeScraperEngine:
    """
    Orchestration engine for on-demand job scraping across multiple sources.
    """

    @classmethod
    async def _run_scraper_with_timing(
        cls,
        name: str,
        coro: Any,
        timeout_seconds: float = 6.0,
    ) -> tuple[str, list[Job], float, str]:
        """
        Executes a single scraper coroutine with circuit breaker timeout and latency tracking.
        Returns: (source_name, jobs, latency_ms, status_string)
        """
        start_time = time.perf_counter()
        try:
            jobs = await asyncio.wait_for(coro, timeout=timeout_seconds)
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return name, jobs or [], latency_ms, "success"
        except asyncio.TimeoutError:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.warning(f"Scraper [{name}] timed out after {timeout_seconds}s")
            return name, [], latency_ms, "timeout"
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(f"Scraper [{name}] failed: {exc}")
            return name, [], latency_ms, f"error: {str(exc)}"

    @classmethod
    async def stream_search(
        cls,
        query: str,
        location: str | None = "India",
        is_remote: bool | None = None,
        force_refresh: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Streams job search results progressively using Server-Sent Events (SSE).
        Checks 8-hour cache first unless force_refresh is True.
        """
        start_total = time.perf_counter()

        # Check Cache
        if not force_refresh:
            cached_data = await SearchCacheService.get_cached_search(query, location, is_remote)
            if cached_data:
                logger.info(f"Returning cached search results for '{query}' in '{location}'")
                yield {
                    "event": "cached",
                    "data": {
                        "jobs": cached_data.get("jobs", []),
                        "total": len(cached_data.get("jobs", [])),
                        "sources_stats": cached_data.get("sources_stats", {}),
                        "is_cached": True,
                        "latency_ms": round((time.perf_counter() - start_total) * 1000, 2),
                    },
                }
                yield {"event": "done", "data": {"total_jobs": len(cached_data.get("jobs", [])), "is_cached": True}}
                return

        sources = [
            ("ats_platforms", ATSScraper.search_all_ats(query, location, is_remote), 5.0),
            ("linkedin", IndianBoardsScraper.search_linkedin_guest(query, location, is_remote), 5.0),
            ("foundit", IndianBoardsScraper.search_foundit_india(query, location, is_remote), 5.0),
            ("freshersworld", IndianBoardsScraper.search_freshersworld(query, location, is_remote), 4.0),
            ("instahyre", StealthBoardsScraper.search_instahyre(query, location, is_remote), 5.0),
            ("indeed_india", StealthBoardsScraper.search_indeed_india(query, location, is_remote), 7.0),
            ("naukri", StealthBoardsScraper.search_naukri(query, location, is_remote), 10.0),
        ]

        yield {
            "event": "init",
            "data": {
                "query": query,
                "location": location,
                "is_remote": is_remote,
                "sources": [s[0] for s in sources],
                "timestamp": time.time(),
            },
        }

        # Create scraper tasks
        tasks = [
            asyncio.create_task(cls._run_scraper_with_timing(name, coro, timeout))
            for name, coro, timeout in sources
        ]

        seen_hashes: set[str] = set()
        all_deduped_jobs: list[dict[str, Any]] = []
        sources_stats: dict[str, Any] = {}

        # Stream chunks as soon as each scraper finishes
        for completed_task in asyncio.as_completed(tasks):
            source_name, jobs, latency_ms, status = await completed_task
            sources_stats[source_name] = {
                "latency_ms": latency_ms,
                "count": len(jobs),
                "status": status,
            }

            # Deduplicate new jobs
            new_job_dicts: list[dict[str, Any]] = []
            for j in jobs:
                h = compute_job_dedup_hash(j)
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    d = job_to_dict(j)
                    new_job_dicts.append(d)
                    all_deduped_jobs.append(d)

            yield {
                "event": "chunk",
                "data": {
                    "source": source_name,
                    "jobs": new_job_dicts,
                    "count": len(new_job_dicts),
                    "latency_ms": latency_ms,
                    "status": status,
                },
            }

        total_latency_ms = round((time.perf_counter() - start_total) * 1000, 2)

        # Cache the aggregated results for 8 hours
        cache_payload = {
            "jobs": all_deduped_jobs,
            "sources_stats": sources_stats,
            "total_latency_ms": total_latency_ms,
            "cached_at": time.time(),
        }
        await SearchCacheService.set_cached_search(query, location, is_remote, cache_payload)

        yield {
            "event": "done",
            "data": {
                "total_jobs": len(all_deduped_jobs),
                "sources_stats": sources_stats,
                "total_latency_ms": total_latency_ms,
                "is_cached": False,
            },
        }

    @classmethod
    async def search_all(
        cls,
        query: str,
        location: str | None = "India",
        is_remote: bool | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """
        Non-streaming helper returning all consolidated jobs.
        """
        all_jobs = []
        sources_stats = {}
        async for item in cls.stream_search(query, location, is_remote, force_refresh):
            evt = item["event"]
            if evt == "cached":
                return item["data"]
            elif evt == "chunk":
                all_jobs.extend(item["data"]["jobs"])
                sources_stats[item["data"]["source"]] = {
                    "latency_ms": item["data"]["latency_ms"],
                    "status": item["data"]["status"],
                }
            elif evt == "done":
                return {
                    "jobs": all_jobs,
                    "total": len(all_jobs),
                    "sources_stats": item["data"].get("sources_stats", sources_stats),
                    "total_latency_ms": item["data"].get("total_latency_ms", 0),
                    "is_cached": False,
                }
        return {"jobs": all_jobs, "total": len(all_jobs), "sources_stats": sources_stats}
