"""
services/copilot.py
~~~~~~~~~~~~~~~~~~~
The Career Copilot: the briefing the overview page opens with, and the
conversation that runs on ``/agent`` underneath it.

    build_briefing()  — deterministic cards derived from the user's live state
    chat()            — a user-scoped turn through the LangGraph agent graph

Design notes
------------
* The briefing is *deterministic*. Every card is computed from rows that
  already exist (applications, events, interview sessions) so the page renders
  identically without an LLM and costs one round of queries.
  The LLM is only ever asked to phrase a chat reply, never to decide what the
  user should do next.
* The briefing carries *decisions*, not reports. Anything the overview already
  renders as a full panel — interview score breakdowns, resume-vs-target-role
  skill focus — is deliberately absent here rather than summarised twice.
* Every card carries a stable ``id``. That id is what a dismissal is recorded
  against, so snoozing "follow up on the Swiggy application" hides that one
  card and not the whole stale-application family.
* ``chat()`` is the only path that passes ``user_id`` into the agent graph, so
  the studio agents (career coach, application tracker, personal agent) can
  answer about *this* user rather than erroring out on a missing id.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from services.agent_memory import (
    active_dismissals,
    get_memories,
    recommend_next_action,
    remember,
)
from services.base import parse_uuid
from storage.database import AsyncSessionLocal
from storage.models import (
    ApplicationStatus,
    Company,
    InterviewSession,
    Job,
    JobApplication,
)

if TYPE_CHECKING:
    from agents.state import RetrievalResult

logger = logging.getLogger(__name__)

#: An application sitting in an active stage longer than this needs a nudge.
STALE_AFTER_DAYS = 10
#: A saved job the user never applied to goes stale faster — postings expire.
SAVED_STALE_AFTER_DAYS = 5
#: How far ahead a scheduled interview becomes something to act on today.
SCHEDULED_PREP_WINDOW_DAYS = 3
#: Stages where silence means "chase it", as opposed to a closed outcome.
IN_FLIGHT_STAGES = (
    ApplicationStatus.APPLIED,
    ApplicationStatus.ONLINE_ASSESSMENT,
    ApplicationStatus.SCREENING,
    ApplicationStatus.INTERVIEW,
)
#: Memory type used for preferences the copilot infers from conversation.
PREFERENCE_TYPE = "preference"
#: The briefing card kinds whose title and detail may be sent to the model. An
#: allowlist on purpose: a card kind added later (Outreach, Events, Practice) holds
#: what the user typed or whom they are talking to, so it stays out of the prompt
#: until someone decides it may go in. The Outreach follow-up card holds a person's
#: name and the user's typed next step; it contributes a count and nothing else.
MODEL_VISIBLE_CARD_KINDS = frozenset({
    "closed_role",
    "interview_momentum",
    "priority",
    "saved_backlog",
    "scheduled_interview",
    "stale_application",
})
#: The kind of the one Outreach card for everything owed today.
FOLLOWUP_CARD_KIND = "outreach_followup"


# ─────────────────────────────────────────────────────────────────────────────
# Briefing
# ─────────────────────────────────────────────────────────────────────────────

def _card(
    *,
    card_id: str,
    kind: str,
    title: str,
    detail: str,
    tone: str = "action",
    actions: list[dict[str, str]] | None = None,
    meta: dict[str, Any] | None = None,
    dismissible: bool = True,
) -> dict[str, Any]:
    """Shape one briefing card. Keeps the payload identical across producers."""
    return {
        "id": card_id,
        "kind": kind,
        "title": title,
        "detail": detail,
        "tone": tone,
        "actions": actions or [],
        "meta": meta or {},
        "dismissible": dismissible,
    }


def _days_since(moment: datetime | None) -> int | None:
    """Whole days between ``moment`` and now, tolerating naive timestamps."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max((datetime.now(tz=UTC) - moment).days, 0)


def _closed_role_card(
    *, application_id: str, role: str, company: str, closed_at: datetime
) -> dict[str, Any]:
    """
    A saved role the employer has stopped listing.

    Only raised for roles still in "saved": once someone has applied, a
    posting coming down is ordinary - employers close a listing when they have
    enough applicants - and says nothing about that application.
    """
    return _card(
        card_id=f"closed_role:{application_id}",
        kind="closed_role",
        title=f"{role} at {company} is no longer listed",
        detail=(
            f"The employer's careers site stopped listing it on "
            f"{closed_at.day} {closed_at.strftime('%b %Y')}. Move it out of your "
            "saved roles so it stops competing for your attention."
        ),
        tone="warning",
        actions=[
            {"label": "Open tracker", "href": "/applications", "style": "primary"},
            {"label": "Find similar roles", "href": "/search", "style": "ghost"},
        ],
        meta={"application_id": application_id, "company": company},
    )


