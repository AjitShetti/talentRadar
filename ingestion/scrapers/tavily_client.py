"""
ingestion/scrapers/tavily_client.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tavily-powered job description collector.

Responsibilities
----------------
1. ``TavilyJobScraper.search_jobs()``  — issue structured Tavily queries that
   target real job postings and return validated ``RawJobResult`` objects.
2. ``TavilyJobScraper.save_raw()``     — persist the raw JSON to the local
   filesystem under ``/data/raw/{run_id}/`` so every ingestion run is
   reproducible and auditable.

The raw files are written **before** any LLM parsing happens so that even a
parser crash doesn't lose the source data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import get_settings
from ingestion.parsers.schemas import RawJobResult

logger = logging.getLogger(__name__)

# ─── Default raw-data directory (override via env RAW_DATA_DIR) ───────────────
_DEFAULT_RAW_DIR = Path(os.getenv("RAW_DATA_DIR", "/data/raw"))

# ─── Tavily API base URL ──────────────────────────────────────────────────────
_TAVILY_BASE_URL = "https://api.tavily.com"

# ─── Job-focused search query template ───────────────────────────────────────
_QUERY_TEMPLATE = (
    '"{role}" job description {location} requirements skills responsibilities '
    "site:linkedin.com OR site:greenhouse.io OR site:lever.co OR site:indeed.com"
)


class TavilyJobScraper:
    """
    Wrapper around the Tavily Search API for collecting raw job descriptions.

    Parameters
    ----------
    api_key:
        Tavily API key. Defaults to ``settings.tavily_api_key``.
    raw_data_dir:
        Root directory under which raw JSON files are saved.
        Defaults to ``/data/raw``.
    timeout:
        HTTP request timeout in seconds.

    Example
    -------
    ::

        scraper = TavilyJobScraper()
        results = scraper.search_jobs("Machine Learning Engineer", "Remote", count=10)
        paths = scraper.save_raw(results, run_id="dag-run-2024-01-15")
    """

    def __init__(
        self,
        api_key: str | None = None,
        raw_data_dir: Path | str | None = None,
        timeout: float = 30.0,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.tavily_api_key
        if not self._api_key:
            raise ValueError(
                "TAVILY_API_KEY is not set. "
                "Add it to your .env file or pass it explicitly."
            )
        self._raw_dir = Path(raw_data_dir or _DEFAULT_RAW_DIR)
        self._timeout = timeout
        self._client = httpx.Client(
            base_url=_TAVILY_BASE_URL,
            timeout=self._timeout,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        include_raw_content: bool = True,
        search_depth: str = "advanced",
    ) -> list[RawJobResult]:
        """
        Issue a single Tavily search and return validated ``RawJobResult`` objects.

        Parameters
        ----------
        query:
            Free-text search query.
        max_results:
            Maximum number of results to request (Tavily cap: 20).
        include_raw_content:
            When True, Tavily returns the full page text (not just a snippet).
            This gives the LLM parser much more signal and is strongly recommended.
        search_depth:
            ``"basic"`` (fast) or ``"advanced"`` (more accurate, slower).

        Returns
        -------
        list[RawJobResult]
            Validated results; malformed/empty items are silently skipped.
        """
        payload = {
            "api_key": self._api_key,
            "query": query,
            "max_results": min(max_results, 20),
            "include_raw_content": include_raw_content,
            "search_depth": search_depth,
            "include_answer": False,
            "include_images": False,
        }

        try:
            raw_data = self._call_tavily(payload)
        except Exception as exc:
            logger.error("Tavily search failed for query=%r: %s", query, exc)
            raise

        results: list[RawJobResult] = []
        for item in raw_data.get("results", []):
            try:
                result = RawJobResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    content=item.get("content", ""),
                    score=float(item.get("score", 0.0)),
                    published_date=item.get("published_date"),
                    raw_content=item.get("raw_content"),
                )
                results.append(result)
            except Exception as exc:
                logger.warning(
                    "Skipping malformed Tavily result url=%r: %s",
                    item.get("url"),
                    exc,
                )

        logger.info(
            "Tavily search returned %d valid results for query=%r",
            len(results),
            query,
        )
        return results

    def search_jobs(
        self,
        role: str,
        location: str = "Remote",
        *,
        count: int = 10,
    ) -> list[RawJobResult]:
        """
        High-level helper: build a JD-focused query and call ``search()``.

        The query is deliberately crafted to surface actual job postings
        (not generic articles) by targeting known job-board domains and
        including role-specific keywords.

        Parameters
        ----------
        role:
            Job title to search for, e.g. "Senior Data Scientist".
        location:
            Geographic filter, e.g. "San Francisco", "Remote", "India".
        count:
            Number of results to fetch.

        Returns
        -------
        list[RawJobResult]
        """
        query = _QUERY_TEMPLATE.format(role=role, location=location)
        logger.info("Searching jobs: role=%r location=%r count=%d", role, location, count)
        return self.search(query, max_results=count, include_raw_content=True)

    def save_raw(
        self,
        results: list[RawJobResult],
        *,
        run_id: str,
        role: str = "unknown",
        location: str = "unknown",
    ) -> list[Path]:
        """
        Persist each ``RawJobResult`` as a JSON file under:
        ``{raw_data_dir}/{run_id}/{role}_{location}/{hash}.json``

        Returns
        -------
        list[Path]
            Absolute paths to the files written.
        """
        slug = _slugify(f"{role}_{location}")
        dest_dir = self._raw_dir / run_id / slug
        dest_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for result in results:
            # Use a content hash as filename so identical pages are deduplicated
            content_hash = hashlib.md5(result.url.encode()).hexdigest()[:12]
            file_path = dest_dir / f"{content_hash}.json"

            payload: dict[str, Any] = {
                "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
                "query_role": role,
                "query_location": location,
                "run_id": run_id,
                **result.model_dump(),
            }

            file_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            written.append(file_path)
            logger.debug("Saved raw result → %s", file_path)

        logger.info(
            "Saved %d raw results to %s (run_id=%s, role=%r, location=%r)",
            len(written),
            dest_dir,
            run_id,
            role,
            location,
        )
        return written

    def load_raw(self, file_path: Path | str) -> dict[str, Any]:
        """Load a previously saved raw JSON file back into a dict."""
        path = Path(file_path)
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    def _call_tavily(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        POST to Tavily /search with automatic retries on transient errors.
        Raises ``httpx.HTTPStatusError`` for 4xx/5xx responses.
        """
        resp = self._client.post("/search", json=payload)
        resp.raise_for_status()
        return resp.json()

    def __enter__(self) -> "TavilyJobScraper":
        return self

    def __exit__(self, *_: Any) -> None:
        self._client.close()

    def close(self) -> None:
        """Explicitly close the underlying HTTP client."""
        self._client.close()


# ─────────────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Convert arbitrary text to a filesystem-safe slug."""
    import re
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:64]
