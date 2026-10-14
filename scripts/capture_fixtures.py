"""
scripts/capture_fixtures.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Record real source responses so the contract tests can replay them offline.

    python scripts/capture_fixtures.py                  # every source
    python scripts/capture_fixtures.py --source foundit # just one
    python scripts/capture_fixtures.py --company-map    # company-map importers

Rather than guessing each scraper's request URLs, this wraps
``ScraplingManager.fetch_html_or_json`` and records every call the scraper
actually makes, keyed by URL. That matters for sources like ``ats_platforms``
which fan out across many company boards in one call: the fixture is a map of
URL to response, and the replay harness serves each request from it.

The captures are committed. Refresh them when a source legitimately changes
shape, and review the diff - a fixture that suddenly parses to zero jobs is
the change you wanted to catch.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from pathlib import Path
from typing import Any

from ingestion.scrapling_manager import ScraplingManager
from ingestion.sources.registry import LiveSource, live_source_registry

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sources"

# Board search pages run to 1.5 MB. Committing them raw would add tens of
# megabytes to the repository, so fixtures are stored gzipped.
CAPTURE_QUERY = "Python Developer"
CAPTURE_LOCATION = "Bengaluru"


async def capture_one(source: LiveSource) -> dict[str, Any] | None:
    """Run *source* live, recording every HTTP response it consumed."""
    recorded: dict[str, Any] = {}
    original = ScraplingManager.fetch_html_or_json

    async def recording_fetch(
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 6.0,  # noqa: ASYNC109 - mirrors ScraplingManager.fetch
        impersonate: str | None = None,
        **kwargs: Any,
    ) -> tuple[int, str | dict[str, Any]]:
        kw: dict[str, Any] = {"headers": headers, "params": params, "timeout": timeout}
        if impersonate is not None:
            kw["impersonate"] = impersonate
        status, payload = await original(url, **kw, **kwargs)
        key = _request_key(url, params)
        recorded[key] = {"status": status, "payload": payload}
        return status, payload

    ScraplingManager.fetch_html_or_json = recording_fetch  # type: ignore[method-assign]
    try:
        jobs = await asyncio.wait_for(
            source.fetch(CAPTURE_QUERY, CAPTURE_LOCATION, None),
            timeout=max(source.timeout_seconds * 3, 20.0),
        )
    except Exception as exc:
        print(f"  {source.name:18} capture FAILED: {type(exc).__name__}: {exc}")
        return None
    finally:
        ScraplingManager.fetch_html_or_json = original  # type: ignore[method-assign]

    if not jobs:
        print(f"  {source.name:18} capture produced 0 jobs - not writing a fixture that proves nothing")
        return None

    return {
        "source": source.name,
        "query": CAPTURE_QUERY,
        "location": CAPTURE_LOCATION,
        "expected_min_jobs": max(1, len(jobs) // 2),
        "captured_job_count": len(jobs),
        "requests": recorded,
    }


def _request_key(url: str, params: dict[str, Any] | None) -> str:
    """A stable key for one request, so replay can match it back."""
    if not params:
        return url
    encoded = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{url}?{encoded}"


def fixture_path(name: str) -> Path:
    return FIXTURE_DIR / f"{name}.json.gz"


async def main_async(args: argparse.Namespace) -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    sources = live_source_registry()
    if args.source:
        wanted = set(args.source)
        sources = tuple(s for s in sources if s.name in wanted)
        if not sources:
            print(f"No source matches {args.source}", file=sys.stderr)
            return 2

    print(f"Capturing fixtures into {FIXTURE_DIR}")
    written = 0
    for source in sources:
        if source.requires_browser:
            print(f"  {source.name:18} skipped (needs a browser; not installed)")
            continue
        captured = await capture_one(source)
        if captured is None:
            continue
        path = fixture_path(source.name)
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(captured, handle)
        size_kb = path.stat().st_size / 1024
        print(
            f"  {source.name:18} {captured['captured_job_count']:>3} jobs, "
            f"{len(captured['requests'])} request(s), {size_kb:.0f} KB -> {path.name}"
        )
        written += 1

    print(f"\nWrote {written} fixture(s).")
    return 0 if written else 1


COMPANY_MAP_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "company_map"
_WIKIDATA_KEEP = 40  # companies
_OVERPASS_KEEP = 80  # elements


async def capture_company_map() -> int:
    """
    Re-record the company-map importer fixtures: Wikidata, Overpass and the
    four news feeds, trimmed so they stay small. The people fixtures
    (GitHub, Tavily) are synthetic by design and are not recorded: real
    responses would put real individuals' details in the repository.
    """
    import re

    import httpx

    from services.company_map import osm, wikidata
    from services.company_map.news_feeds import FEEDS, fetch_feed

    COMPANY_MAP_DIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=120.0) as client:
        payload = await wikidata.fetch(client)
        rows = payload.get("results", {}).get("bindings", [])
        keep = list(dict.fromkeys(r["item"]["value"] for r in rows if "item" in r))[:_WIKIDATA_KEEP]
        payload["results"]["bindings"] = [r for r in rows if r.get("item", {}).get("value") in keep]
        (COMPANY_MAP_DIR / "wikidata.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"  wikidata  {len(keep)} companies")

        overpass = await osm.fetch(client)
        overpass["elements"] = overpass.get("elements", [])[:_OVERPASS_KEEP]
        (COMPANY_MAP_DIR / "overpass.json").write_text(json.dumps(overpass, indent=1), encoding="utf-8")
        print(f"  overpass  {len(overpass['elements'])} elements")

        for source in FEEDS:
            xml = await fetch_feed(client, source)
            # Full article bodies are copyrighted and large; the parser never reads them.
            xml = re.sub(rb"<content:encoded>.*?</content:encoded>", b"", xml, flags=re.S)
            (COMPANY_MAP_DIR / f"rss_{source}.xml").write_bytes(xml)
            print(f"  rss_{source:9} {len(xml) // 1024} KB")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Record live source responses as test fixtures.")
    parser.add_argument("--source", action="append", help="Capture only this source (repeatable)")
    parser.add_argument(
        "--company-map", action="store_true", help="Record the company-map importer fixtures"
    )
    args = parser.parse_args()
    if args.company_map:
        return asyncio.run(capture_company_map())
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
