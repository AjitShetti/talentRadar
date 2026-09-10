"""
agents/sourcing_policy.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Decide whether a query should go out to the internet.

This is deliberately **not** an LLM call and not a heuristic buried in a node.
It is the single place that answers one question - "is the index good enough
for this query, or do we scrape?" - and it is a pure function of its inputs so
every branch can be tested without a network, a database, or a model.

The decision matters in both directions:

* scraping too rarely means the product only ever knows what it already knew
* scraping too often means 5-10 seconds on every search, an ~60-request burst
  at the boards per query, and a deployment IP that gets blocked

The lock is the important half. ``already_sourced`` reflects a Redis key set
for 8 hours after any live fan-out for that query, and it wins over every
other signal. Without it, one popular query re-scrapes on every page load and
the boards notice.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: How long after a live fan-out the same query is served from the index.
SOURCED_LOCK_TTL_SECONDS = 8 * 3600
SOURCED_KEY_PREFIX = "tr:sourced:"

#: Below this many indexed hits, the index is treated as too thin to answer.
MIN_INDEXED_RESULTS = 8

#: Indexed hits older than this make the answer stale even when there are many.
STALE_AFTER_DAYS = 14

#: Phrasings that explicitly ask for what is new. Cheap to check, and a direct
#: statement of intent beats any inference from result counts.
FRESHNESS_PATTERNS = (
    r"\blatest\b",
    r"\bnewest\b",
    r"\bnew(ly)?\s+(posted|listed|added)\b",
    r"\brecent(ly)?\b",
    r"\bthis\s+(week|month)\b",
    r"\btoday\b",
    r"\bfresh\b",
    r"\bjust\s+posted\b",
    r"\bright\s+now\b",
)

_FRESHNESS_RE = re.compile("|".join(FRESHNESS_PATTERNS), re.IGNORECASE)


def query_fingerprint(query: str, location: str | None, is_remote: bool | None) -> str:
    """
    A stable key for "this search", used for the 8-hour lock.

    Normalised so that spacing and casing do not open a second window onto the
    same search.
    """
    normalised = " ".join((query or "").lower().split())
    loc = " ".join((location or "").lower().split())
    raw = f"{normalised}|{loc}|{bool(is_remote)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def sourced_lock_key(query: str, location: str | None, is_remote: bool | None) -> str:
    return f"{SOURCED_KEY_PREFIX}{query_fingerprint(query, location, is_remote)}"


def wants_fresh_results(query: str) -> bool:
    """True when the query explicitly asks for recent postings."""
    return bool(_FRESHNESS_RE.search(query or ""))


@dataclass(frozen=True)
class SourcingDecision:
    """Whether to scrape, and why. The reason is surfaced to the API."""

    should_source: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {"sourced": self.should_source, "reason": self.reason}


def decide_sourcing(
    *,
    query: str,
    indexed_count: int,
    newest_indexed_at: datetime | None = None,
    already_sourced: bool = False,
    force_refresh: bool = False,
    now: datetime | None = None,
) -> SourcingDecision:
    """
    Decide whether this query warrants a live fan-out.

    ``already_sourced`` is checked before everything except an explicit
    ``force_refresh``: it is the guard that stops a popular query from
    re-scraping the boards on every page load.
    """
    if force_refresh:
        return SourcingDecision(True, "caller requested a refresh")

    if already_sourced:
        return SourcingDecision(
            False,
            "this search was already sourced live within the last 8 hours",
        )

    if indexed_count < MIN_INDEXED_RESULTS:
        return SourcingDecision(
            True,
            f"only {indexed_count} indexed result(s), below the {MIN_INDEXED_RESULTS} needed",
        )

    if wants_fresh_results(query):
        return SourcingDecision(True, "the query asks for recent postings")

    if newest_indexed_at is not None:
        reference = now or datetime.now(timezone.utc)
        # A naive timestamp from the database is treated as UTC rather than
        # rejected; the alternative is a TypeError on the comparison below.
        newest = (
            newest_indexed_at
            if newest_indexed_at.tzinfo is not None
            else newest_indexed_at.replace(tzinfo=timezone.utc)
        )
        age = reference - newest
        if age > timedelta(days=STALE_AFTER_DAYS):
            return SourcingDecision(
                True,
                f"freshest indexed result is {age.days} days old",
            )

    return SourcingDecision(False, f"{indexed_count} fresh indexed results already answer this")