async def _application_cards(user_id: str) -> list[dict[str, Any]]:
    """
    Cards for applications that have gone quiet.

    Two distinct problems, deliberately separated: a *saved* job is one the
    user never acted on (the posting may close), while an *in-flight* one is
    waiting on the employer (the user should chase it).
    """
    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        if not user_uuid:
            return []

        result = await session.execute(
            select(JobApplication, Job.title, Company.name, Job.closed_at)
            .outerjoin(Job, Job.id == JobApplication.job_id)
            .outerjoin(Company, Company.id == Job.company_id)
            .where(JobApplication.user_id == user_uuid)
            .order_by(JobApplication.updated_at.asc())
        )
        rows = result.all()

        cards: list[dict[str, Any]] = []
        stale_saved: list[tuple[JobApplication, str | None]] = []

        for app, job_title, company_name, closed_at in rows:
            age = _days_since(app.updated_at)
            if age is None:
                continue
            role = job_title or "a saved role"
            company = company_name or "this company"

            if app.status == ApplicationStatus.SAVED and closed_at is not None:
                # Said once, on its own, and kept out of the saved backlog:
                # "tailor a resume and send one today" is wrong advice for a
                # role that can no longer be applied to.
                cards.append(_closed_role_card(
                    application_id=str(app.id), role=role, company=company,
                    closed_at=closed_at,
                ))
            elif app.status == ApplicationStatus.SAVED and age >= SAVED_STALE_AFTER_DAYS:
                stale_saved.append((app, job_title))
            elif app.status in IN_FLIGHT_STAGES and age >= STALE_AFTER_DAYS:
                cards.append(_card(
                    card_id=f"stale_application:{app.id}",
                    kind="stale_application",
                    title=f"No movement on {role} at {company}",
                    detail=(
                        f"{age} days in “{app.status.value.replace('_', ' ')}” with no update. "
                        "A short follow-up to the recruiter is the highest-leverage thing "
                        "you can do on this one today."
                    ),
                    tone="warning",
                    actions=[
                        {"label": "Open tracker", "href": "/applications", "style": "primary"},
                        {"label": "Find a contact", "href": "/company-intel", "style": "ghost"},
                    ],
                    meta={
                        "application_id": str(app.id),
                        "days_idle": age,
                        "status": app.status.value,
                        "company": company,
                    },
                ))

        # Saved-but-untouched roles collapse into one card — a list of six
        # identical nudges is noise, one card with a count is a decision.
        if stale_saved:
            oldest = _days_since(stale_saved[0][0].updated_at) or 0
            names = [title or "Untitled role" for _, title in stale_saved[:3]]
            cards.append(_card(
                card_id="saved_backlog",
                kind="saved_backlog",
                title=f"{len(stale_saved)} saved role(s) you never applied to",
                detail=(
                    f"Oldest has been sitting for {oldest} days ({', '.join(names)}"
                    f"{'…' if len(stale_saved) > 3 else ''}). Postings close — "
                    "tailor a resume and send one today."
                ),
                tone="action",
                actions=[
                    {"label": "Tailor a resume", "href": "/resume-studio", "style": "primary"},
                    {"label": "Review saved", "href": "/applications", "style": "ghost"},
                ],
                meta={"count": len(stale_saved), "oldest_days": oldest},
            ))
        return cards
    except Exception as exc:  # a broken card must never break the briefing
        logger.warning("Application cards unavailable: %s", exc)
        return []
    finally:
        await session.close()


