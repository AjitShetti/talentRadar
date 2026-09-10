"""
scripts/verify_sources.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Hit every registered live source for real and report what came back.

This is the ground truth for "which scrapers actually work". Contract tests
replay recorded fixtures and so keep passing when a site changes underneath
us; this script goes to the network and tells you the truth about today.

    make verify-sources
    python scripts/verify_sources.py --query "Python Developer" --location Bengaluru

Exits non-zero if any source in the roster returned zero jobs, so it can gate
a release or run from CI on a schedule.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass

from config.settings import get_settings
from domain.entities import Job
from ingestion.sources.registry import LiveSource, default_live_sources


@dataclass
class ProbeResult:
    source: str
    tier: str
    count: int
    latency_ms: float
    status: str
    sample_title: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


async def probe(source: LiveSource, query: str, location: str | None, is_remote: bool | None) -> ProbeResult:
    """Run one source with its own timeout and never raise."""
    start = time.perf_counter()
    try:
        jobs: list[Job] = await asyncio.wait_for(
            source.fetch(query, location, is_remote),
            timeout=source.timeout_seconds,
        )
    except asyncio.TimeoutError:
        return ProbeResult(
            source.name,
            source.tier,
            0,
            round((time.perf_counter() - start) * 1000, 1),
            "timeout",
            detail=f"exceeded {source.timeout_seconds}s",
        )
    except Exception as exc:  # noqa: BLE001 - a probe reports failures, never propagates them
        return ProbeResult(
            source.name,
            source.tier,
            0,
            round((time.perf_counter() - start) * 1000, 1),
            "error",
            detail=f"{type(exc).__name__}: {exc}"[:120],
        )

    latency_ms = round((time.perf_counter() - start) * 1000, 1)
    jobs = jobs or []
    if not jobs:
        # A source that returns an empty list without raising is the failure
        # mode this whole script exists to catch: the site changed, the
        # parser silently matched nothing, and every dashboard still says
        # "success".
        return ProbeResult(source.name, source.tier, 0, latency_ms, "empty")

    return ProbeResult(
        source.name,
        source.tier,
        len(jobs),
        latency_ms,
        "ok",
        sample_title=(jobs[0].title or "")[:48],
    )


def render(results: list[ProbeResult], query: str, location: str | None) -> None:
    symbols = {"ok": "PASS", "empty": "EMPTY", "timeout": "TIMEOUT", "error": "ERROR"}

    print()
    print(f"  Live source verification - query={query!r} location={location!r}")
    print("  " + "-" * 88)
    print(f"  {'SOURCE':<18}{'TIER':<9}{'STATUS':<10}{'JOBS':>6}{'LATENCY':>11}   SAMPLE / DETAIL")
    print("  " + "-" * 88)

    for r in sorted(results, key=lambda x: (not x.ok, x.source)):
        note = r.sample_title if r.ok else r.detail
        print(
            f"  {r.source:<18}{r.tier:<9}{symbols[r.status]:<10}"
            f"{r.count:>6}{r.latency_ms:>9.0f}ms   {note}"
        )

    print("  " + "-" * 88)
    working = [r for r in results if r.ok]
    total_jobs = sum(r.count for r in results)
    print(f"  {len(working)}/{len(results)} sources returned jobs; {total_jobs} postings total")

    broken = [r for r in results if not r.ok]
    if broken:
        print()
        print("  Not returning jobs: " + ", ".join(f"{r.source} ({r.status})" for r in broken))
    print()


async def main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    sources = default_live_sources(enable_stealth=settings.enable_stealth_scrapers)

    if args.source:
        sources = tuple(s for s in sources if s.name in set(args.source))
        if not sources:
            print(f"No source in the current roster matches {args.source}", file=sys.stderr)
            return 2

    results = await asyncio.gather(
        *(probe(s, args.query, args.location, args.remote) for s in sources)
    )
    render(list(results), args.query, args.location)

    return 0 if all(r.ok for r in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify every live job source against the real internet.")
    parser.add_argument("--query", default="Python Developer", help="Role or skill keywords")
    parser.add_argument("--location", default="Bengaluru", help="Target city or country")
    parser.add_argument("--remote", action="store_true", default=None, help="Probe remote-only listings")
    parser.add_argument("--source", action="append", help="Probe only this source (repeatable)")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
