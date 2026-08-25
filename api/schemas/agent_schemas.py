"""
api/schemas/agent_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Request/response models for the Career Copilot (``/api/v1/agent``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RememberRequestSchema(BaseModel):
    """Teach the copilot something it should keep."""
    memory_type: str = Field(
        default="preference",
        description="preference | goal | note | application",
        max_length=64,
    )
    content: str = Field(..., min_length=1, max_length=2000)
    metadata: dict[str, Any] | None = None


class BriefingActionSchema(BaseModel):
    """A button on a briefing card."""
    label: str
    href: str
    style: str = "ghost"


class BriefingCardSchema(BaseModel):
    """One item in today's briefing."""
    id: str
    kind: str
    title: str
    detail: str
    tone: str
    actions: list[BriefingActionSchema] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    dismissible: bool = True


class BriefingResponseSchema(BaseModel):
    """The default state of the copilot page."""
    generated_at: str
    headline: str
    cards: list[BriefingCardSchema] = Field(default_factory=list)
    hidden_count: int = 0
    stats: dict[str, Any] = Field(default_factory=dict)


class DismissRequestSchema(BaseModel):
    """Hide a card, optionally only for a while."""
    card_id: str = Field(..., min_length=1, max_length=200)
    snooze_days: int | None = Field(
        default=None, ge=1, le=90,
        description="Bring the card back after N days; omit to dismiss for good.",
    )


class ChatTurnSchema(BaseModel):
    """One prior turn, replayed for conversational continuity."""
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=4000)


class ChatRequestSchema(BaseModel):
    """A message to the copilot."""
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[ChatTurnSchema] = Field(default_factory=list, max_length=20)


class ChatJobSchema(BaseModel):
    """A job surfaced inside a chat reply."""
    id: str
    title: str
    company: str | None = None
    location: str | None = None
    is_remote: bool = False
    skills: list[str] = Field(default_factory=list)
    source_url: str | None = None
    score: float | None = None


class ChatResponseSchema(BaseModel):
    """The copilot's answer for one turn."""
    intent: str
    reply: str
    jobs: list[ChatJobSchema] = Field(default_factory=list)
    data: Any | None = None
    error: str | None = None
