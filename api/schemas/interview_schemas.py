"""
api/schemas/interview_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pydantic V2 request/response schemas for the mock interview API.

Design notes
------------
* Uses model_config = ConfigDict(from_attributes=True) on response models
  so SQLAlchemy ORM instances can be directly passed to model_validate().
* All UUIDs are serialised as strings for JSON transport.
* ``InterviewAgentStateSchema`` mirrors InterviewAgentState TypedDict so the
  frontend can submit the full state back on each turn without the backend
  needing to maintain in-memory sessions.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums (mirrors storage.models enums as plain strings for validation)
# ---------------------------------------------------------------------------

VALID_TRACKS = {"python_dsa", "python_backend", "sql", "system_design"}
VALID_DIFFICULTIES = {"beginner", "mid", "senior"}


# ---------------------------------------------------------------------------
# Session start
# ---------------------------------------------------------------------------

class StartSessionRequest(BaseModel):
    """Request body to start a new mock interview session."""

    track: str = Field(
        ...,
        description="Interview track: python_dsa | python_backend | sql | system_design",
    )
    difficulty: str = Field(
        ...,
        description="Difficulty level: beginner | mid | senior",
    )

    @field_validator("track")
    @classmethod
    def validate_track(cls, v: str) -> str:
        if v not in VALID_TRACKS:
            raise ValueError(f"track must be one of {sorted(VALID_TRACKS)}")
        return v

    @field_validator("difficulty")
    @classmethod
    def validate_difficulty(cls, v: str) -> str:
        if v not in VALID_DIFFICULTIES:
            raise ValueError(f"difficulty must be one of {sorted(VALID_DIFFICULTIES)}")
        return v


class StartSessionResponse(BaseModel):
    """Returned after a session is created. Includes the first question."""

    session_id: str = Field(..., description="UUID of the created InterviewSession")
    question: str = Field(..., description="First question text (to be spoken via TTS)")
    question_index: int = Field(0, description="0-based question index")
    is_followup: bool = Field(False)
    # Full agent state — returned so the frontend can submit it back next turn
    agent_state: dict[str, Any] = Field(
        ..., description="Full InterviewAgentState for stateless round-trips"
    )


# ---------------------------------------------------------------------------
# Submit answer (text or transcribed voice)
# ---------------------------------------------------------------------------

class SubmitAnswerRequest(BaseModel):
    """Request body when the user submits an answer (typed or voice-transcribed)."""

    session_id: str = Field(..., description="UUID of the active session")
    answer: str = Field(
        ...,
        min_length=1,
        max_length=8192,
        description="The user's answer text",
    )
    # The agent state returned by the previous response — round-tripped for statelesness
    agent_state: dict[str, Any] = Field(
        ..., description="Full InterviewAgentState from the previous response"
    )

    @field_validator("answer")
    @classmethod
    def strip_answer(cls, v: str) -> str:
        return v.strip()


class AnswerScoreSchema(BaseModel):
    """Sub-score breakdown for one answer."""

    correctness: float = Field(..., ge=0, le=10)
    clarity: float = Field(..., ge=0, le=10)
    depth: float = Field(..., ge=0, le=10)
    answer_summary: str | None = None


class SubmitAnswerResponse(BaseModel):
    """Returned after an answer is evaluated."""

    session_id: str
    question: str = Field(..., description="Next question text (or closing message if done)")
    question_index: int
    is_followup: bool
    score: AnswerScoreSchema
    session_complete: bool = Field(False)
    agent_state: dict[str, Any] = Field(
        ..., description="Updated InterviewAgentState for next round-trip"
    )


# ---------------------------------------------------------------------------
# End session early (user clicks "End Interview")
# ---------------------------------------------------------------------------

class EndSessionRequest(BaseModel):
    """Request body when the user manually ends a session."""

    session_id: str
    agent_state: dict[str, Any] = Field(
        ..., description="Current InterviewAgentState for score computation"
    )


class FinalScoreSchema(BaseModel):
    """Final score breakdown returned at session end."""

    total_score: float = Field(..., ge=0, le=100, description="Aggregate 0–100 score")
    correctness: float = Field(..., ge=0, le=100)
    clarity: float = Field(..., ge=0, le=100)
    depth: float = Field(..., ge=0, le=100)
    questions_answered: int


class EndSessionResponse(BaseModel):
    """Returned when a session is concluded (gracefully or manually)."""

    session_id: str
    completed: bool
    final_score: FinalScoreSchema
    closing_message: str


# ---------------------------------------------------------------------------
# Session history
# ---------------------------------------------------------------------------

class SessionSummarySchema(BaseModel):
    """One entry in the user's interview history list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    track: str
    difficulty: str
    total_score: float | None = None
    completed: bool
    duration_seconds: int | None = None
    created_at: str  # ISO-8601 string

    @field_validator("created_at", mode="before")
    @classmethod
    def serialize_datetime(cls, v: Any) -> str:
        """Convert datetime to ISO string for JSON transport."""
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)

    @field_validator("id", mode="before")
    @classmethod
    def serialize_uuid(cls, v: Any) -> uuid.UUID:
        if isinstance(v, str):
            return uuid.UUID(v)
        return v


class SessionHistoryResponse(BaseModel):
    """Paginated list of past sessions for the history page."""

    sessions: list[SessionSummarySchema]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Voice / STT
# ---------------------------------------------------------------------------

class TranscribeResponse(BaseModel):
    """Returned after a voice chunk is transcribed via Groq Whisper."""

    transcript: str = Field(..., description="Transcribed text")
    confidence: float | None = Field(
        None,
        description="Confidence score if available from the STT provider",
    )
    provider: str = Field(
        "groq_whisper",
        description="Which STT provider was used (groq_whisper | browser_fallback)",
    )


class AnswerScoreDetailSchema(BaseModel):
    """Full per-question score for session detail page."""
    question_index: int
    question_text: str
    answer_summary: str | None
    score_correctness: float
    score_clarity: float
    score_depth: float
    was_followup: bool


class SessionDetailResponse(BaseModel):
    """Full session with all per-question scores."""
    id: str
    track: str
    difficulty: str
    total_score: float | None
    completed: bool
    duration_seconds: int | None
    created_at: datetime
    score_breakdown: dict[str, float]
    answer_scores: list[AnswerScoreDetailSchema]
