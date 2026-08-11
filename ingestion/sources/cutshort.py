"""
ingestion/sources/cutshort.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Cutshort job discovery.

Cutshort (India's curated startup hiring platform) does not offer a stable
public JSON API for all job listings. We discover ``cutshort.io`` postings
via Tavily search restricted to the cutshort.io domain — matching the
"Tavily as discovery fallback for Indian users" decision.

The raw page content is passed through the shared JDParser, so structured
fields (skills, salary LPA, experience, remote) are still extracted for the
jobs table.
"""

from __future__ import annotations

import logging
from typing import Any

from ingestion.parsers.schemas import RawJobResult
from ingestion.scrapers.tavily_client import TavilyJobScraper
from ingestion.sources.base import BaseJobSource

logger = logging.getLogger(__name__)

_DEFAULT_LOCATIONS = ["Bangalore", "Mumbai", "Delhi NCR", "Pune", "Remote"]


class CutshortSource(BaseJobSource):
    """Job discovery from Cutshort via Tavily domain-scoped search."""

    name = "cutshort"

    def __init__(self, *, raw_data_dir=None, api_key: str | None = None) -> None:
        super().__init__(raw_data_dir=raw_data_dir)
        self._api_key = api_key
        self._max_per_query = 10

    def __enter__(self) -> "CutshortSource":
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def discover(self, **kwargs: Any) -> list[RawJobResult]:
        roles = kwargs.get("roles") or ["Software Engineer", "Data Scientist"]
        locations = kwargs.get("locations") or _DEFAULT_LOCATIONS
        count = kwargs.get("count") or self._max_per_query

        if not self._api_key:
            from config.settings import get_settings
            self._api_key = get_settings().tavily_api_key
        if not self._api_key:
            logger.warning("TAVILY_API_KEY missing — cutshort source disabled")
            return []

        results: list[RawJobResult] = []
        seen: set[str] = set()
        with TavilyJobScraper(api_key=self._api_key) as scraper:
            for role in roles:
                for location in locations:
                    try:
                        hits = scraper.search(
                            f"{role} job openings in {location} hiring",
                            max_results=count,
                            include_domains=["cutshort.io"],
                        )
                        for hit in hits:
                            if hit.url in seen:
                                continue
                            seen.add(hit.url)
                            results.append(hit)
                    except Exception as exc:
                        logger.warning(
                            "cutshort discovery failed role=%r location=%r: %s",
                            role, location, exc,
                        )
        return results
