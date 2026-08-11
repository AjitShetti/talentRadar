"""
ingestion/sources/base.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Base interface for job-discovery sources.

Every source exposes:
    name                  — stable source identifier (stored in jobs.source)
    discover(**kwargs)    — yields RawJobResult objects
    save_raw(...)         — optional persistence of raw payloads
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ingestion.parsers.schemas import RawJobResult


class JobSource(Protocol):
    """Protocol describing a job-discovery source."""

    name: str

    def discover(self, **kwargs: Any) -> list[RawJobResult]:
        """Fetch raw job results for the given query parameters."""
        ...

    def save_raw(
        self,
        results: list[RawJobResult],
        *,
        run_id: str,
        role: str = "unknown",
        location: str = "unknown",
    ) -> list[Path]:
        """Persist raw results to disk for audit / replay. May be a no-op."""
        ...


class BaseJobSource:
    """Shared helpers for concrete sources."""

    name: str = "base"
    _raw_dir: Path

    def __init__(self, raw_data_dir: str | Path | None = None) -> None:
        self._raw_dir = Path(raw_data_dir or "data/raw") / self.name

    def save_raw(
        self,
        results: list[RawJobResult],
        *,
        run_id: str,
        role: str = "unknown",
        location: str = "unknown",
    ) -> list[Path]:
        import hashlib
        import json
        from datetime import datetime, timezone

        from ingestion.scrapers.tavily_client import _slugify

        dest = self._raw_dir / run_id / _slugify(f"{role}_{location}")
        dest.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for result in results:
            url_hash = hashlib.md5(result.url.encode()).hexdigest()[:12]
            fp = dest / f"{url_hash}.json"
            payload = {
                "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
                "source": self.name,
                "query_role": role,
                "query_location": location,
                "run_id": run_id,
                **result.model_dump(),
            }
            fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(fp)
        return written
