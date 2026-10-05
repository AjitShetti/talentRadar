"""
tests/test_dossier.py
~~~~~~~~~~~~~~~~~~~~~
The Role Dossier: one page per role answering four questions - is it open,
is it for me, what is my way in, am I ready.

Each answer comes from a different part of the system. What is pinned here is
the composition: a section that fails or has nothing to say is absent, and
never takes the page down with it.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from services import dossier

JOB_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())

ROLE = {
    "id": JOB_ID,
    "title": "Backend Engineer",
    "company_id": str(uuid.uuid4()),
    "company": "Stripe",
    "skills": ["python", "kafka", "postgresql"],
    "description": "Own the ledger service.",
    "liveness": {"state": "verified_open", "headline": "Confirmed open 2 hours ago", "evidence": [], "open_days": 4},
}


@pytest.fixture
def sections(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub every loader with a healthy answer; tests break one at a time."""
    answers: dict[str, Any] = {
        "role": dict(ROLE),
        "fit": {"available": True, "coverage_percentage": 66.7, "matched_skills": ["python", "kafka"], "missing_skills": ["postgresql"]},
        "way_in": {"contacts": [{"name": "A Recruiter", "is_curated": True}], "careers_url": None},
        "readiness": {"sessions": 1, "last_score": 72.0, "application_status": "saved"},
    }

    def loader(name: str) -> Any:
        async def load(*args: Any, **kwargs: Any) -> Any:
            value = answers[name]
            if isinstance(value, Exception):
                raise value
            return value

        return load

    monkeypatch.setattr(dossier, "_load_role", loader("role"))
    monkeypatch.setattr(dossier, "_fit", loader("fit"))
    monkeypatch.setattr(dossier, "_way_in", loader("way_in"))
    monkeypatch.setattr(dossier, "_readiness", loader("readiness"))
    return answers


async def test_dossier_carries_all_four_answers(sections):
    result = await dossier.build_dossier(job_id=JOB_ID, user_id=USER_ID)
    assert result is not None
    assert result["role"]["title"] == "Backend Engineer"
    assert result["liveness"]["state"] == "verified_open"
    assert result["fit"]["missing_skills"] == ["postgresql"]
    assert result["way_in"]["contacts"][0]["name"] == "A Recruiter"
    assert result["readiness"]["last_score"] == 72.0


async def test_liveness_is_lifted_out_of_the_role(sections):
    result = await dossier.build_dossier(job_id=JOB_ID, user_id=USER_ID)
    assert result is not None
    assert "liveness" not in result["role"]


async def test_unknown_role_is_none(sections):
    sections["role"] = None
    assert await dossier.build_dossier(job_id=JOB_ID, user_id=USER_ID) is None


async def test_a_job_id_that_is_not_a_uuid_is_none(sections):
    assert await dossier.build_dossier(job_id="not-a-uuid", user_id=USER_ID) is None


@pytest.mark.parametrize("broken", ["fit", "way_in", "readiness"])
async def test_one_failing_section_does_not_fail_the_page(sections, broken):
    sections[broken] = RuntimeError("upstream is down")
    result = await dossier.build_dossier(job_id=JOB_ID, user_id=USER_ID)
    assert result is not None
    assert result[broken] is None
    for other in {"fit", "way_in", "readiness"} - {broken}:
        assert result[other] is not None


async def test_a_role_with_no_liveness_evidence_says_so_with_null(sections):
    sections["role"] = {**ROLE, "liveness": None}
    result = await dossier.build_dossier(job_id=JOB_ID, user_id=USER_ID)
    assert result is not None
    assert result["liveness"] is None


# ── fit ──────────────────────────────────────────────────────────────────────


async def test_fit_without_a_saved_resume_says_what_is_missing(monkeypatch):
    async def no_resume(*, user_id: str) -> None:
        return None

    monkeypatch.setattr(dossier, "get_active_resume", no_resume)
    fit = await dossier._fit(USER_ID, ROLE)
    assert fit == {"available": False, "reason": "no_resume"}


async def test_fit_compares_the_resume_with_the_roles_skills(monkeypatch):
    async def resume(*, user_id: str) -> dict[str, Any]:
        return {"extracted_text": "Five years of Python and Kafka."}

    monkeypatch.setattr(dossier, "get_active_resume", resume)
    monkeypatch.setattr(dossier, "_extract_skills", lambda text: {"python", "kafka"})
    fit = await dossier._fit(USER_ID, ROLE)
    assert fit["available"] is True
    assert fit["matched_skills"] == ["kafka", "python"]
    assert fit["missing_skills"] == ["postgresql"]
    assert fit["coverage_percentage"] == 66.7


async def test_fit_for_a_role_that_names_no_skills_is_unavailable(monkeypatch):
    async def resume(*, user_id: str) -> dict[str, Any]:
        return {"extracted_text": "Python."}

    monkeypatch.setattr(dossier, "get_active_resume", resume)
    monkeypatch.setattr(dossier, "_extract_skills", lambda text: set())
    fit = await dossier._fit(USER_ID, {**ROLE, "skills": [], "description": ""})
    assert fit == {"available": False, "reason": "no_role_skills"}


# ── way in ───────────────────────────────────────────────────────────────────


async def test_way_in_lists_known_contacts_and_never_invents_one(monkeypatch):
    async def contacts(company_id: str, user_id: str | None) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(dossier, "list_contacts", contacts)
    way_in = await dossier._way_in(USER_ID, ROLE)
    assert way_in["contacts"] == []


async def test_way_in_separates_the_careers_page_from_people(monkeypatch):
    async def contacts(company_id: str, user_id: str | None) -> list[dict[str, Any]]:
        return [
            {"kind": "careers_page", "source_url": "https://stripe.com/jobs", "name": None},
            {"kind": "recruiter", "name": "A Recruiter", "source_url": None},
        ]

    monkeypatch.setattr(dossier, "list_contacts", contacts)
    way_in = await dossier._way_in(USER_ID, ROLE)
    assert way_in["careers_url"] == "https://stripe.com/jobs"
    assert [c["name"] for c in way_in["contacts"]] == ["A Recruiter"]