async def _scheduled_interview_cards(user_id: str) -> list[dict[str, Any]]:
    """
    Interviews already on the calendar.

    This outranks everything else the briefing can say: a stale application
    costs a reply, an interview tomorrow costs the round. Cards are emitted
    only inside the prep window — a date three weeks out is not today's work.
    """
    from services.applications import day_agenda

    agenda = await day_agenda(user_id=user_id, within_days=SCHEDULED_PREP_WINDOW_DAYS)

    cards: list[dict[str, Any]] = []
    for item in agenda:
        days = int(item["days_away"])
        when = str(item["when_label"]).lower()
        cards.append(_card(
            card_id=f"scheduled_interview:{item['application_id']}",
            kind="scheduled_interview",
            title=f"{item['role']} interview at {item['company']} — {when}",
            detail=(
                f"Scheduled for {_clock(item['scheduled_at'])}. "
                + (
                    "Run one mock round on this company's stack before you sit down."
                    if days <= 1
                    else "Block practice time now so the prep isn't the night before."
                )
            ),
            tone="warning" if days <= 1 else "action",
            actions=[
                {"label": "Prep with a mock", "href": "/interview", "style": "primary"},
                {"label": "Research the company", "href": "/company-intel", "style": "ghost"},
            ],
            meta={
                "application_id": item["application_id"],
                "days_away": days,
                "scheduled_at": item["scheduled_at"],
                "company": item["company"],
            },
            dismissible=days > 0,
        ))
    return cards


def _clock(iso: str) -> str:
    """Render a stored ISO timestamp as a readable time, tolerating junk."""
    try:
        return datetime.fromisoformat(iso).strftime("%d %b, %H:%M")
    except ValueError:
        return "a time on your tracker"


async def _interview_card(user_id: str, applied: int) -> dict[str, Any] | None:
    """
    The one interview nudge the briefing owns: applications in flight and no
    practice behind them.

    Score trends deliberately live *outside* the briefing. The overview's
    interview-feedback panel already reports the latest score against the
    running average, the weakest track and the answers that cost the most
    points — a card repeating a thinner version of that is noise.
    """
    if applied == 0:
        return None

    session = AsyncSessionLocal()
    try:
        user_uuid = parse_uuid(user_id)
        if not user_uuid:
            return None

        result = await session.execute(
            select(InterviewSession.id)
            .where(InterviewSession.user_id == user_uuid)
            .where(InterviewSession.completed.is_(True))
            .where(InterviewSession.total_score.is_not(None))
            .limit(1)
        )
        if result.first() is not None:
            return None

        return _card(
            card_id="interview_cold_start",
            kind="interview_momentum",
            title="You've applied, but never practised",
            detail=(
                f"{applied} application(s) are in flight and you have no completed mock "
                "interviews. One 15-minute session now beats cramming the night before."
            ),
            tone="action",
            actions=[{"label": "Start a mock interview", "href": "/interview", "style": "primary"}],
            meta={"applied": applied},
        )
    except Exception as exc:
        logger.warning("Interview card unavailable: %s", exc)
        return None
    finally:
        await session.close()


async def _outreach_cards(user_id: str) -> dict[str, dict[str, Any] | None]:
    """
    The Outreach follow-up and suggestion cards, or neither.

    ``outreach_cards`` already degrades to no cards, so this only guards the call
    itself: one failing producer must not blank the briefing. Imported here because
    ``services.outreach.briefing`` builds its cards with ``_card`` from this module.
    """
    try:
        from services.outreach.briefing import outreach_cards

        return await outreach_cards(user_id)
    except Exception as exc:
        logger.warning("Outreach cards unavailable: %s", type(exc).__name__)
        return {"followup": None, "suggestion": None}


async def build_briefing(*, user_id: str) -> dict[str, Any]:
    """
    Assemble today's briefing.

    Ordering is deliberate: a dated commitment (an interview on the calendar)
    outranks everything, then the follow-ups the user owes on Outreach (a date
    they set themselves), then the single next-best action, then anything
    decaying (stale applications), then the practice nudge, and last the one
    company worth a note. Cards the user dismissed or snoozed are filtered out
    at the end so the id-based dismissal works no matter which producer emitted
    the card.
    """
    next_action = await recommend_next_action(user_id=user_id)
    context = next_action.get("context", {}) if isinstance(next_action, dict) else {}
    outreach = await _outreach_cards(user_id)

    cards: list[dict[str, Any]] = await _scheduled_interview_cards(user_id)

    if followup_card := outreach.get("followup"):
        cards.append(followup_card)

    if next_action.get("recommendation"):
        cards.append(_card(
            card_id=f"priority:{next_action.get('action', 'next')}",
            kind="priority",
            title=str(next_action["recommendation"]),
            detail=str(next_action.get("detail") or ""),
            tone="primary",
            actions=[{
                "label": "Do this now",
                "href": str(next_action.get("href") or "/dashboard"),
                "style": "primary",
            }],
            meta={"action": next_action.get("action")},
            dismissible=False,
        ))

    cards.extend(await _application_cards(user_id))

    interview_card = await _interview_card(user_id, int(context.get("applied", 0) or 0))
    if interview_card:
        cards.append(interview_card)

    if suggestion_card := outreach.get("suggestion"):
        cards.append(suggestion_card)

    dismissed = await active_dismissals(user_id=user_id)
    visible = [c for c in cards if c["id"] not in dismissed]

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "headline": _headline(visible, context),
        "cards": visible,
        "hidden_count": len(cards) - len(visible),
        "stats": {
            "total_applications": context.get("total_applications", 0),
            "saved": context.get("saved", 0),
            "applied": context.get("applied", 0),
            "interviews": context.get("interviews", 0),
            "onboarding_completed": context.get("onboarding_completed", False),
        },
    }


