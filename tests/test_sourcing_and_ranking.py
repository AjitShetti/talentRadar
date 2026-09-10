"""
tests/test_sourcing_and_ranking.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for the two pure pieces of the search flow: the decision to go to the
internet, and the ranking of what comes back.

Both are pure functions, so every branch is exercised here without a network,
a database or a model - which is the reason they were separated from the graph
nodes that call them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agents.merge_rank import (
    completeness_score,
    dedup_key,
    merge_and_rank,
    recency_score,
    relevance_score,
    score_job,
    source_score,
    to_retrieval_dict,
)
from agents.sourcing_policy import (
    MIN_INDEXED_RESULTS,
    STALE_AFTER_DAYS,
    decide_sourcing,
    query_fingerprint,
    sourced_lock_key,
    wants_fresh_results,
)

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def job(**overrides):
    base = {
        "title": "Python Developer",
        "company_name": "Acme",
        "city": "Bengaluru",
        "source": "linkedin",
        "skills": ["Python"],
        "source_url": "https://example.com/job/1",
        "posted_at": (NOW - timedelta(days=1)).isoformat(),
    }
    base.update(overrides)
    return base


# ── the sourcing gate ────────────────────────────────────────────────────────

def test_thin_index_triggers_sourcing():
    decision = decide_sourcing(query="python jobs", indexed_count=2)
    assert decision.should_source is True
    assert "below" in decision.reason


def test_healthy_fresh_index_does_not_trigger_sourcing():
    decision = decide_sourcing(
        query="python jobs",
        indexed_count=MIN_INDEXED_RESULTS + 10,
        newest_indexed_at=NOW - timedelta(days=1),
        now=NOW,
    )
    assert decision.should_source is False


def test_stale_index_triggers_sourcing():
    decision = decide_sourcing(
        query="python jobs",
        indexed_count=50,
        newest_indexed_at=NOW - timedelta(days=STALE_AFTER_DAYS + 1),
        now=NOW,
    )
    assert decision.should_source is True
    assert "days old" in decision.reason


def test_freshness_wording_triggers_sourcing_even_with_a_full_index():
    decision = decide_sourcing(
        query="latest python jobs in Bengaluru",
        indexed_count=100,
        newest_indexed_at=NOW - timedelta(days=1),
        now=NOW,
    )
    assert decision.should_source is True


def test_the_eight_hour_lock_beats_every_other_signal():
    """
    The guard that stops a popular query re-scraping on every page load, and
    the deployment's IP from being blocked for it.
    """
    decision = decide_sourcing(
        query="latest python jobs",
        indexed_count=0,
        newest_indexed_at=NOW - timedelta(days=365),
        already_sourced=True,
        now=NOW,
    )
    assert decision.should_source is False
    assert "8 hours" in decision.reason


def test_force_refresh_overrides_the_lock():
    decision = decide_sourcing(
        query="python jobs", indexed_count=100, already_sourced=True, force_refresh=True
    )
    assert decision.should_source is True


def test_naive_timestamps_are_treated_as_utc():
    """A naive datetime from the database must not raise on comparison."""
    decision = decide_sourcing(
        query="python jobs",
        indexed_count=50,
        newest_indexed_at=datetime(2020, 1, 1),
        now=NOW,
    )
    assert decision.should_source is True


def test_freshness_detection():
    assert wants_fresh_results("latest jobs") is True
    assert wants_fresh_results("jobs posted this week") is True
    assert wants_fresh_results("newly added roles") is True
    assert wants_fresh_results("senior python developer") is False


def test_fingerprint_normalises_spacing_and_case():
    assert query_fingerprint("Python  Developer", "Bengaluru", False) == query_fingerprint(
        "python developer", "bengaluru", False
    )


def test_fingerprint_separates_different_searches():
    assert query_fingerprint("python", "Bengaluru", False) != query_fingerprint(
        "python", "Mumbai", False
    )


def test_lock_key_is_namespaced():
    assert sourced_lock_key("python", "India", None).startswith("tr:sourced:")


# ── ranking signals ──────────────────────────────────────────────────────────

def test_relevance_prefers_title_matches_over_skill_matches():
    in_title = relevance_score(job(title="Python Developer", skills=[]), "python")
    in_skills = relevance_score(job(title="Software Engineer", skills=["Python"]), "python")
    assert in_title > in_skills > 0


def test_recency_decays_with_age():
    fresh = recency_score(job(posted_at=NOW.isoformat()), now=NOW)
    old = recency_score(job(posted_at=(NOW - timedelta(days=30)).isoformat()), now=NOW)
    assert fresh > old


def test_unknown_posting_date_scores_mid_not_zero():
    """Most scraped rows carry no real date; zeroing them would bury live results."""
    assert recency_score(job(posted_at=None), now=NOW) == 0.5


def test_first_party_sources_outrank_aggregators():
    assert source_score(job(source="greenhouse")) > source_score(job(source="freshersworld"))


def test_completeness_rewards_actionable_rows():
    rich = completeness_score(
        job(skills=["Python"], salary_raw="20 LPA", description_clean="x", source_url="u")
    )
    stub = completeness_score({"title": "Dev"})
    assert rich == 1.0
    assert stub == 0.0


def test_score_stays_within_bounds():
    assert 0.0 <= score_job(job(), "python", NOW) <= 1.0


# ── merging ──────────────────────────────────────────────────────────────────

def test_dedup_key_matches_the_same_posting_across_sources():
    a = job(source="linkedin")
    b = job(source="foundit")
    assert dedup_key(a) == dedup_key(b)


def test_indexed_rows_win_over_live_duplicates():
    """
    The stored row has an id the rest of the product can act on; a live row is
    transient until persistence catches up.
    """
    indexed = [job(id="stored-1", source="linkedin")]
    live = [job(source="foundit")]

    merged = merge_and_rank(indexed, live, query="python", now=NOW)
    assert len(merged) == 1
    assert merged[0]["id"] == "stored-1"
    assert merged[0]["also_seen_live"] is True


def test_live_only_results_are_included_and_flagged():
    merged = merge_and_rank([], [job(title="Django Engineer")], query="django", now=NOW)
    assert len(merged) == 1
    assert merged[0]["is_live"] is True


def test_more_relevant_jobs_rank_higher():
    merged = merge_and_rank(
        [job(title="Warehouse Associate", skills=[]), job(title="Python Developer")],
        [],
        query="python developer",
        now=NOW,
    )
    assert merged[0]["title"] == "Python Developer"


def test_limit_is_respected():
    many = [job(title=f"Python Developer {i}", company_name=f"C{i}") for i in range(50)]
    assert len(merge_and_rank(many, [], query="python", limit=10, now=NOW)) == 10


def test_ordering_is_deterministic():
    """An unstable order makes results jump between identical searches."""
    a = [job(title=f"Python Dev {i}", company_name=f"C{i}") for i in range(20)]
    first = [j["title"] for j in merge_and_rank(a, [], query="python", now=NOW)]
    second = [j["title"] for j in merge_and_rank(a, [], query="python", now=NOW)]
    assert first == second


def test_empty_inputs_produce_an_empty_list():
    assert merge_and_rank([], [], query="python", now=NOW) == []


def test_indexed_and_live_rows_dedupe_despite_different_location_fields():
    """
    Indexed rows carry ``location`` ("Bengaluru, India"); live rows carry
    ``city``. Hashing only ``city`` gave every indexed row an empty city, so
    the same posting from both sides never matched and reached the user twice.
    """
    indexed = [
        {
            "job_id": "stored-1",
            "title": "Python Developer",
            "company": "Acme",
            "location": "Bengaluru, India",
        }
    ]
    live = [
        {
            "id": "live-1",
            "title": "Python Developer",
            "company_name": "Acme",
            "city": "Bengaluru",
        }
    ]
    assert dedup_key(indexed[0]) == dedup_key(live[0])
    assert len(merge_and_rank(indexed, live, query="python", now=NOW)) == 1


# ── output contract ──────────────────────────────────────────────────────────

def test_live_only_rows_get_a_job_id():
    """
    The orchestrator indexes ``r["job_id"]`` directly. A live row carries
    ``id``, so without normalisation the whole request fails with KeyError.
    """
    normalised = to_retrieval_dict({"id": "live-1", "title": "Dev", "company_name": "Acme"})
    assert normalised["job_id"] == "live-1"
    assert normalised["company"] == "Acme"


def test_retrieval_dict_has_every_key_the_orchestrator_reads():
    normalised = to_retrieval_dict({"id": "x", "title": "Dev"})
    for key in ("job_id", "title", "company", "location", "is_remote", "skills", "score", "source_url"):
        assert key in normalised


def test_reported_score_is_the_blended_rank():
    normalised = to_retrieval_dict({"id": "x", "title": "Dev", "score": 0.2, "rank_score": 0.9})
    assert normalised["score"] == 0.9
    assert normalised["vector_score"] == 0.2
