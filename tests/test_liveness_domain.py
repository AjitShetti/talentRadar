"""
tests/test_liveness_domain.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The liveness verdict is a pure function over evidence we actually hold.

Every rule and every boundary is pinned here because the verdict is the one
thing v2 shows a stranger before they trust anything else on the page.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from domain.liveness import (
    LivenessEvidence,
    LivenessState,
    SightingState,
    assess,
    is_verifiable_source,
    next_sighting,
    parse_source_timestamp,
    title_key,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def evidence(**overrides: object) -> LivenessEvidence:
    base: dict[str, object] = {
        "source_posted_at": NOW - timedelta(days=5),
        "first_seen_at": NOW - timedelta(days=5),
        "last_seen_at": NOW - timedelta(hours=2),
        "last_verified_at": NOW - timedelta(hours=2),
        "closed_at": None,
        "times_reposted": 0,
        "verifiable": True,
    }
    base.update(overrides)
    return LivenessEvidence(**base)  # type: ignore[arg-type]


# ── one case per state ───────────────────────────────────────────────────────


def test_recently_verified_ats_role_is_verified_open():
    verdict = assess(evidence(), now=NOW)
    assert verdict.state is LivenessState.VERIFIED_OPEN
    assert verdict.open_days == 5


def test_closed_at_source_is_closed():
    verdict = assess(evidence(closed_at=NOW - timedelta(days=1)), now=NOW)
    assert verdict.state is LivenessState.CLOSED


def test_twice_reposted_role_is_reposted():
    verdict = assess(evidence(times_reposted=2), now=NOW)
    assert verdict.state is LivenessState.REPOSTED


def test_role_open_longer_than_45_days_is_ageing():
    verdict = assess(evidence(source_posted_at=NOW - timedelta(days=46)), now=NOW)
    assert verdict.state is LivenessState.AGEING
    assert verdict.open_days == 46


def test_board_role_seen_this_week_is_open_unverified():
    verdict = assess(
        evidence(verifiable=False, last_verified_at=None, last_seen_at=NOW - timedelta(days=3)),
        now=NOW,
    )
    assert verdict.state is LivenessState.OPEN_UNVERIFIED


def test_role_with_no_recent_evidence_is_unknown():
    verdict = assess(
        evidence(verifiable=False, last_verified_at=None, last_seen_at=NOW - timedelta(days=20)),
        now=NOW,
    )
    assert verdict.state is LivenessState.UNKNOWN


def test_role_with_no_evidence_at_all_is_unknown():
    verdict = assess(
        LivenessEvidence(None, None, None, None, None, 0, False),
        now=NOW,
    )
    assert verdict.state is LivenessState.UNKNOWN
    assert verdict.open_days is None


# ── boundaries ───────────────────────────────────────────────────────────────


def test_exactly_45_days_is_not_yet_ageing():
    verdict = assess(evidence(source_posted_at=NOW - timedelta(days=45)), now=NOW)
    assert verdict.state is LivenessState.VERIFIED_OPEN


def test_verification_older_than_72_hours_no_longer_counts():
    verdict = assess(
        evidence(last_verified_at=NOW - timedelta(hours=73), last_seen_at=NOW - timedelta(days=10)),
        now=NOW,
    )
    assert verdict.state is LivenessState.UNKNOWN


def test_verification_at_exactly_72_hours_still_counts():
    verdict = assess(evidence(last_verified_at=NOW - timedelta(hours=72)), now=NOW)
    assert verdict.state is LivenessState.VERIFIED_OPEN


def test_stale_verification_falls_back_to_a_recent_sighting():
    verdict = assess(
        evidence(last_verified_at=NOW - timedelta(days=5), last_seen_at=NOW - timedelta(days=1)),
        now=NOW,
    )
    assert verdict.state is LivenessState.OPEN_UNVERIFIED


def test_a_single_repost_is_not_flagged():
    verdict = assess(evidence(times_reposted=1), now=NOW)
    assert verdict.state is LivenessState.VERIFIED_OPEN


def test_age_falls_back_to_first_seen_when_the_source_gave_no_date():
    verdict = assess(
        evidence(source_posted_at=None, first_seen_at=NOW - timedelta(days=60)), now=NOW
    )
    assert verdict.state is LivenessState.AGEING
    assert verdict.open_days == 60


# ── precedence ───────────────────────────────────────────────────────────────


def test_closed_beats_reposted_and_ageing():
    verdict = assess(
        evidence(
            closed_at=NOW,
            times_reposted=4,
            source_posted_at=NOW - timedelta(days=200),
        ),
        now=NOW,
    )
    assert verdict.state is LivenessState.CLOSED


def test_reposted_beats_ageing():
    verdict = assess(
        evidence(times_reposted=3, source_posted_at=NOW - timedelta(days=200)), now=NOW
    )
    assert verdict.state is LivenessState.REPOSTED


# ── the verdict explains itself ──────────────────────────────────────────────


def test_every_verdict_carries_a_headline():
    for ev in (
        evidence(),
        evidence(closed_at=NOW),
        evidence(times_reposted=2),
        evidence(source_posted_at=NOW - timedelta(days=90)),
        evidence(verifiable=False, last_verified_at=None),
        LivenessEvidence(None, None, None, None, None, 0, False),
    ):
        assert assess(ev, now=NOW).headline


def test_evidence_lines_state_the_facts_behind_the_verdict():
    verdict = assess(
        evidence(source_posted_at=NOW - timedelta(days=54), times_reposted=3), now=NOW
    )
    joined = " | ".join(verdict.evidence)
    assert "54 days" in joined
    assert "3 times" in joined


def test_unverifiable_source_says_so_in_the_evidence():
    verdict = assess(evidence(verifiable=False, last_verified_at=None), now=NOW)
    assert any("cannot be re-checked" in line for line in verdict.evidence)


def test_naive_datetimes_are_treated_as_utc():
    naive = (NOW - timedelta(days=50)).replace(tzinfo=None)
    verdict = assess(evidence(source_posted_at=naive), now=NOW)
    assert verdict.state is LivenessState.AGEING


# ── source and title helpers ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("greenhouse:stripe", True),
        ("lever:spotify", True),
        ("ashby:openai", True),
        ("greenhouse", True),
        ("linkedin", False),
        ("foundit", False),
        ("live_search", False),
        ("", False),
    ],
)
def test_only_ats_sources_are_verifiable(source: str, expected: bool):
    assert is_verifiable_source(source) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Senior Backend Engineer", "senior backend engineer"),
        ("  Senior   Backend  Engineer ", "senior backend engineer"),
        ("Senior Backend Engineer (R-12345)", "senior backend engineer"),
        ("Senior Backend Engineer - REQ 9981", "senior backend engineer"),
        ("Senior Backend Engineer, Payments", "senior backend engineer payments"),
        ("SDE-2 | Platform", "sde 2 platform"),
        ("", ""),
    ],
)
def test_title_key_normalises_titles(raw: str, expected: str):
    assert title_key(raw) == expected


def test_title_key_is_bounded():
    assert len(title_key("engineer " * 100)) <= 200


# ── source timestamps ────────────────────────────────────────────────────────


def test_parses_an_iso_timestamp_with_offset():
    parsed = parse_source_timestamp("2026-08-12T10:30:00-04:00")
    assert parsed == datetime(2026, 8, 12, 14, 30, tzinfo=UTC)


def test_parses_a_zulu_timestamp():
    assert parse_source_timestamp("2026-08-12T10:30:00.123Z") == datetime(
        2026, 8, 12, 10, 30, 0, 123000, tzinfo=UTC
    )


def test_parses_epoch_milliseconds():
    # Lever's createdAt.
    assert parse_source_timestamp(1786530600000) == datetime.fromtimestamp(1786530600, tz=UTC)


@pytest.mark.parametrize("junk", [None, "", "yesterday", {}, [], -5, True])
def test_unparseable_timestamps_yield_none(junk: object):
    assert parse_source_timestamp(junk) is None


# ── repost detection ─────────────────────────────────────────────────────────


def test_first_sighting_starts_the_record():
    state = next_sighting(None, external_id="a1", seen_at=NOW)
    assert state == SightingState(NOW, NOW, 1, 0, "a1")


def test_same_posting_seen_again_is_not_a_repost():
    first = next_sighting(None, external_id="a1", seen_at=NOW - timedelta(days=30))
    state = next_sighting(first, external_id="a1", seen_at=NOW)
    assert state.times_seen == 2
    assert state.times_reposted == 0
    assert state.first_seen_at == NOW - timedelta(days=30)
    assert state.last_seen_at == NOW


def test_new_id_within_14_days_is_a_sibling_opening_not_a_repost():
    first = next_sighting(None, external_id="a1", seen_at=NOW - timedelta(days=10))
    state = next_sighting(first, external_id="b2", seen_at=NOW)
    assert state.times_reposted == 0
    assert state.last_external_id == "b2"


def test_new_id_after_a_14_day_gap_is_a_repost():
    first = next_sighting(None, external_id="a1", seen_at=NOW - timedelta(days=15))
    state = next_sighting(first, external_id="b2", seen_at=NOW)
    assert state.times_reposted == 1
    assert state.last_external_id == "b2"


def test_an_out_of_order_sighting_never_moves_last_seen_backwards():
    first = next_sighting(None, external_id="a1", seen_at=NOW)
    state = next_sighting(first, external_id="a1", seen_at=NOW - timedelta(days=3))
    assert state.last_seen_at == NOW
