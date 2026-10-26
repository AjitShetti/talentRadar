"""
api/routers/outreach.py
~~~~~~~~~~~~~~~~~~~~~~~
Outreach: the people to reach at a company, what has been said to them, and the
follow-ups that are owed.

Thin by design. A handler validates its request (``api/schemas/outreach_schemas.py``),
calls one ``services/outreach`` function and returns what it returns. A refused
request is raised by the service as an ``OutreachError`` carrying its own status and
message; ``_call`` turns it into the HTTP error in one place, for every route.

A thread, person or company id that is not the caller's is a 404, never a 403: the
services filter on the caller, so an id reveals nothing. Nothing here sends a message.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import StringConstraints

from api.auth import get_current_user
from api.schemas.outreach_schemas import (
    AddPersonRequest,
    CreateThreadRequest,
    DraftRequest,
    PackAppliedRequest,
    PackSeenRequest,
    PackSentRequest,
    PatchPersonRequest,
    PatchThreadRequest,
    SettingsRequest,
    TouchRequest,
)
from services.outreach import drafts, pack, people, suggest, threads, touches
from services.outreach.errors import OutreachError
from services.outreach.threads import ThreadPatch

router = APIRouter(prefix="/outreach", tags=["Outreach"])

T = TypeVar("T")


async def get_current_user_id(user: Annotated[dict[str, Any], Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim"
        )
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]

#: At least two characters once trimmed: one letter would scan the whole table.
CompanyQuery = Annotated[
    str, Query(), StringConstraints(strip_whitespace=True, min_length=2, max_length=80)
]


async def _call(awaitable: Awaitable[T]) -> T:
    """Await a service call, answering a refusal with its status and message."""
    try:
        return await awaitable
    except OutreachError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _thread_patch(payload: PatchThreadRequest) -> ThreadPatch:
    """Only what the client sent: an absent key leaves the field alone, ``null`` clears it."""
    sent = payload.model_fields_set
    patch: ThreadPatch = {}
    if "note" in sent:
        patch["note"] = payload.note
    if "next_step" in sent:
        patch["next_step"] = payload.next_step
    if "next_step_at" in sent:
        patch["next_step_at"] = payload.next_step_at
    if "job_id" in sent:
        patch["job_id"] = payload.job_id
    if "application_id" in sent:
        patch["application_id"] = payload.application_id
    if payload.close is not None:
        patch["close_outcome"] = payload.close.outcome
    if payload.reopen:
        patch["reopen"] = True
    return patch


# --- the page and its settings ------------------------------------------------------------


@router.get("")
async def home(user_id: CurrentUserId) -> dict[str, Any]:
    """The Outreach page: today's target, what is due, and every thread."""
    return await _call(touches.home(user_id))


@router.get("/settings")
async def read_settings(user_id: CurrentUserId) -> dict[str, int]:
    return {"daily_target": await _call(touches.get_target(user_id))}


@router.put("/settings")
async def save_settings(payload: SettingsRequest, user_id: CurrentUserId) -> dict[str, int]:
    return {"daily_target": await _call(touches.set_target(user_id, payload.daily_target))}


# --- the daily pack ---------------------------------------------------------------------------


@router.get("/pack")
async def daily_pack(user_id: CurrentUserId) -> dict[str, Any]:
    """Today's pack (IST), built on the day's first read. No model, no network."""
    return await _call(pack.get_pack(user_id))


@router.post("/pack/seen", status_code=status.HTTP_204_NO_CONTENT)
async def pack_seen(payload: PackSeenRequest, user_id: CurrentUserId) -> None:
    """Record that /outreach displayed these items."""
    await _call(pack.mark_seen(user_id, payload.item_ids))


@router.post("/pack/companies/{company_id}/write")
async def write_pack_company(company_id: str, user_id: CurrentUserId) -> dict[str, Any]:
    """Write every message for one company in today's pack (one model call, kept)."""
    return await _call(pack.write_company(user_id, company_id))


@router.post("/pack/items/{item_id}/write")
async def write_pack_item(item_id: str, user_id: CurrentUserId) -> dict[str, Any]:
    """Write a due follow-up in today's pack (kept on the item)."""
    return await _call(pack.write_follow_up(user_id, item_id))


@router.post("/pack/items/{item_id}/sent")
async def pack_item_sent(
    item_id: str, payload: PackSentRequest, user_id: CurrentUserId
) -> dict[str, Any]:
    """Record that the user sent this message themselves. Nothing is sent from here."""
    return await _call(
        pack.mark_sent(user_id, item_id, message=payload.message, subject=payload.subject)
    )


@router.post("/pack/items/{item_id}/skip", status_code=status.HTTP_204_NO_CONTENT)
async def pack_item_skip(item_id: str, user_id: CurrentUserId) -> None:
    await _call(pack.mark_skipped(user_id, item_id))


