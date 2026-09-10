"""
agents/merge_rank.py
~~~~~~~~~~~~~~~~~~~~
Combine indexed results and live-scraped results into one ranked list.

This module performs **no I/O**. It takes two lists of job dicts and returns
one, which is the whole reason it is separate from the graph node that calls
it: ranking is the logic most likely to be wrong and least pleasant to debug
through a database and six scrapers, so it is a pure function with a test per
rule.

Ranking blends four signals, deliberately simple and explainable:

* **relevance** - query terms matched in the title, then in the skills
* **recency** - a posting from yesterday beats one from six weeks ago
* **source trust** - an ATS posting is first-party and complete; a board
  aggregate is neither
* **completeness** - a row with skills and a salary is more useful than a stub

There is no learned model here on purpose. A scorer that needs training data
would need to be retrained to stay honest, and nothing in the free-tier
deployment can do that.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

#: How much each signal can contribute. They sum to 1.0.
WEIGHT_RELEVANCE = 0.50
WEIGHT_RECENCY = 0.25
WEIGHT_SOURCE = 0.15
WEIGHT_COMPLETENESS = 0.10

#: First-party postings are more trustworthy and more complete than aggregated
#: ones: an ATS row comes from the employer, a board row from a scraper.
SOURCE_TRUST = {
    "greenhouse": 1.0,
    "lever": 1.0,
    "ashby": 1.0,
    "ats_platforms": 1.0,
    "linkedin": 0.8,
    "instahyre": 0.75,
    "indeed_india": 0.7,
    "foundit": 0.65,
    "naukri": 0.65,
    "cutshort": 0.6,
    "freshersworld": 0.5,
    "tavily": 0.4,
}
DEFAULT_SOURCE_TRUST = 0.5

#: A posting stops gaining from recency past this age.
RECENCY_HORIZON_DAYS = 45


def _terms(query: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9+#.]+", (query or "").lower()) if len(t) > 2]


def _city_of(job: dict[str, Any]) -> str:
    """
    The city component of the dedup key.

    The two sides carry location differently: an indexed row has ``location``
    ("Bengaluru, India"), a live row has ``city``. Reading only ``city`` made
    every indexed row hash with an empty city, so the same posting from both
    sides never matched and duplicates reached the user.
    """
    city = (job.get("city") or "").strip()
    if not city:
        city = (job.get("location") or job.get("location_raw") or "").split(",")[0].strip()
    return city.lower()


def dedup_key(job: dict[str, Any]) -> str:
    """
    Identify the same posting across sources.

    Matches ``ingestion.engine.compute_job_dedup_hash``: the same role at the
    same company in the same city is one job, however many boards list it.
    """
    company = (job.get("company_name") or job.get("company") or "").strip().lower()
    title = (job.get("title") or "").strip().lower()
    return hashlib.md5(f"{company}:{title}:{_city_of(job)}".encode("utf-8")).hexdigest()


def to_retrieval_dict(job: dict[str, Any]) -> dict[str, Any]:
    """
    Normalise a merged row onto the shape the orchestrator re-hydrates.

    Indexed rows arrive as ``RetrievalResult`` dicts (``job_id``, ``company``)
    and live rows as ``job_to_dict`` output (``id``, ``company_name``). The
    orchestrator indexes ``r["job_id"]`` directly, so a live-only row without
    that key raises KeyError and fails the whole request.
    """
    return {
        "job_id": job.get("job_id") or job.get("id") or "",
        "title": job.get("title") or "",
        "company": job.get("company") or job.get("company_name") or "",
        "location": job.get("location") or job.get("location_raw") or job.get("city"),
        "is_remote": bool(job.get("is_remote", False)),
        "skills": job.get("skills") or [],
        # The blended rank is the score the user's ordering reflects, so it is
        # what the API should report; the raw vector distance is kept for
        # anything that wants it.
        "score": job.get("rank_score", job.get("score", 0.0)),
        "vector_score": job.get("score"),
        "match_reason": job.get("match_reason"),
        "source_url": job.get("source_url"),
        "source": job.get("source"),
        "posted_at": job.get("posted_at"),
        "salary_raw": job.get("salary_raw"),
        "is_live": bool(job.get("is_live", False)),
        "also_seen_live": bool(job.get("also_seen_live", False)),
    }


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def relevance_score(job: dict[str, Any], query: str) -> float:
    """Fraction of query terms present, title weighted above skills."""
    terms = _terms(query)
    if not terms:
        return 0.5

    title = (job.get("title") or "").lower()
    skills = " ".join(job.get("skills") or []).lower()

    hits = 0.0
    for term in terms:
        if term in title:
            hits += 1.0
        elif term in skills:
            hits += 0.6
    return min(hits / len(terms), 1.0)


def recency_score(job: dict[str, Any], now: datetime | None = None) -> float:
    """1.0 for a posting from today, decaying to 0.0 at the horizon."""
    posted = _parse_dt(job.get("posted_at")) or _parse_dt(job.get("created_at"))
    if posted is None:
        # Unknown age scores mid, not zero: most scraped rows carry no real
        # posting date, and zeroing them would bury every live result.
        return 0.5
    age_days = ((now or datetime.now(timezone.utc)) - posted).days
    if age_days <= 0:
        return 1.0
    if age_days >= RECENCY_HORIZON_DAYS:
        return 0.0
    return 1.0 - (age_days / RECENCY_HORIZON_DAYS)


def source_score(job: dict[str, Any]) -> float:
    return SOURCE_TRUST.get((job.get("source") or "").lower(), DEFAULT_SOURCE_TRUST)


def completeness_score(job: dict[str, Any]) -> float:
    """Reward rows a user can actually act on."""
    present = 0
    if job.get("skills"):
        present += 1
    if job.get("salary_raw") or job.get("salary_min"):
        present += 1
    if job.get("description_clean"):
        present += 1
    if job.get("source_url"):
        present += 1
    return present / 4.0


def score_job(job: dict[str, Any], query: str, now: datetime | None = None) -> float:
    """The blended rank score for one job, in [0, 1]."""
    return round(
        WEIGHT_RELEVANCE * relevance_score(job, query)
        + WEIGHT_RECENCY * recency_score(job, now)
        + WEIGHT_SOURCE * source_score(job)
        + WEIGHT_COMPLETENESS * completeness_score(job),
        6,
    )


def merge_and_rank(
    indexed: list[dict[str, Any]],
    live: list[dict[str, Any]],
    *,
    query: str,
    limit: int = 30,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Merge indexed and live results into one ranked, deduplicated list.

    Indexed rows are added first so that when the same posting appears in
    both, the stored one wins: it has an id the rest of the product can act
    on (saving, applying, matching), where a live row is transient until
    persistence catches up.
    """
    merged: dict[str, dict[str, Any]] = {}

    for job in indexed:
        key = dedup_key(job)
        if key not in merged:
            entry = dict(job)
            entry.setdefault("is_live", False)
            merged[key] = entry

    for job in live:
        key = dedup_key(job)
        if key in merged:
            # Same posting from both sides. Keep the stored row, but note the
            # corroboration - a job two sources list is more likely real.
            merged[key]["also_seen_live"] = True
            continue
        entry = dict(job)
        entry["is_live"] = True
        merged[key] = entry

    for entry in merged.values():
        entry["rank_score"] = score_job(entry, query, now)

    ranked = sorted(
        merged.values(),
        # Ties broken by title so the order is deterministic; an unstable
        # order makes results jump between identical searches.
        key=lambda j: (-float(j.get("rank_score") or 0.0), (j.get("title") or "").lower()),
    )
    return ranked[:limit]
