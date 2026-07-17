"""
api/routers/interview.py
~~~~~~~~~~~~~~~~~~~~~~~~~
FastAPI router for the mock interview feature.

Endpoints
---------
POST   /interview/sessions/start          Start a new session, get first question
POST   /interview/sessions/answer         Submit an answer, get next question + score
POST   /interview/sessions/end            Gracefully end a session early
GET    /interview/sessions/history        List current user's past sessions
POST   /interview/voice/transcribe        Transcribe audio blob via Groq Whisper

Design decisions
----------------
* All state is managed client-side (stateless backend).  The frontend receives
  the full ``agent_state`` dict in every response and submits it back with the
  next request.  This keeps the backend horizontally scalable.
* JWT authentication uses the existing auth dependency from api/routers/auth.py.
* Database writes are handled here via InterviewRepository (not in the agent
  nodes) so the agent graph stays pure and testable.
* Groq Whisper STT errors fall back to "browser_fallback" — the response still
  returns 200 with an empty transcript so the client can activate Web Speech API.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from agents.interview.graph import interview_graph
from agents.interview.nodes import (
    node_end_session,
    node_evaluate_answer,
    node_generate_question,
)
from agents.interview.state import InterviewAgentState
from api.schemas.interview_schemas import (
    AnswerScoreSchema,
    EndSessionRequest,
    EndSessionResponse,
    FinalScoreSchema,
    SessionHistoryResponse,
    SessionSummarySchema,
    StartSessionRequest,
    StartSessionResponse,
    SubmitAnswerRequest,
    SubmitAnswerResponse,
    TranscribeResponse,
)
from api.utils.voice_pipeline import STTError, VoicePipeline
from storage.database import get_db_dep
from storage.interview_repository import InterviewRepository
from storage.models import InterviewDifficulty, InterviewTrack

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interview", tags=["Interview"])

# ---------------------------------------------------------------------------
# Auth dependency (reuses the existing JWT bearer pattern)
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=True)


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> str:
    """
    Validate JWT and return user_id string.

    Reuses the same JWT secret and algorithm as the rest of the API.
    Returns the ``sub`` claim which is the user's UUID as a string.
    """
    from jose import JWTError, jwt
    from config.settings import get_settings

    settings = get_settings()
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing sub claim",
            )
        return user_id
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Dependency shortcuts
# ---------------------------------------------------------------------------

CurrentUserId = Annotated[str, Depends(get_current_user_id)]
DBSession = Annotated[AsyncSession, Depends(get_db_dep)]


# ---------------------------------------------------------------------------
# POST /interview/sessions/start
# ---------------------------------------------------------------------------

@router.post(
    "/sessions/start",
    response_model=StartSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new mock interview session",
    description=(
        "Creates a DB record for the session, runs the LangGraph interview agent "
        "to generate the first question, and returns the full agent state for "
        "stateless round-trips."
    ),
)
async def start_session(
    body: StartSessionRequest,
    user_id: CurrentUserId,
    db: DBSession,
) -> StartSessionResponse:
    """Start a new interview session and return the first question."""

    # -- Persist the session row ---------------------------------------- #
    repo = InterviewRepository()
    try:
        session = await repo.create_session(
            db,
            user_id=uuid.UUID(user_id),
            track=InterviewTrack(body.track),
            difficulty=InterviewDifficulty(body.difficulty),
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error("Failed to create interview session", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not create session: {exc}",
        ) from exc

    # -- Build initial state and run node_generate_question -------------- #
    initial_state: InterviewAgentState = {
        "track": body.track,
        "difficulty": body.difficulty,
        "user_id": user_id,
        "conversation_history": [],
        "question_index": 0,
        "followup_count": 0,
        "scores": [],
        "session_complete": False,
        "error": None,
    }

    try:
        updated_state = await node_generate_question(initial_state)
        agent_state = {**initial_state, **updated_state}
    except Exception as exc:
        logger.error("Failed to generate first question", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not generate first question: {exc}",
        ) from exc

    return StartSessionResponse(
        session_id=str(session.id),
        question=agent_state.get("current_question", ""),
        question_index=agent_state.get("question_index", 0),
        is_followup=False,
        agent_state=agent_state,
    )


# ---------------------------------------------------------------------------
# POST /interview/sessions/answer
# ---------------------------------------------------------------------------

@router.post(
    "/sessions/answer",
    response_model=SubmitAnswerResponse,
    summary="Submit an answer and get the next question + score",
    description=(
        "Evaluates the user's answer, persists the score, and returns the next "
        "question (or the session closing message if done). The agent_state from "
        "the previous response must be submitted back."
    ),
)
async def submit_answer(
    body: SubmitAnswerRequest,
    user_id: CurrentUserId,
    db: DBSession,
) -> SubmitAnswerResponse:
    """Evaluate answer, save score, and return the next question."""

    # -- Rehydrate agent state ------------------------------------------ #
    state: InterviewAgentState = {
        **body.agent_state,
        "current_answer": body.answer,
    }

    repo = InterviewRepository()
    session_id = uuid.UUID(body.session_id)

    # -- Evaluate the answer -------------------------------------------- #
    try:
        eval_update = await node_evaluate_answer(state)
        state = {**state, **eval_update}
    except Exception as exc:
        logger.error("Answer evaluation failed", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Evaluation failed: {exc}",
        ) from exc

    last_score = state.get("last_score", {})

    # -- Persist the score record --------------------------------------- #
    try:
        scores_list = state.get("scores", [])
        latest = scores_list[-1] if scores_list else {}
        await repo.save_answer_score(
            db,
            session_id=session_id,
            question_index=latest.get("question_index", 0),
            question_text=latest.get("question_text", ""),
            score_correctness=last_score.get("correctness", 0.0),
            score_clarity=last_score.get("clarity", 0.0),
            score_depth=last_score.get("depth", 0.0),
            answer_summary=last_score.get("answer_summary"),
            was_followup=latest.get("was_followup", False),
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error("Failed to persist answer score", exc_info=True)
        # Non-fatal — session continues even if score persistence fails

    # -- Determine next action and generate next question/message -------- #
    next_action = state.get("next_action", "next_question")
    session_complete = state.get("session_complete", False)

    if next_action == "end" or session_complete:
        # Generate the closing message and finalise in DB
        end_update = await node_end_session(state)
        state = {**state, **end_update}
        next_question = state.get("conversation_history", [{}])[-1].get("content", "")

        # Persist final score
        try:
            total = await repo.compute_total_score(db, session_id)
            await repo.complete_session(
                db,
                session_id=session_id,
                total_score=total,
            )
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.error("Failed to complete session", exc_info=True)

        session_complete = True

    elif next_action == "followup":
        from agents.interview.nodes import node_generate_followup
        followup_update = await node_generate_followup(state)
        state = {**state, **followup_update}
        next_question = state.get("current_question", "")

    else:  # next_question
        q_update = await node_generate_question(state)
        state = {**state, **q_update}
        next_question = state.get("current_question", "")

    return SubmitAnswerResponse(
        session_id=body.session_id,
        question=next_question,
        question_index=state.get("question_index", 0),
        is_followup=state.get("is_followup", False),
        score=AnswerScoreSchema(
            correctness=last_score.get("correctness", 0.0),
            clarity=last_score.get("clarity", 0.0),
            depth=last_score.get("depth", 0.0),
            answer_summary=last_score.get("answer_summary"),
        ),
        session_complete=session_complete,
        agent_state=state,
    )


# ---------------------------------------------------------------------------
# POST /interview/sessions/end
# ---------------------------------------------------------------------------

@router.post(
    "/sessions/end",
    response_model=EndSessionResponse,
    summary="End a session early",
    description=(
        "Marks the session as abandoned and computes a partial score from "
        "whatever answers were already submitted."
    ),
)
async def end_session(
    body: EndSessionRequest,
    user_id: CurrentUserId,
    db: DBSession,
) -> EndSessionResponse:
    """Manually end a session and get the final score."""

    session_id = uuid.UUID(body.session_id)
    repo = InterviewRepository()

    # Run the end node to get the closing message
    state: InterviewAgentState = body.agent_state
    end_update = await node_end_session(state)
    state = {**state, **end_update}
    closing = state.get("conversation_history", [{}])[-1].get("content", "")

    # Compute final score
    try:
        total = await repo.compute_total_score(db, session_id)
        breakdown = await repo.compute_score_breakdown(db, session_id)
        scores_list = state.get("scores", [])
        await repo.abandon_session(
            db,
            session_id=session_id,
            partial_score=total,
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error("Failed to finalise ended session", exc_info=True)
        total = 0.0
        breakdown = {"correctness": 0.0, "clarity": 0.0, "depth": 0.0}
        scores_list = state.get("scores", [])

    return EndSessionResponse(
        session_id=body.session_id,
        completed=False,  # user ended early → not "completed"
        final_score=FinalScoreSchema(
            total_score=total,
            correctness=breakdown.get("correctness", 0.0),
            clarity=breakdown.get("clarity", 0.0),
            depth=breakdown.get("depth", 0.0),
            questions_answered=len(scores_list),
        ),
        closing_message=closing,
    )


# ---------------------------------------------------------------------------
# GET /interview/sessions/history
# ---------------------------------------------------------------------------

@router.get(
    "/sessions/history",
    response_model=SessionHistoryResponse,
    summary="List current user's past sessions",
)
async def get_session_history(
    user_id: CurrentUserId,
    db: DBSession,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> SessionHistoryResponse:
    """Return paginated interview history for the authenticated user."""

    repo = InterviewRepository()
    try:
        sessions = await repo.get_session_history(
            db,
            uuid.UUID(user_id),
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.error("Failed to fetch session history", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not fetch history: {exc}",
        ) from exc

    return SessionHistoryResponse(
        sessions=[
            SessionSummarySchema.model_validate(s) for s in sessions
        ],
        total=len(sessions),
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# POST /interview/voice/transcribe
# ---------------------------------------------------------------------------

@router.post(
    "/voice/transcribe",
    response_model=TranscribeResponse,
    summary="Transcribe a recorded audio blob via Groq Whisper",
    description=(
        "Accepts a raw audio file (webm/wav/mp4/m4a) from the browser's "
        "MediaRecorder API and returns the transcription. On Groq rate-limit, "
        "returns provider='browser_fallback' with an empty transcript so the "
        "client activates the Web Speech API silently."
    ),
)
async def transcribe_audio(
    user_id: CurrentUserId,
    audio: UploadFile = File(
        ...,
        description="Audio recording from the browser (webm, wav, mp4, m4a)",
    ),
) -> TranscribeResponse:
    """Transcribe audio using Groq Whisper with browser fallback."""

    pipeline = VoicePipeline()

    try:
        audio_bytes = await audio.read()
        filename = audio.filename or "audio.webm"
        transcript, provider = await pipeline.transcribe_audio(
            audio_bytes, filename=filename
        )
    except STTError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.error("Transcription endpoint error", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transcription failed: {exc}",
        ) from exc

    return TranscribeResponse(
        transcript=transcript,
        confidence=None,  # Groq Whisper v3 doesn't expose confidence
        provider=provider,
    )