@router.post("/pack/companies/{company_id}/applied")
async def pack_company_applied(
    company_id: str, payload: PackAppliedRequest, user_id: CurrentUserId
) -> dict[str, Any]:
    """Record that the user applied to one of the company's roles."""
    return await _call(pack.mark_applied(user_id, company_id, payload.job_id))


# --- suggestions and companies -----------------------------------------------------------------


@router.get("/suggestions")
async def list_suggestions(
    user_id: CurrentUserId, limit: Annotated[int, Query(ge=1, le=25)] = 10
) -> dict[str, Any]:
    """Companies worth a message, best first. Ranked from stored facts; no model."""
    return await _call(suggest.suggestions(user_id, limit=limit))


@router.post("/suggestions/{company_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_suggestion(company_id: str, user_id: CurrentUserId) -> None:
    await _call(suggest.dismiss_suggestion(user_id, company_id))


@router.get("/companies/search")
async def search_companies(q: CompanyQuery, user_id: CurrentUserId) -> list[dict[str, Any]]:
    """Up to five stored companies whose name contains ``q``, for "Add a company"."""
    return await _call(threads.search_companies(q))


@router.get("/companies/{company_id}")
async def company_thread(company_id: str, user_id: CurrentUserId) -> dict[str, Any]:
    """The caller's thread with this company, or ``null``, for the company drawer."""
    return {"thread": await _call(threads.thread_for_company(user_id, company_id))}


# --- threads ---------------------------------------------------------------------------------------


@router.post("/threads", status_code=status.HTTP_201_CREATED)
async def open_thread(
    payload: CreateThreadRequest, response: Response, user_id: CurrentUserId
) -> dict[str, Any]:
    """Open a thread: 201, or 200 with the one that was already there."""
    thread, created = await _call(
        threads.create_thread(
            user_id,
            company_id=payload.company_id,
            company_name=payload.company_name,
            job_id=payload.job_id,
            source=payload.source,
        )
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return {"thread": thread, "created": created}


@router.get("/threads/{thread_id}")
async def read_thread(thread_id: str, user_id: CurrentUserId) -> dict[str, Any]:
    return await _call(people.get_thread(user_id, thread_id))


@router.patch("/threads/{thread_id}")
async def patch_thread(
    thread_id: str, payload: PatchThreadRequest, user_id: CurrentUserId
) -> dict[str, Any]:
    await _call(threads.update_thread(user_id, thread_id, _thread_patch(payload)))
    return await _call(people.get_thread(user_id, thread_id))


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(thread_id: str, user_id: CurrentUserId) -> None:
    await _call(threads.delete_thread(user_id, thread_id))


# --- people, touches and drafts -----------------------------------------------------------------------


@router.post("/threads/{thread_id}/people")
async def add_person(
    thread_id: str, payload: AddPersonRequest, user_id: CurrentUserId
) -> dict[str, Any]:
    return await _call(
        people.add_person(
            user_id,
            thread_id,
            contact_id=payload.contact_id,
            name=payload.name,
            title=payload.title,
            email=payload.email,
            linkedin_url=payload.linkedin_url,
        )
    )


@router.patch("/threads/{thread_id}/people/{person_id}")
async def patch_person(
    thread_id: str, person_id: str, payload: PatchPersonRequest, user_id: CurrentUserId
) -> dict[str, Any]:
    return await _call(
        people.update_person(
            user_id,
            thread_id,
            person_id,
            snooze_days=payload.snooze_days,
            state=payload.state,
        )
    )


@router.delete("/threads/{thread_id}/people/{person_id}")
async def remove_person(thread_id: str, person_id: str, user_id: CurrentUserId) -> dict[str, Any]:
    return await _call(people.remove_person(user_id, thread_id, person_id))


@router.post("/threads/{thread_id}/touches")
async def record_touch(
    thread_id: str, payload: TouchRequest, user_id: CurrentUserId
) -> dict[str, Any]:
    """Write down what the user did. A closed thread takes touches too."""
    return await _call(
        touches.record_touch(
            user_id,
            thread_id,
            person_id=payload.person_id,
            channel=payload.channel,
            direction=payload.direction,
            note=payload.note,
            body=payload.body,
            happened_at=payload.happened_at,
            next_step=payload.next_step,
            next_step_at=payload.next_step_at,
        )
    )


@router.post("/threads/{thread_id}/draft")
async def draft_message(
    thread_id: str, payload: DraftRequest, user_id: CurrentUserId
) -> dict[str, Any]:
    """Draft one message (the only route that may call the model). Nothing is sent or stored."""
    return await _call(
        drafts.draft(
            user_id,
            thread_id,
            person_id=payload.person_id,
            fmt=payload.format,
            relationship=payload.relationship,
            issue_url=payload.issue_url,
        )
    )
