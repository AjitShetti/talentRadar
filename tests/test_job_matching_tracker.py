"""
tests/test_job_matching_tracker.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Today's matches are the Overview's "Apply today" stack: each says whether the user has
applied to it, and a role already in the tracker is never picked again.

Runs on in-memory SQLite (``tests/outreach_support.py``). Every role and company is fictional.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services import job_matching
from storage.models import ApplicationStatus, JobApplication
from tests.outreach_support import make_company, make_job, make_user, outreach_db  # noqa: F401

Factory = async_sessionmaker[AsyncSession]


def match(job_id: Any) -> dict[str, Any]:
    return {"id": str(job_id), "title": "Platform Engineer", "matched_role": "Platform Engineer"}


async def test_each_match_says_whether_it_is_applied(outreach_db: Factory) -> None:  # noqa: F811
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Lumen Works")
        saved, applied, untouched = [
            await make_job(session, company, f"Role {i}") for i in range(3)
        ]
        session.add(
            JobApplication(user_id=user.id, job_id=saved.id, status=ApplicationStatus.SAVED)
        )
        session.add(
            JobApplication(user_id=user.id, job_id=applied.id, status=ApplicationStatus.APPLIED)
        )
        await session.commit()

        out = await job_matching.with_tracker_state(
            user_id=str(user.id),
            matches=[match(saved.id), match(applied.id), match(untouched.id)],
            session=session,
        )

    assert [m["applied"] for m in out] == [False, True, False]
    assert out[0]["application_id"] is not None and out[2]["application_id"] is None


async def test_a_tracked_role_is_not_picked_again(
    outreach_db: Factory,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with outreach_db() as session:
        user = await make_user(session)
        company = await make_company(session, "Lumen Works")
        tracked, fresh = (
            await make_job(session, company, "Role A"),
            await make_job(session, company, "Role B"),
        )
        session.add(
            JobApplication(user_id=user.id, job_id=tracked.id, status=ApplicationStatus.SAVED)
        )
        await session.commit()

        async def profile(**_: Any) -> dict[str, Any]:
            return {"target_roles": ["Platform Engineer"]}

        async def search(**_: Any) -> dict[str, Any]:
            return {"jobs": [match(tracked.id), match(fresh.id)]}

        monkeypatch.setattr(job_matching, "get_profile", profile)
        monkeypatch.setattr(job_matching, "search_jobs", search)
        picked = await job_matching.compute_daily_matches_for_user(
            user_id=str(user.id), session=session
        )

    assert [m["id"] for m in picked] == [str(fresh.id)]
