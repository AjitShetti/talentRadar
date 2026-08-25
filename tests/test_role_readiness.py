"""
tests/test_role_readiness.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for ``services.career.target_role_readiness`` — the resume-vs-target-
roles comparison behind the dashboard's "Skills to strengthen" panel.

The invariants that matter:
  * a skill the resume already shows is never reported as missing, whether the
    taxonomy caught it or it is only spelled out in the prose;
  * the LLM narrows a shortlist we derived from real postings — it never
    invents demand, and when it is unavailable the panel still says something
    true from the market counts alone;
  * when the resume genuinely covers the role, the panel flips to how the
    resume could read better rather than inventing a gap;
  * missing profile pieces produce a prompt, not an empty panel.

Everything runs without a database, a network, or an LLM key.
"""

from __future__ import annotations

from typing import Any

import pytest

from services import career

RESUME_TEXT = """
Backend Engineer — built REST APIs with Python and FastAPI, designed PostgreSQL
schemas, and ran Redis caching for a job ingestion pipeline doing 50k postings a day.
"""

RESUME_ROW: dict[str, Any] = {
    "id": "resume-1",
    "extracted_text": RESUME_TEXT,
    "filename": "ajit_backend.pdf",
    "updated_at": "2026-08-20T10:00:00",
}

PROFILE: dict[str, Any] = {
    "target_roles": ["Backend Engineer"],
    "skills": [{"name": "python", "proficiency": 4}],
}

MARKET: dict[str, Any] = {
    "jobs": [
        {"skills": ["Python", "FastAPI", "Kubernetes", "AWS"]},
        {"skills": ["python", "Kubernetes", "AWS", "CI/CD"]},
        {"skills": ["Python", "PostgreSQL", "Kubernetes"]},
    ]
}


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Each test starts from a cold cache — results are memoised for 30 minutes."""
    career._READINESS_CACHE.clear()


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile: dict[str, Any] | None = PROFILE,
    resume: dict[str, Any] | None = RESUME_ROW,
    market: dict[str, Any] | None = None,
    llm: dict[str, Any] | Exception | None = None,
) -> dict[str, list[Any]]:
    """Stub the profile/resume/market/LLM collaborators; record what they saw."""
    calls: dict[str, list[Any]] = {"market": [], "llm": []}

    async def fake_profile(*, user_id: str) -> dict[str, Any] | None:
        return profile

    async def fake_resume(*, user_id: str) -> dict[str, Any] | None:
        return resume

    async def fake_search(**kwargs: object) -> dict[str, Any]:
        calls["market"].append(kwargs)
        return market if market is not None else MARKET

    async def fake_llm(**kwargs: object) -> dict[str, Any]:
        calls["llm"].append(kwargs)
        if isinstance(llm, Exception):
            raise llm
        return llm or {"missing_skills": [], "resume_improvements": []}

    monkeypatch.setattr("services.profiles.get_profile", fake_profile)
    monkeypatch.setattr("services.resumes.get_active_resume", fake_resume)
    monkeypatch.setattr("services.jobs.search_jobs", fake_search)
    monkeypatch.setattr("services.llm.generate_role_readiness", fake_llm)
    return calls


# ─────────────────────────────────────────────────────────────────────────────
# Missing prerequisites
# ─────────────────────────────────────────────────────────────────────────────

async def test_no_resume_asks_for_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, resume=None)
    result = await career.target_role_readiness(user_id="u1")
    assert result["status"] == "no_resume"
    assert result["items"] == []
    assert "Upload your resume" in result["headline"]


async def test_no_target_roles_asks_for_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, profile={"target_roles": [], "skills": []})
    result = await career.target_role_readiness(user_id="u2")
    assert result["status"] == "no_target_roles"
    assert "target role" in result["headline"]


async def test_missing_prerequisites_never_call_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """No resume means nothing to analyse — don't spend a Groq call finding out."""
    calls = _stub(monkeypatch, resume=None)
    await career.target_role_readiness(user_id="u3")
    assert calls["llm"] == [] and calls["market"] == []


# ─────────────────────────────────────────────────────────────────────────────
# The shortlist handed to the LLM
# ─────────────────────────────────────────────────────────────────────────────

async def test_shortlist_excludes_skills_the_resume_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch)
    await career.target_role_readiness(user_id="u4")

    shortlisted = {c["skill"].lower() for c in calls["llm"][0]["candidate_skills"]}
    # Named in the resume prose (and the taxonomy) — not a gap.
    assert "python" not in shortlisted
    assert "fastapi" not in shortlisted
    assert "postgresql" not in shortlisted
    # In demand for the role and absent from the resume — a real gap.
    assert {"kubernetes", "aws", "ci/cd"} <= shortlisted


async def test_shortlist_is_ranked_by_posting_demand(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch)
    await career.target_role_readiness(user_id="u5")

    counts = [c["postings"] for c in calls["llm"][0]["candidate_skills"]]
    assert counts == sorted(counts, reverse=True)
    top = calls["llm"][0]["candidate_skills"][0]
    assert top["skill"] == "Kubernetes" and top["postings"] == 3


