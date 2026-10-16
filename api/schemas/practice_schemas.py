"""
api/schemas/practice_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Request and response models for Practice (``/api/v1/practice``).

The response models mirror what ``services/practice`` returns and what the Practice page
types in ``frontend/lib/api.ts``. They list every field a route may send, so a field that
is not named here (a streak, say) cannot reach a client by accident.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from domain.practice_pick import Difficulty, Feel

# --------------------------------------------------------------------------- requests


class DoneRequestSchema(BaseModel):
    """Mark today's question done, optionally with the one-tap rating."""

    feel: Feel | None = None


class FeelRequestSchema(BaseModel):
    """Rate the question that was marked done."""

    feel: Feel


# --------------------------------------------------------------------------- today


class PracticeQuestionSchema(BaseModel):
    """A pointer to a LeetCode problem. Never its statement."""

    id: int
    slug: str
    title: str
    difficulty: Difficulty
    topics: list[str]
    url: str


class ContestSchema(BaseModel):
    """The weekly LeetCode contest. ``show`` is true from Saturday 20:00 IST until it ends."""

    starts_at: str
    ends_at: str
    show: bool
    live: bool
    url: str


class PracticeTodaySchema(BaseModel):
    """The day's question, whether it is done, and the links beside it."""

    day: str = Field(description="The IST calendar date, YYYY-MM-DD")
    catalogue: Literal["ok", "empty"]
    catalogue_size: int
    track: str
    track_label: str
    question: PracticeQuestionSchema | None
    reason: str
    relaxed: list[str]
    done_at: str | None
    feel: Feel | None
    swaps_left: int
    leetcode_daily_url: str
    contest: ContestSchema


# --------------------------------------------------------------------------- history


class HistoryDaySchema(BaseModel):
    """One calendar cell. ``difficulty`` and ``topic`` are set only on a practised day."""

    day: str
    done: bool
    difficulty: str | None
    topic: str | None


class HistoryCountsSchema(BaseModel):
    """Three neutral counts. There is deliberately no current streak."""

    this_month: int
    last_30: int
    longest_run: int


class TopicCountSchema(BaseModel):
    topic: str
    count: int


class PracticeHistorySchema(BaseModel):
    days: list[HistoryDaySchema]
    counts: HistoryCountsSchema
    topics: list[TopicCountSchema]
