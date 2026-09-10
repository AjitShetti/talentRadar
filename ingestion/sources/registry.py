"""
ingestion/sources/registry.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The single list of live scrapers.

Three consumers need to agree on what "the sources" are, and before this
module existed they each hardcoded their own list:

* ``ingestion/engine.py`` built the fan-out inline
* the health service needs a stable key per source
* the fixture contract tests need to prove every source is covered

Any of those drifting from the others is a silent gap - a source nobody
tests, or health counters for a source that no longer runs. They all read
this registry instead.

Sources are described, not executed. ``fetch`` is stored as a callable and
built lazily by the caller, because an un-awaited coroutine created at import
time is both a warning and a leak.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from domain.entities import Job

# (query, location, is_remote) -> jobs. Every source shares this signature so
# the fan-out can treat an ATS JSON API and a board scraper identically;
# tests/test_source_registry.py enforces it.
FetchCallable = Callable[[str, str | None, bool | None], Awaitable[list[Job]]]

SourceTier = Literal["ats", "board", "stealth"]


@dataclass(frozen=True)
class LiveSource:
    """One scrapeable source of job postings."""

    name: str
    fetch: FetchCallable
    tier: SourceTier
    timeout_seconds: float
    # True when the source can only be reached by launching a real browser.
    # The deployed instance has 512 MB and cannot, so this is what
    # ``default_live_sources`` filters on - the fan-out never needs to know
    # which individual scraper is which.
    requires_browser: bool = False
    # Basename of the recorded response under tests/fixtures/sources/. The
    # contract tests assert every registered source has one, so a source
    # cannot be added without a test.
    fixture: str | None = None


def _build_registry() -> tuple[LiveSource, ...]:
    """Construct the registry.

    Imports are deferred into this function: the scraper modules pull in
    httpx and (optionally) curl_cffi, and importing them at module scope
    makes the registry unusable from any context that only wants to read
    source metadata.
    """
    from ingestion.scrapers.ats_scraper import ATSScraper
    from ingestion.scrapers.indian_boards_scraper import IndianBoardsScraper
    from ingestion.scrapers.stealth_boards_scraper import StealthBoardsScraper

    return (
        # --- ATS JSON APIs: public, documented, and the highest-quality rows
        # we get. No impersonation needed, so these are the sources that keep
        # working when everything else is blocked.
        LiveSource(
            name="ats_platforms",
            fetch=ATSScraper.search_all_ats,
            tier="ats",
            timeout_seconds=5.0,
            fixture="ats_greenhouse.json",
        ),
        # --- Public board endpoints reachable over plain HTTP. LinkedIn's
        # guest endpoint and Indeed's mobile JSON both sit behind Cloudflare,
        # which is what the curl_cffi impersonation tier in
        # ingestion/scrapling_manager.py is for.
        LiveSource(
            name="linkedin",
            fetch=IndianBoardsScraper.search_linkedin_guest,
            tier="board",
            timeout_seconds=5.0,
            fixture="linkedin_guest.html",
        ),
        LiveSource(
            name="foundit",
            fetch=IndianBoardsScraper.search_foundit_india,
            tier="board",
            timeout_seconds=5.0,
            fixture="foundit.json",
        ),
        LiveSource(
            name="freshersworld",
            fetch=IndianBoardsScraper.search_freshersworld,
            tier="board",
            timeout_seconds=4.0,
            fixture="freshersworld.html",
        ),
        LiveSource(
            name="indeed_india",
            fetch=StealthBoardsScraper.search_indeed_india,
            tier="board",
            timeout_seconds=7.0,
            fixture="indeed_india.html",
        ),
        LiveSource(
            name="instahyre",
            fetch=StealthBoardsScraper.search_instahyre,
            tier="board",
            timeout_seconds=5.0,
            fixture="instahyre.json",
        ),
        # --- Browser-only. Naukri hydrates its listings client-side, so
        # there is no HTML to parse without running JS. Excluded from the
        # default roster and uninstalled in the deployed image; kept
        # registered so the upgrade path stays tested and documented.
        LiveSource(
            name="naukri",
            fetch=StealthBoardsScraper.search_naukri,
            tier="stealth",
            timeout_seconds=10.0,
            requires_browser=True,
            fixture="naukri.html",
        ),
    )


_REGISTRY: tuple[LiveSource, ...] | None = None


def live_source_registry() -> tuple[LiveSource, ...]:
    """Every known source, browser-requiring ones included."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def default_live_sources(*, enable_stealth: bool = False) -> tuple[LiveSource, ...]:
    """
    The roster to fan out across.

    Browser-requiring sources are excluded unless ``enable_stealth`` is set.
    The default is the free-tier shape: no browser, so nothing here can OOM a
    512 MB instance.
    """
    if enable_stealth:
        return live_source_registry()
    return tuple(s for s in live_source_registry() if not s.requires_browser)


def get_live_source(name: str) -> LiveSource:
    """Look one source up by name. Raises ``KeyError`` if it is not registered."""
    for source in live_source_registry():
        if source.name == name:
            return source
    known = ", ".join(s.name for s in live_source_registry())
    raise KeyError(f"unknown live source {name!r}; registered: {known}")
