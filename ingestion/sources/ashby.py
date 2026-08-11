"""
ingestion/sources/ashby.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Ashby job board discovery via the public JSON API.

Ashby exposes public postings at:
    https://api.ashbyhq.com/posting-api/job-board/{org}

The response is a JSON object with a ``jobs`` array. ``org`` is the slug in
a company's Ashby careers URL, e.g. for ``https://jobs.ashbyhq.com/linear``
the org is ``linear``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any

import httpx

from ingestion.parsers.schemas import RawJobResult
from ingestion.sources.base import BaseJobSource

logger = logging.getLogger(__name__)

_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{org}"

_DEFAULT_ORGS: list[str] = [
    "linear",
    "notion",
    "ramp",
    "retool",
    "brex",
    "deel",
    "stripe",
    "raster",
]


def _html_to_text(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</(p|div|li|h1|h2|h3|h4|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


class AshbySource(BaseJobSource):
    """Job discovery from Ashby-hosted boards."""

    name = "ashby"

    def __init__(self, *, raw_data_dir=None, orgs: list[str] | None = None) -> None:
        super().__init__(raw_data_dir=raw_data_dir)
        import os
        env_orgs = os.getenv("ASHBY_ORGS")
        self._orgs = (
            [o.strip() for o in env_orgs.split(",") if o.strip()]
            if env_orgs else (orgs or _DEFAULT_ORGS)
        )
        self._client = httpx.Client(timeout=30.0)

    def __enter__(self) -> "AshbySource":
        return self

    def __exit__(self, *_: Any) -> None:
        self._client.close()

    def _fetch_org(self, org: str) -> list[dict[str, Any]]:
        resp = self._client.get(
            _BOARD_URL.format(org=org),
            params={"includeCompensation": "true"},
        )
        resp.raise_for_status()
        return resp.json().get("jobs", [])

    def _to_raw(self, job: dict[str, Any]) -> RawJobResult | None:
        title = job.get("title")
        abs_url = job.get("jobUrl") or job.get("applyUrl")
        if not title or not abs_url:
            return None

        location = job.get("location") or "Remote"
        remote = job.get("isRemote", False)
        published = job.get("publishedAt")
        published_date = None
        if published:
            try:
                published_date = datetime.fromisoformat(
                    published.replace("Z", "+00:00")
                ).isoformat()
            except Exception:
                published_date = published

        description_html = job.get("descriptionHtml") or ""
        parts = [
            f"Company: {job.get('company') or ''}",
            f"Location: {location}",
            f"Remote: {remote}",
            f"Employment type: {job.get('employmentType') or 'Full time'}",
            f"Department: {job.get('department') or ''}",
            f"Team: {job.get('team') or ''}",
            "Compensation: "
            + "; ".join(
                c.get("compensationTierSummary", "")
                for c in (job.get("compensation") or [])
                if c.get("compensationTierSummary")
            ),
            _html_to_text(description_html),
        ]
        full_text = "\n\n".join(p for p in parts if p)

        return RawJobResult(
            title=title,
            url=abs_url,
            content=full_text[:500],
            raw_content=full_text,
            score=1.0,
            published_date=published_date,
        )

    def discover(self, **kwargs: Any) -> list[RawJobResult]:
        role_filter = kwargs.get("roles")
        results: list[RawJobResult] = []
        for org in self._orgs:
            try:
                jobs = self._fetch_org(org)
                logger.info("ashby board %s: %d jobs", org, len(jobs))
                for job in jobs:
                    if role_filter and not any(
                        r.lower() in job.get("title", "").lower() for r in role_filter
                    ):
                        continue
                    raw = self._to_raw(job)
                    if raw:
                        results.append(raw)
            except Exception as exc:
                logger.warning("ashby board %s failed: %s", org, exc)
        return results