def _follow_ups_due(cards: list[dict[str, Any]]) -> int:
    """How many Outreach follow-ups the briefing says are owed today (0 when none).

    The count is read from the card's ``meta`` and nothing else, so it is safe to
    show anywhere, including in the prompt of ``_general_reply``.
    """
    card = next((c for c in cards if c["kind"] == FOLLOWUP_CARD_KIND), None)
    if card is None:
        return 0
    try:
        return max(int(card["meta"].get("count", 1)), 1)
    except (TypeError, ValueError):
        return 1


def _headline(cards: list[dict[str, Any]], context: dict[str, Any]) -> str:
    """One line summarising the state of the queue."""
    today = [
        c for c in cards
        if c["kind"] == "scheduled_interview" and c["meta"].get("days_away") == 0
    ]
    if today:
        return f"You have {len(today)} interview(s) today — everything else can wait."
    urgent = sum(1 for c in cards if c["tone"] == "warning")
    if urgent:
        return f"{urgent} thing(s) need chasing today."
    owed = _follow_ups_due(cards)
    if owed:
        return f"{owed} follow-up{'s' if owed != 1 else ''} due on Outreach."
    if len(cards) <= 1:
        return "You're on top of things — nothing is going stale."
    total = context.get("total_applications", 0)
    return f"{len(cards)} things worth your attention across {total} application(s)."


# ─────────────────────────────────────────────────────────────────────────────
# Chat
# ─────────────────────────────────────────────────────────────────────────────

#: Prompts offered when the thread is empty — each maps to a distinct intent.
STARTERS = [
    "What should I focus on today?",
    "Which of my applications have gone cold?",
    "Find senior backend roles in Bengaluru",
    "What skills am I missing for my target roles?",
]


_MARKDOWN_NOISE = re.compile(r"(\*\*|__|`|^#{1,6}\s*|^\s*[-*]\s+)", re.MULTILINE)