async def test_shortlist_keeps_the_postings_own_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matching is lowercased; display must not turn 'CI/CD' into 'Ci/Cd'."""
    calls = _stub(monkeypatch)
    await career.target_role_readiness(user_id="u6")
    assert "CI/CD" in {c["skill"] for c in calls["llm"][0]["candidate_skills"]}


# ─────────────────────────────────────────────────────────────────────────────
# The two answers the panel can give
# ─────────────────────────────────────────────────────────────────────────────

async def test_missing_skills_are_capped_at_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(
        monkeypatch,
        llm={
            "missing_skills": [
                {"skill": "Kubernetes", "reason": "Backend roles here run on it."},
                {"skill": "AWS", "reason": "Every posting names a cloud."},
                {"skill": "CI/CD", "reason": "Expected to own your own deploys."},
                {"skill": "Terraform", "reason": "Nice to have."},
            ],
            "resume_improvements": [],
        },
    )
    result = await career.target_role_readiness(user_id="u7", limit=3)

    assert result["kind"] == "missing_skills"
    assert [item["title"] for item in result["items"]] == ["Kubernetes", "AWS", "CI/CD"]
    assert all(item["detail"] for item in result["items"])


async def test_covered_resume_switches_to_improvements(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing major missing — say how the resume could read better instead."""
    _stub(
        monkeypatch,
        llm={
            "missing_skills": [],
            "resume_improvements": [
                {"title": "Quantify the pipeline", "reason": "50k/day is strong — add latency."},
                {"title": "Show ownership", "reason": "Name what you decided, not the team's work."},
            ],
        },
    )
    result = await career.target_role_readiness(user_id="u8")

    assert result["kind"] == "resume_improvements"
    assert result["analysis"] == "llm"
    assert len(result["items"]) == 2
    assert "covers the core skills" in result["headline"]


async def test_blank_and_duplicate_llm_entries_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(
        monkeypatch,
        llm={
            "missing_skills": [
                {"skill": "Kubernetes", "reason": "r1"},
                {"skill": "  ", "reason": "no name"},
                {"skill": "kubernetes", "reason": "same skill again"},
            ],
            "resume_improvements": [],
        },
    )
    result = await career.target_role_readiness(user_id="u9")
    assert [item["title"] for item in result["items"]] == ["Kubernetes"]


# ─────────────────────────────────────────────────────────────────────────────
# Degradation
# ─────────────────────────────────────────────────────────────────────────────

async def test_llm_failure_falls_back_to_market_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, llm=RuntimeError("groq is down"))
    result = await career.target_role_readiness(user_id="u10")

    assert result["status"] == "ok"
    assert result["kind"] == "missing_skills"
    assert result["analysis"] == "market"
    assert result["items"][0]["title"] == "Kubernetes"
    assert "postings we sampled" in result["items"][0]["detail"]


async def test_llm_failure_with_no_gaps_gives_generic_advice(monkeypatch: pytest.MonkeyPatch) -> None:
    """The market shows nothing the resume lacks and the LLM is gone: still useful."""
    _stub(
        monkeypatch,
        llm=RuntimeError("groq is down"),
        market={"jobs": [{"skills": ["Python", "FastAPI"]}]},
    )
    result = await career.target_role_readiness(user_id="u11")

    assert result["kind"] == "resume_improvements"
    assert result["analysis"] == "generic"
    assert len(result["items"]) == 3


async def test_market_failure_does_not_sink_the_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead job search leaves an empty shortlist, not an exception."""
    calls = _stub(monkeypatch)

    async def boom(**kwargs: object) -> dict[str, Any]:
        raise RuntimeError("db is down")

    monkeypatch.setattr("services.jobs.search_jobs", boom)
    result = await career.target_role_readiness(user_id="u12")

    assert result["status"] == "ok"
    assert calls["llm"][0]["candidate_skills"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Caching
# ─────────────────────────────────────────────────────────────────────────────

async def test_repeat_calls_are_served_from_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch)
    first = await career.target_role_readiness(user_id="u13")
    second = await career.target_role_readiness(user_id="u13")

    assert first == second
    assert len(calls["llm"]) == 1


async def test_a_new_resume_invalidates_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-uploading must re-run the analysis, not show yesterday's gaps."""
    _stub(monkeypatch)
    await career.target_role_readiness(user_id="u14")

    # Same user, same roles — only the saved-at stamp moved.
    reupload = _stub(monkeypatch, resume={**RESUME_ROW, "updated_at": "2026-08-25T09:00:00"})
    await career.target_role_readiness(user_id="u14")

    assert len(reupload["llm"]) == 1


async def test_editing_target_roles_invalidates_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch)
    await career.target_role_readiness(user_id="u15")

    retarget = _stub(monkeypatch, profile={**PROFILE, "target_roles": ["Platform Engineer"]})
    result = await career.target_role_readiness(user_id="u15")

    assert len(retarget["llm"]) == 1
    assert retarget["market"][0]["query"] == "Platform Engineer"
    assert result["target_roles"] == ["Platform Engineer"]
