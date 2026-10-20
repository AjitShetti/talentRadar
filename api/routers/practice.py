"""
api/routers/practice.py
~~~~~~~~~~~~~~~~~~~~~~~
Practice endpoints (auth required). Thin: validation and delegation to ``services/practice``.

GET    /practice/today          - today's question, computed (writes nothing)
POST   /practice/today/done     - mark it done, optionally with a rating
PATCH  /practice/today/feel     - rate the question marked done
DELETE /practice/today/done     - undo a misclicked done (today only)
POST   /practice/today/swap     - a different question; one a day, then 409
GET    /practice/history        - the calendar and its counts (weeks 1-26)
GET    /practice/challenges            - the open build, when the next opens, the last twelve
GET    /practice/challenges/{slug}     - one build's brief and the caller's own attempt
POST   /practice/challenges/{slug}/start        - press Start: the clock and the proof code
PUT    /practice/challenges/{slug}/submission   - save or replace the submission
GET    /practice/challenges/{slug}/result       - the caller's scores, notes, signals and appeal
POST   /practice/challenges/{slug}/appeal       - one appeal, within seven days of the result
GET    /practice/challenges/{slug}/leaderboard  - one build's board: top 25 and the caller's row
GET    /practice/leaderboard    - the overall board: best four of the last twelve builds
GET    /practice/settings       - leaderboard name, repo link and contest reminder switches
PUT    /practice/settings       - save all three (422 for a name that is not allowed)
GET    /practice/admin/attempts/held            - admin: attempts the judge held
POST   /practice/admin/attempts/{id}/clear      - admin: release a hold
POST   /practice/admin/attempts/{id}/requeue    - admin: back in the judge's queue
POST   /practice/admin/attempts/{id}/override   - admin: set the scores by hand

The services raise ``PracticeConflict``, ``PracticeInvalid`` and ``PracticeNotFound``; each
becomes the status below, carrying a message written for the person using the page.
``PracticeRateLimited`` (raised only by a submission save) is a ``PracticeConflict`` that
becomes 429 with ``Retry-After``; every other ``PracticeConflict`` is 409.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi import Path as PathParam

from api.auth import get_current_user, require_role
from api.schemas.practice_schemas import (
    AppealRequestSchema,
    AttemptResultSchema,
    ChallengeBoardSchema,
    ChallengeSchema,
    ChallengesOverviewSchema,
    DoneRequestSchema,
    FeelRequestSchema,
    HeldAttemptsSchema,
    OverallBoardSchema,
    OverrideRequestSchema,
    PracticeHistorySchema,
    PracticeSettingsSchema,
    PracticeTodaySchema,
    SettingsRequestSchema,
    SubmissionRequestSchema,
)
from services.practice import board, challenges, daily, judge
from services.practice.errors import (
    PracticeConflict,
    PracticeInvalid,
    PracticeNotFound,
    PracticeRateLimited,
)

router = APIRouter(prefix="/practice", tags=["Practice"])


async def get_current_user_id(user: Annotated[dict[str, Any], Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim"
        )
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]
Slug = Annotated[str, PathParam(min_length=1, max_length=96)]


async def _answer(call: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Await a Practice service call, turning its errors into HTTP statuses."""
    try:
        return await call
    except PracticeRateLimited as exc:  # before PracticeConflict, which it subclasses
        raise HTTPException(
            status_code=429, detail=str(exc), headers={"Retry-After": "60"}
        ) from exc
    except PracticeConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PracticeInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PracticeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/today", response_model=PracticeTodaySchema)
async def get_today(user_id: CurrentUserId) -> dict[str, Any]:
    """Today's question for the signed-in user."""
    return await _answer(daily.today(user_id))


@router.post("/today/done", response_model=PracticeTodaySchema)
async def mark_done(
    user_id: CurrentUserId, body: DoneRequestSchema | None = None
) -> dict[str, Any]:
    """Mark today's question done. Repeating it changes nothing."""
    return await _answer(daily.mark_done(user_id, body.feel if body else None))


@router.patch("/today/feel", response_model=PracticeTodaySchema)
async def set_feel(user_id: CurrentUserId, body: FeelRequestSchema) -> dict[str, Any]:
    """The one-tap rating after "Done"."""
    return await _answer(daily.set_feel(user_id, body.feel))


@router.delete("/today/done", response_model=PracticeTodaySchema)
async def undo_done(user_id: CurrentUserId) -> dict[str, Any]:
    """Undo, for today only."""
    return await _answer(daily.undo_done(user_id))


@router.post("/today/swap", response_model=PracticeTodaySchema)
async def swap(user_id: CurrentUserId) -> dict[str, Any]:
    """A different question for today. 409 once it has been used."""
    return await _answer(daily.swap(user_id))


@router.get("/history", response_model=PracticeHistorySchema)
async def get_history(
    user_id: CurrentUserId, weeks: Annotated[int, Query(ge=1, le=26)] = 12
) -> dict[str, Any]:
    """The calendar of practised days and three neutral counts."""
    return await _answer(daily.history(user_id, weeks=weeks))


