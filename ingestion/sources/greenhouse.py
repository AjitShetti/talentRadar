"""
ingestion/sources/greenhouse.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Greenhouse job board discovery via the public API.

Greenhouse exposes public JSON for company boards at:
    https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

No API key is required for public boards. ``company_tokens`` are the
slug-like identifiers in a company's careers URL, e.g. for
``https://boards.greenhouse.io/stripe`` the token is ``stripe``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from html import unescape
from typing import Any

import httpx

from ingestion.parsers.schemas import RawJobResult
from ingestion.sources.base import BaseJobSource

logger = logging.getLogger(__name__)

_API_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

_DEFAULT_TOKENS: list[str] = [
    # Curated list of well-known Greenhouse-hosted Indian/remote-friendly
    # employers. Override via GREENHOUSE_TOKENS env var (comma-separated).
    "stripe",
    "airbnb",
    "notion",
    "figma",
    "instacart",
    "databricks",
    "zapier",
]


def _html_to_text(html: str | None) -> str:
    """Convert Greenhouse job HTML content into plain text."""
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</(p|div|li|h1|h2|h3|h4|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


class GreenhouseSource(BaseJobSource):
    """Job discovery from Greenhouse-hosted boards."""

    name = "greenhouse"

    def __init__(self, *, raw_data_dir=None, tokens: list[str] | None = None) -> None:
        super().__init__(raw_data_dir=raw_data_dir)
        import os
        env_tokens = os.getenv("GREENHOUSE_TOKENS")
        self._tokens = (
            [t.strip() for t in env_tokens.split(",") if t.strip()]
            if env_tokens else (tokens or _DEFAULT_TOKENS)
        )
        self._client = httpx.Client(timeout=30.0)

    def __enter__(self) -> "GreenhouseSource":
        return self

    def __exit__(self, *_: Any) -> None:
        self._client.close()

    def _fetch_board(self, token: str) -> list[dict[str, Any]]:
        url = _API_URL.format(token=token)
        resp = self._client.get(url, params={"content": "true"})
        resp.raise_for_status()
        data = resp.json()
        return data.get("jobs", [])

    def _to_raw(self, job: dict[str, Any], token: str) -> RawJobResult | None:
        title = job.get("title")
        abs_url = job.get("absolute_url")
        if not title or not abs_url:
            return None
        location = job.get("location") or {}
        loc_str = location.get("name") or "Remote"

        metadata = job.get("metadata", [])
        metadata_str = "; ".join(
            f"{m.get('name')}: {m.get('value')}" for m in metadata
        )

        content_parts = [
            f"Company: {job.get('company_name') or ''}",
            f"Location: {loc_str}",
            f"Employment type: {job.get('employment_type') or 'full_time'}",
            "Department: " + str(job.get("department") or ""),
            metadata_str,
            _html_to_text(job.get("content")),
        ]
        full_text = "\n\n".join(p for p in content_parts if p)

        published = job.get("updated_at") or job.get("first_published")
        published_date = None
        if published:
            try:
                published_date = datetime.fromisoformat(
                    published.replace("Z", "+00:00")
                ).isoformat()
            except Exception:
                published_date = published

        return RawJobResult(
            title=title,
            url=abs_url,
            content=full_text[:500],
            raw_content=full_text,
            score=1.0,
            published_date=published_date,
        )

    def discover(self, **kwargs: Any) -> list[RawJobResult]:
        """Fetch all open jobs across the configured Greenhouse boards."""
        role_filter = kwargs.get("roles")
        results: list[RawJobResult] = []
        for token in self._tokens:
            try:
                jobs = self._fetch_board(token)
                logger.info("greenhouse board %s: %d jobs", token, len(jobs))
                for job in jobs:
                    if role_filter and not any(
                        r.lower() in job.get("title", "").lower() for r in role_filter
                    ):
                        continue
                    raw = self._to_raw(job, token)
                    if raw:
                        results.append(raw)
            except httpx.HTTPStatusError as exc:
                # 404 = board doesn't exist; keep going with the rest.
                logger.warning("greenhouse board %s failed: %s", token, exc)
            except Exception as exc:
                logger.warning("greenhouse board %s error: %s", token, exc)
        return results
