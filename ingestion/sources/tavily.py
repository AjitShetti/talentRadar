"""
ingestion/sources/tavily.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Generic web discovery source backed by Tavily search.

Used as a catch-all / fallback for job boards without a stable public API
(LinkedIn, Naukri, Indeed for the Indian market, and general web postings).
"""

from __future__ import annotations

import logging
from typing import Any

from ingestion.parsers.schemas import RawJobResult
from ingestion.scrapers.tavily_client import TavilyJobScraper
from ingestion.sources.base import BaseJobSource

logger = logging.getLogger(__name__)

_DEFAULT_LOCATIONS = [
    "Remote",
    "Bangalore",
    "Mumbai",
    "Delhi",
    "Hyderabad",
    "Pune",
    "India",
]

_DEFAULT_DOMAINS = [
    "linkedin.com",
    "naukri.com",
    "indeed.com",
    "in.indeed.com",
]


class TavilySource(BaseJobSource):
    """Tavily-backed discovery for boards without a public API."""

    name = "tavily_search"

    def __init__(
        self,
        *,
        raw_data_dir=None,
        api_key: str | None = None,
        domains: list[str] | None = None,
    ) -> None:
        super().__init__(raw_data_dir=raw_data_dir)
        self._api_key = api_key
        self._domains = domains or _DEFAULT_DOMAINS

    def __enter__(self) -> "TavilySource":
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def discover(self, **kwargs: Any) -> list[RawJobResult]:
        roles = kwargs.get("roles") or ["Software Engineer", "Data Scientist"]
        locations = kwargs.get("locations") or _DEFAULT_LOCATIONS
        count = kwargs.get("count") or 5
        domains = kwargs.get("domains") or self._domains

        if not self._api_key:
            from config.settings import get_settings
            self._api_key = get_settings().tavily_api_key
        if not self._api_key:
            logger.warning("TAVILY_API_KEY missing — tavily source disabled")
            return []

        results: list[RawJobResult] = []
        seen: set[str] = set()
        with TavilyJobScraper(api_key=self._api_key) as scraper:
            for role in roles:
                for location in locations:
                    try:
                        hits = scraper.search_jobs(
                            role, location, count=count, include_domains=domains
                        )
                        for hit in hits:
                            if hit.url in seen:
                                continue
                            seen.add(hit.url)
                            results.append(hit)
                    except Exception as exc:
                        logger.warning(
                            "tavily search failed role=%r location=%r: %s",
                            role, location, exc,
                        )
        return results