def _plain(text: str) -> str:
    """
    Strip markdown from a reply before it reaches a chat bubble.

    The RAG agent formats its summary for a document, not a conversation, so
    its bullets and bold markers would otherwise render as literal asterisks.
    """
    cleaned = _MARKDOWN_NOISE.sub("", text or "")
    lines = [line.strip() for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _job_payload(result: RetrievalResult) -> dict[str, Any]:
    """Serialise a RetrievalResult for the chat transcript."""
    return {
        "id": result.job_id,
        "title": result.title,
        "company": result.company,
        "location": result.location,
        "is_remote": result.is_remote,
        "skills": (result.skills or [])[:6],
        "source_url": result.source_url,
        "score": result.score,
    }


async def _remember_preferences(user_id: str, context: dict[str, Any]) -> None:
    """
    Capture what a search-style question implies about the user.

    Only structured signals the classifier already extracted are stored, and
    only once — the copilot should look like it noticed something, not like it
    logged every keystroke.
    """
    location = context.get("location")
    skills = [s for s in (context.get("skills") or []) if s][:4]
    seniority = context.get("seniority")
    if not location and not skills:
        return

    # Reads back as a sentence: "Interested in senior backend roles in Bengaluru".
    descriptor = " ".join(part for part in (
        str(seniority).replace("_", " ") if seniority else "",
        ", ".join(skills),
    ) if part)
    content = f"Interested in {descriptor} roles".replace("  ", " ").strip()
    if location:
        content += f" in {location}"

    existing = await get_memories(user_id=user_id, memory_type=PREFERENCE_TYPE, limit=50)
    if any((m.get("content") or "").lower() == content.lower() for m in existing):
        return

    await remember(
        user_id=user_id,
        memory_type=PREFERENCE_TYPE,
        content=content,
        metadata={"source": "chat", "location": location, "skills": skills},
    )


async def chat(
    *,
    user_id: str,
    message: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Run one conversational turn.

    The agent graph does the routing; this function is responsible for turning
    whatever came back — job rows, a funnel dict, a learning plan, or nothing —
    into a sentence a person can read, and for grounding the general-chat case
    in the user's actual numbers instead of letting the model invent them.
    """
    from agents.orchestrator import Orchestrator
    from agents.state import IntentType

    message = (message or "").strip()
    if not message:
        return {"intent": "general", "reply": "Ask me anything about your search.", "jobs": []}

    try:
        response = await Orchestrator().process_query(query=message, user_id=user_id)
    except Exception as exc:
        logger.error("Copilot chat failed: %s", exc, exc_info=True)
        return {
            "intent": "general",
            "reply": "I couldn't reach my reasoning engine just now. Try again in a moment.",
            "jobs": [],
            "error": str(exc),
        }

    intent = response.intent.value
    data = (response.metadata or {}).get("data")
    jobs = [_job_payload(r) for r in response.results]

    if intent in (IntentType.SEARCH_JOBS.value, IntentType.FIND_CANDIDATES.value):
        await _remember_preferences(user_id, (response.metadata or {}).get("context", {}))
        if jobs:
            reply = _plain(response.summary or "") or (
                f"I found {len(jobs)} role(s) worth a look. The closest match is "
                f"{jobs[0]['title']} at {jobs[0]['company']}."
            )
        else:
            reply = (
                "Nothing in the index matches that yet. Try widening the location or "
                "seniority — TalentRadar only tracks roles hiring in India."
            )
        return {"intent": intent, "reply": reply, "jobs": jobs, "data": None}

    # Studio intents answer with structured data; ask the model to narrate it.
    if data or response.summary:
        reply = await _narrate(message, intent, data, response.summary)
        return {"intent": intent, "reply": reply, "jobs": jobs, "data": data}

    # GENERAL / unroutable — ground the answer in the user's real state.
    return {
        "intent": intent,
        "reply": await _general_reply(user_id, message, history),
        "jobs": [],
        "data": None,
    }


async def _narrate(
    message: str,
    intent: str,
    data: object,
    summary: str | None,
) -> str:
    """Turn a structured agent result into a short, readable answer."""
    from services.llm import generate_copilot_reply

    try:
        return _plain(await generate_copilot_reply(
            question=message,
            context={"intent": intent, "result": data, "summary": summary},
        ))
    except Exception as exc:
        logger.warning("Reply narration failed, falling back to summary: %s", exc)
        return _plain(summary or "") or "Here's what I found in your workspace."


async def _general_reply(
    user_id: str,
    message: str,
    history: list[dict[str, str]] | None,
) -> str:
    """Answer an open question using the user's briefing as the only facts."""
    from services.llm import generate_copilot_reply

    briefing = await build_briefing(user_id=user_id)
    memories = await get_memories(user_id=user_id, memory_type=PREFERENCE_TYPE, limit=10)

    # Only cleared kinds go in by title and detail; Outreach adds a count, never a name.
    visible = [c for c in briefing["cards"] if c["kind"] in MODEL_VISIBLE_CARD_KINDS]
    context: dict[str, Any] = {
        "intent": "general",
        "stats": briefing["stats"],
        "open_items": [{"title": c["title"], "detail": c["detail"]} for c in visible[:4]],
        "known_preferences": [m.get("content") for m in memories],
        "recent_turns": (history or [])[-4:],
    }
    if owed := _follow_ups_due(briefing["cards"]):
        context["outreach_follow_ups_due"] = owed

    try:
        return await generate_copilot_reply(question=message, context=context)
    except Exception as exc:
        logger.warning("General reply failed: %s", exc)
        cards = briefing["cards"]
        if cards:
            return f"{briefing['headline']} Start with: {cards[0]['title']}."
        return "I don't have enough activity yet to advise you. Save a few roles and ask again."
