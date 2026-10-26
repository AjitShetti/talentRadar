"""
api/schemas/outreach_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Request bodies for ``/api/v1/outreach``.

Every bound comes from the constant the service enforces, so the two cannot drift.
The services still check what they are handed (they are called from elsewhere too);
these models only refuse the obviously malformed early, with a 422 and no database
work.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from domain.outreach import (
    BODY_MAX,
    MAX_DAILY_TARGET,
    NEXT_STEP_MAX,
    NOTE_MAX,
    SNOOZE_MAX_DAYS,
    Channel,
    Outcome,
    Source,
)
from domain.outreach_drafts import SUBJECT_MAX, DraftFormat
from services.outreach.pack import MAX_SEEN_IDS
from services.outreach.people import EMAIL_MAX, LINKEDIN_URL_MAX, NAME_MAX, TITLE_MAX
from services.outreach.threads import COMPANY_NAME_MAX


class CreateThreadRequest(BaseModel):
    """Open a thread with a stored company (``company_id``) or a typed name."""

    company_id: str | None = None
    company_name: str | None = Field(None, max_length=COMPANY_NAME_MAX)
    job_id: str | None = None
    source: Source = "drawer"

    @model_validator(mode="after")
    def _needs_a_company(self) -> Self:
        if not self.company_id and not (self.company_name or "").strip():
            raise ValueError("Give a company_id or a company_name.")
        return self


class CloseThread(BaseModel):
    outcome: Outcome


class PatchThreadRequest(BaseModel):
    """Change a thread. A key that is absent is left alone; ``null`` clears the
    ones that can be cleared. ``close`` closes the thread, ``reopen`` undoes that."""

    note: str | None = Field(None, max_length=NOTE_MAX)
    next_step: str | None = Field(None, max_length=NEXT_STEP_MAX)
    next_step_at: date | None = None
    job_id: str | None = None
    application_id: str | None = None
    close: CloseThread | None = None
    reopen: bool = False


class AddPersonRequest(BaseModel):
    """Either a stored ``contact_id``, or the fields of someone the user knows."""

    contact_id: str | None = None
    name: str | None = Field(None, max_length=NAME_MAX)
    title: str | None = Field(None, max_length=TITLE_MAX)
    email: str | None = Field(None, max_length=EMAIL_MAX)
    linkedin_url: str | None = Field(None, max_length=LINKEDIN_URL_MAX)


class PatchPersonRequest(BaseModel):
    """Snooze a follow-up, or drop the person. Exactly one."""

    snooze_days: int | None = Field(None, ge=1, le=SNOOZE_MAX_DAYS)
    state: Literal["dropped"] | None = None


class TouchRequest(BaseModel):
    """What the user did: a message they sent (``out``) or an answer they got (``in``)."""

    person_id: str | None = None
    channel: Channel
    direction: Literal["out", "in"]
    note: str | None = Field(None, max_length=NOTE_MAX)
    body: str | None = Field(None, max_length=BODY_MAX)
    happened_at: datetime | None = None
    next_step: str | None = Field(None, max_length=NEXT_STEP_MAX)
    next_step_at: date | None = None


class DraftRequest(BaseModel):
    person_id: str | None = None
    format: DraftFormat
    relationship: str | None = Field(None, max_length=40)
    issue_url: str | None = Field(None, max_length=512)


class SettingsRequest(BaseModel):
    daily_target: int = Field(..., ge=0, le=MAX_DAILY_TARGET)


class PackSeenRequest(BaseModel):
    item_ids: list[str] = Field(..., max_length=MAX_SEEN_IDS)


class PackSentRequest(BaseModel):
    """What the user sent, as they edited it."""

    message: str = Field(..., min_length=1, max_length=BODY_MAX)
    subject: str | None = Field(None, max_length=SUBJECT_MAX)


class PackAppliedRequest(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=64)
