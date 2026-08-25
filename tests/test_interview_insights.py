"""
tests/test_interview_insights.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for the dashboard's interview feedback aggregator.

The invariant that matters here is that the panel is *deterministic and
honest*: every number is derived from persisted sub-scores (no LLM call),
the weakest dimension and weakest round are really the lowest ones, and a
user with no history gets the same payload shape as one with ten sessions.

Everything runs without a database or a network.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from services.interviews import (
    DIMENSION_HINTS,
    WEAK_MOMENT_THRESHOLD,
    interview_insights,
)
from storage.models import InterviewDifficulty, InterviewTrack

USER_ID = "11111111-1111-1111-1111-111111111111"


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / fakes
# ─────────────────────────────────────────────────────────────────────────────

def _session(
    *,
    track: InterviewTrack = InterviewTrack.CODING,
    difficulty: InterviewDifficulty = InterviewDifficulty.MID,
    completed: bool = True,
    days_ago: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        track=track,
        difficulty=difficulty,
        completed=completed,
        created_at=datetime.now(tz=UTC) - timedelta(days=days_ago),
    )


def _score(
    session_id: uuid.UUID,
    *,
    correctness: float,
    clarity: float,
    depth: float,
    question: str = "Explain how you would design this.",
    index: int = 0,
    followup: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        question_index=index,
        question_text=question,
        answer_summary=None,
        score_correctness=correctness,
        score_clarity=clarity,
        score_depth=depth,
        was_followup=followup,
    )


class _FakeRepo:
    """Stands in for InterviewRepository with canned rows."""

    def __init__(self, sessions: list[Any], scores: list[Any]) -> None:
        self._sessions = sessions
        self._scores = scores

    async def get_session_history(self, _db: Any, _user: uuid.UUID, *, limit: int = 20) -> list[Any]:
        # Newest first, exactly like the real repository.
        ordered = sorted(self._sessions, key=lambda s: s.created_at, reverse=True)
        return ordered[:limit]

    async def get_answer_scores_for_sessions(self, _db: Any, session_ids: Any) -> list[Any]:
        wanted = set(session_ids)
        return [s for s in self._scores if s.session_id in wanted]


class _FakeDb:
    async def close(self) -> None:
        return None


@pytest.fixture
def patch_storage(monkeypatch: pytest.MonkeyPatch):
    """Return a helper that installs fake sessions/scores for one call."""

    def _install(sessions: list[Any], scores: list[Any]) -> None:
        import storage.database as database
        import storage.interview_repository as repo_module

        monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _FakeDb())
        monkeypatch.setattr(
            repo_module, "InterviewRepository", lambda: _FakeRepo(sessions, scores)
        )

    return _install


# ─────────────────────────────────────────────────────────────────────────────
# Zero state
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_sessions_returns_the_populated_shape(patch_storage) -> None:
    """The dashboard renders one contract — the empty payload must match it."""
    patch_storage([], [])
    result = await interview_insights(user_id=USER_ID)

    assert result["sessions_analyzed"] == 0
    assert result["weakest_dimension"] is None
    assert result["dimensions"] == []
    assert result["weak_moments"] == []
    assert result["focus"] == []


@pytest.mark.asyncio
async def test_invalid_user_id_does_not_touch_storage() -> None:
    result = await interview_insights(user_id="not-a-uuid")
    assert result["sessions_analyzed"] == 0


@pytest.mark.asyncio
async def test_sessions_without_answers_are_treated_as_empty(patch_storage) -> None:
    """A session started and abandoned before the first answer has no signal."""
    patch_storage([_session()], [])
    assert (await interview_insights(user_id=USER_ID))["sessions_analyzed"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Score maths
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_average_matches_the_session_scoring_formula(patch_storage) -> None:
    """Same formula the score card uses: (c + cl + d) / 30 * 100."""
    sess = _session()
    patch_storage([sess], [_score(sess.id, correctness=6.0, clarity=6.0, depth=6.0)])

    result = await interview_insights(user_id=USER_ID)
    assert result["average_score"] == 60.0
    assert result["questions_analyzed"] == 1


@pytest.mark.asyncio
async def test_weakest_dimension_is_the_lowest_sub_score(patch_storage) -> None:
    sess = _session()
    patch_storage(
        [sess],
        [_score(sess.id, correctness=9.0, clarity=8.0, depth=3.0)],
    )

    result = await interview_insights(user_id=USER_ID)
    weakest = result["weakest_dimension"]
    assert weakest["key"] == "depth"
    assert weakest["score"] == 30.0
    assert weakest["hint"] == DIMENSION_HINTS["depth"]

    flagged = [d for d in result["dimensions"] if d["weakest"]]
    assert [d["key"] for d in flagged] == ["depth"]
    # The hint is the headline coaching line the panel shows.
    assert result["focus"][0] == DIMENSION_HINTS["depth"]


@pytest.mark.asyncio
async def test_dimensions_are_averaged_across_every_answer(patch_storage) -> None:
    sess = _session()
    patch_storage(
        [sess],
        [
            _score(sess.id, correctness=8.0, clarity=4.0, depth=6.0, index=0),
            _score(sess.id, correctness=6.0, clarity=6.0, depth=4.0, index=1),
        ],
    )

    scores = {d["key"]: d["score"] for d in (await interview_insights(user_id=USER_ID))["dimensions"]}
    assert scores == {"correctness": 70.0, "clarity": 50.0, "depth": 50.0}


# ─────────────────────────────────────────────────────────────────────────────
# Trend
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trend_runs_oldest_to_newest(patch_storage) -> None:
    """The sparkline reads left-to-right in time, not in query order."""
    old = _session(days_ago=10)
    recent = _session(days_ago=1)
    patch_storage(
        [old, recent],
        [
            _score(old.id, correctness=3.0, clarity=3.0, depth=3.0),
            _score(recent.id, correctness=9.0, clarity=9.0, depth=9.0),
        ],
    )

    result = await interview_insights(user_id=USER_ID)
    assert [point["score"] for point in result["trend"]] == [30.0, 90.0]
    assert result["delta"] == 60.0  # latest vs the average of everything before


@pytest.mark.asyncio
async def test_single_session_has_no_delta(patch_storage) -> None:
    sess = _session()
    patch_storage([sess], [_score(sess.id, correctness=5.0, clarity=5.0, depth=5.0)])
    assert (await interview_insights(user_id=USER_ID))["delta"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Round types
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tracks_are_ranked_weakest_first(patch_storage) -> None:
    coding = _session(track=InterviewTrack.CODING, days_ago=2)
    design = _session(track=InterviewTrack.SYSTEM_DESIGN, days_ago=1)
    patch_storage(
        [coding, design],
        [
            _score(coding.id, correctness=9.0, clarity=9.0, depth=9.0),
            _score(design.id, correctness=3.0, clarity=4.0, depth=2.0),
        ],
    )

    result = await interview_insights(user_id=USER_ID)
    assert [t["track"] for t in result["tracks"]] == ["system_design", "coding"]
    assert result["weakest_track"]["label"] == "System Design"
    assert result["weakest_track"]["sessions"] == 1
    # The weakest round earns its own focus line once it drags below the average.
    assert any("System Design" in line for line in result["focus"])


@pytest.mark.asyncio
async def test_a_single_track_is_not_called_the_weakest(patch_storage) -> None:
    """With one round type there is nothing to compare it against."""
    sess = _session(track=InterviewTrack.BEHAVIORAL)
    patch_storage([sess], [_score(sess.id, correctness=2.0, clarity=2.0, depth=2.0)])

    result = await interview_insights(user_id=USER_ID)
    assert result["weakest_track"] is None
    assert len(result["tracks"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# "Where it went wrong"
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_weak_moments_are_the_lowest_answers_only(patch_storage) -> None:
    sess = _session(track=InterviewTrack.TECHNICAL, difficulty=InterviewDifficulty.SENIOR)
    patch_storage(
        [sess],
        [
            _score(sess.id, correctness=9.0, clarity=9.0, depth=9.0, question="Strong answer", index=0),
            _score(sess.id, correctness=2.0, clarity=5.0, depth=3.0, question="Weak answer", index=1),
        ],
    )

    moments = (await interview_insights(user_id=USER_ID))["weak_moments"]
    assert len(moments) == 1
    assert moments[0]["question"] == "Weak answer"
    assert moments[0]["dimension"] == "Correctness"  # the sub-score that sank it
    assert moments[0]["dimension_score"] == 2.0
    assert moments[0]["track_label"] == "Technical / Domain"
    assert moments[0]["difficulty"] == "senior"


@pytest.mark.asyncio
async def test_a_strong_run_reports_no_weak_moments(patch_storage) -> None:
    sess = _session()
    patch_storage(
        [sess],
        [_score(sess.id, correctness=8.0, clarity=8.0, depth=8.0)],
    )
    result = await interview_insights(user_id=USER_ID)
    assert result["average_score"] > WEAK_MOMENT_THRESHOLD
    assert result["weak_moments"] == []


@pytest.mark.asyncio
async def test_weak_moments_are_capped_and_questions_truncated(patch_storage) -> None:
    sess = _session()
    long_question = "q" * 400
    patch_storage(
        [sess],
        [
            _score(sess.id, correctness=1.0, clarity=1.0, depth=1.0, question=long_question, index=i)
            for i in range(6)
        ],
    )

    moments = (await interview_insights(user_id=USER_ID, moment_limit=3))["weak_moments"]
    assert len(moments) == 3
    assert moments[0]["question"].endswith("…")
    assert len(moments[0]["question"]) == 161


# ─────────────────────────────────────────────────────────────────────────────
# Focus lines
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_abandoned_sessions_are_called_out(patch_storage) -> None:
    finished = _session(days_ago=3)
    dropped = _session(days_ago=1, completed=False)
    patch_storage(
        [finished, dropped],
        [
            _score(finished.id, correctness=6.0, clarity=6.0, depth=6.0),
            _score(dropped.id, correctness=6.0, clarity=6.0, depth=6.0),
        ],
    )

    result = await interview_insights(user_id=USER_ID)
    assert result["abandoned"] == 1
    assert any("unfinished" in line for line in result["focus"])


@pytest.mark.asyncio
async def test_focus_never_floods_the_panel(patch_storage) -> None:
    coding = _session(track=InterviewTrack.CODING, days_ago=2)
    design = _session(track=InterviewTrack.SYSTEM_DESIGN, days_ago=1, completed=False)
    patch_storage(
        [coding, design],
        [
            _score(coding.id, correctness=9.0, clarity=8.0, depth=2.0),
            _score(design.id, correctness=3.0, clarity=3.0, depth=1.0),
        ],
    )

    assert len((await interview_insights(user_id=USER_ID))["focus"]) <= 3