@router.get("/challenges", response_model=ChallengesOverviewSchema)
async def get_challenges(user_id: CurrentUserId) -> dict[str, Any]:
    """The open build with the caller's attempt, when the next opens, and the last twelve."""
    return await _answer(challenges.challenges_overview(user_id))


@router.get("/challenges/{slug}", response_model=ChallengeSchema)
async def get_challenge(user_id: CurrentUserId, slug: Slug) -> dict[str, Any]:
    """One build's brief and the caller's own attempt. 404 until it has opened."""
    return await _answer(challenges.challenge_detail(user_id, slug))


@router.post("/challenges/{slug}/start", response_model=ChallengeSchema)
async def start_challenge(user_id: CurrentUserId, slug: Slug) -> dict[str, Any]:
    """Press Start. 409 if already started, 422 if too little of the week is left."""
    return await _answer(challenges.start(user_id, slug))


@router.put("/challenges/{slug}/submission", response_model=ChallengeSchema)
async def put_submission(
    user_id: CurrentUserId, slug: Slug, body: SubmissionRequestSchema
) -> dict[str, Any]:
    """Save or replace the submission. 422 on the link, bounds or repo; 409 once the grace hour
    is over; 429 above ten saves a minute."""
    return await _answer(
        challenges.save_submission(
            user_id,
            slug,
            repo_url=body.repo_url,
            writeup=body.writeup.model_dump(),
            video_url=body.video_url,
        )
    )


@router.get("/challenges/{slug}/result", response_model=AttemptResultSchema)
async def get_result(user_id: CurrentUserId, slug: Slug) -> dict[str, Any]:
    """The caller's own result for a build. 404 if they have no attempt at it."""
    return await _answer(judge.attempt_result(user_id, slug))


@router.post("/challenges/{slug}/appeal", response_model=AttemptResultSchema)
async def appeal_result(
    user_id: CurrentUserId, slug: Slug, body: AppealRequestSchema
) -> dict[str, Any]:
    """Appeal a result once, within seven days. The reason goes to a person, never the model.
    409 before a result, once a person has reviewed it, after the first appeal or after the
    window; 422 on the reason."""
    return await _answer(judge.request_appeal(user_id, slug, body.reason))


@router.get("/challenges/{slug}/leaderboard", response_model=ChallengeBoardSchema)
async def get_challenge_board(user_id: CurrentUserId, slug: Slug) -> dict[str, Any]:
    """One build's board: the top 25, the caller's own row, and whether it is still provisional.
    Names are handles unless a person chose one. 404 until the build has opened."""
    return await _answer(board.challenge_board(user_id, slug))


@router.get("/leaderboard", response_model=OverallBoardSchema)
async def get_overall_board(user_id: CurrentUserId) -> dict[str, Any]:
    """The overall board: the top 50 by best four totals of the last twelve builds, and the
    caller's own row."""
    return await _answer(board.overall_board(user_id))


@router.get("/settings", response_model=PracticeSettingsSchema)
async def get_settings(user_id: CurrentUserId) -> dict[str, Any]:
    """The caller's leaderboard name, repo link and contest reminder switches."""
    return await _answer(board.get_settings(user_id))


@router.put("/settings", response_model=PracticeSettingsSchema)
async def put_settings(user_id: CurrentUserId, body: SettingsRequestSchema) -> dict[str, Any]:
    """Save the three switches. A blank name goes by the handle; 422 for a name that is not allowed."""
    return await _answer(
        board.put_settings(
            user_id,
            display_name=body.display_name,
            show_repo=body.show_repo,
            contest_reminder=body.contest_reminder,
        )
    )


async def _acknowledged(call: Awaitable[None]) -> dict[str, Any]:
    """Await an admin action that returns nothing, and answer ``{"ok": true}``."""

    async def done() -> dict[str, Any]:
        await call
        return {"ok": True}

    return await _answer(done())


AdminOnly = Depends(require_role("admin"))


@router.get("/admin/attempts/held", response_model=HeldAttemptsSchema, dependencies=[AdminOnly])
async def list_held_attempts() -> dict[str, Any]:
    """The attempts the judge held, with the id ``clear`` takes. No candidate text."""
    return await _answer(board.held_attempts())


@router.post("/admin/attempts/{attempt_id}/clear", dependencies=[AdminOnly])
async def clear_hold(attempt_id: uuid.UUID) -> dict[str, Any]:
    """Release a hold and put the attempt back in the judge's queue."""
    return await _acknowledged(judge.admin_clear(attempt_id))


@router.post("/admin/attempts/{attempt_id}/requeue", dependencies=[AdminOnly])
async def requeue_attempt(attempt_id: uuid.UUID) -> dict[str, Any]:
    """Give an attempt a fresh set of judge tries, whatever became of it."""
    return await _acknowledged(judge.admin_requeue(attempt_id))


@router.post("/admin/attempts/{attempt_id}/override", dependencies=[AdminOnly])
async def override_attempt(attempt_id: uuid.UUID, body: OverrideRequestSchema) -> dict[str, Any]:
    """Set the final scores by hand, with a note saying why. 409 for a build that is not yet
    submitted or was missed."""
    return await _acknowledged(judge.admin_override(attempt_id, body.scores, body.note))
