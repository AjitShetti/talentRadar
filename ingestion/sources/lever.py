"""
ingestion/sources/lever.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Lever job board discovery via the public JSON API.

Lever exposes public postings at:
    https://api.lever.co/v0/postings/{company}?mode=json
    https://api.lever.co/v0/postings/{company}/{posting_id}

No API key is required. ``company`` is the slug in a company's Lever careers
URL, e.g. for ``https://jobs.lever.co/dropbox`` the slug is ``dropbox``.
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

_BOARD_URL = "https://api.lever.co/v0/postings/{company}"
_POSTING_URL = "https://api.lever.co/v0/postings/{company}/{posting_id}"

_DEFAULT_COMPANIES: list[str] = [
    "dropbox",
    "box",
    "pinterest",
    "atlassian",
    "stripe",
    "airtable",
    "webflow",
    "gong",
]


def _html_to_text(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</(p|div|li|h1|h2|h3|h4|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


class LeverSource(BaseJobSource):
    """Job discovery from Lever-hosted boards."""

    name = "lever"

    def __init__(self, *, raw_data_dir=None, companies: list[str] | None = None) -> None:
        super().__init__(raw_data_dir=raw_data_dir)
        import os
        env_companies = os.getenv("LEVER_COMPANIES")
        self._companies = (
            [c.strip() for c in env_companies.split(",") if c.strip()]
            if env_companies else (companies or _DEFAULT_COMPANIES)
        )
        self._client = httpx.Client(timeout=30.0)

    def __enter__(self) -> "LeverSource":
        return self

    def __exit__(self, *_: Any) -> None:
        self._client.close()

    def _fetch_posting(self, company: str, posting_id: str) -> dict[str, Any]:
        resp = self._client.get(_POSTING_URL.format(company=company, posting_id=posting_id))
        resp.raise_for_status()
        return resp.json()

    def _to_raw(self, posting: dict[str, Any]) -> RawJobResult | None:
        title = posting.get("text")
        abs_url = posting.get("hostedUrl")
        if not title or not abs_url:
            return None

        categories = posting.get("categories") or {}
        workplace = posting.get("workplaceType") or ""
        remote = posting.get("workplaceType") in (
            "remote", "hybrid", "Remote", "Hybrid"
        ) or "remote" in str(workplace).lower()

        lists = posting.get("lists", [])
        additional = []
        for section in lists:
            name = section.get("text")
            content = section.get("content")
            if content:
                additional.append(f"{name}:\n{_html_to_text(content)}")

        parts = [
            f"Company: {posting.get('team') or ''}",
            f"Location: {posting.get('categories', {}).get('location') or 'Remote'}",
            f"Workplace type: {workplace}",
            f"Employment type: {categories.get('commitment') or 'Full-time'}",
            f"Department: {categories.get('team') or ''}",
            _html_to_text(posting.get("description")),
        ]
        parts.extend(additional)
        full_text = "\n\n".join(p for p in parts if p)

        published = posting.get("createdAt")
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
        role_filter = kwargs.get("roles")
        results: list[RawJobResult] = []
        for company in self._companies:
            try:
                resp = self._client.get(
                    _BOARD_URL.format(company=company), params={"mode": "json"}
                )
                resp.raise_for_status()
                postings = resp.json()
                logger.info("lever board %s: %d postings", company, len(postings))
                for posting in postings:
                    if role_filter and not any(
                        r.lower() in posting.get("text", "").lower() for r in role_filter
                    ):
                        continue
                    raw = self._to_raw(posting)
                    if raw:
                        results.append(raw)
            except Exception as exc:
                logger.warning("lever board %s failed: %s", company, exc)
        return results
