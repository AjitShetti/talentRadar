"""
agents/graph.py
~~~~~~~~~~~~~~~
LangGraph state machine for the TalentRadar agent pipeline.

Graph topology:
    classify → route_by_intent → node_rag_retrieve  → route_after_retrieval
                                                       ↘ node_live_search ↘
                                                         node_merge_rank → END
                                                                           (search_jobs,
                                                                            find_candidates)
                              ↘ node_studio_agent → END   (company_info, career_coach,
                                                             application_tracker,
                                                             personal_agent, resume_studio)
                              ↘ node_general      → END   (general, interview_prep)
                              ↘ node_error        → END   (unrouted intent — a wiring bug)

Every IntentType member is routed. ``node_error`` is unreachable for known
intents by construction and exists only to make a missing route loud.

Each node receives and returns the full AgentState dict.
The compiled graph is exposed as ``agent_graph`` for use in the API layer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from agents.state import IntentType, QueryContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

# TypedDict is the canonical LangGraph state type
from typing import TypedDict


class AgentState(TypedDict, total=False):
    """State that flows through every node in the graph."""
    # Input
    query: str
    user_id: str | None

    # Intent classification output
    intent: str          # IntentType value string
    context: dict[str, Any]  # serialised QueryContext

    # Retrieval output
    retrieved_jobs: list[dict[str, Any]]
    total_retrieved: int

    # Live sourcing output. ``sourcing_decision`` carries {sourced, reason} so
    # the API can say *why* a query did or did not go out to the internet
    # rather than leaving it invisible.
    live_jobs: list[dict[str, Any]]
    sources_stats: dict[str, Any]
    sourcing_decision: dict[str, Any]
    force_refresh: bool

    # Generation output
    summary: str | None
    final_response: dict[str, Any]  # serialised AgentResponse

    # Error
    error: str | None


# ---------------------------------------------------------------------------
# Node functions  (each takes AgentState, returns partial AgentState update)
# ---------------------------------------------------------------------------

async def node_classify(state: AgentState) -> AgentState:
    """Classify user intent and extract query context via Groq LLM.

    Classification is best-effort. A Groq outage or rate limit used to raise
    straight out of ``ainvoke``, so every caller saw a 500 instead of the
    graceful reply the general node can still produce from local state.
    """
    from agents.orchestrator import Orchestrator  # local import avoids cycles

    try:
        orchestrator = Orchestrator()
        context: QueryContext = await orchestrator._classify_intent(state["query"])
    except Exception as exc:
        logger.warning("Intent classification failed, falling back to general: %s", exc)
        return {
            "intent": IntentType.GENERAL.value,
            "context": {"raw_query": state.get("query", ""), "keywords": [], "skills": []},
        }

    return {
        "intent": context.intent.value,
        "context": {
            "raw_query": context.raw_query,
            "keywords": context.keywords,
            "skills": context.skills,
            "location": context.location,
            "is_remote": context.is_remote,
            "seniority": context.seniority,
            "employment_type": context.employment_type,
            "company": context.company,
            "limit": context.limit,
            "offset": context.offset,
        },
    }


async def node_rag_retrieve(state: AgentState) -> AgentState:
    """Retrieve matching jobs via RAG agent."""
    from agents.rag_agent import RAGAgent
    from agents.state import QueryContext

    ctx_dict = state.get("context", {})
    context = QueryContext(
        raw_query=ctx_dict.get("raw_query", state["query"]),
        intent=IntentType(state["intent"]),
        keywords=ctx_dict.get("keywords", []),
        skills=ctx_dict.get("skills", []),
        location=ctx_dict.get("location"),
        is_remote=ctx_dict.get("is_remote"),
        seniority=ctx_dict.get("seniority"),
        employment_type=ctx_dict.get("employment_type"),
        company=ctx_dict.get("company"),
        limit=ctx_dict.get("limit", 10),
        offset=ctx_dict.get("offset", 0),
    )
    rag = RAGAgent()
    response = await rag.search_jobs(context)
    return {
        "retrieved_jobs": [
            {
                "job_id": r.job_id,
                "title": r.title,
                "company": r.company,
                "location": r.location,
                "is_remote": r.is_remote,
                "skills": r.skills,
                "score": r.score,
                "match_reason": r.match_reason,
                "source_url": r.source_url,
            }
            for r in response.results
        ],
        "total_retrieved": len(response.results),
        "summary": response.summary,
        "final_response": {
            "success": response.success,
            "intent": response.intent.value,
            "summary": response.summary,
            "error": response.error,
            "metadata": response.metadata,
        },
    }


async def node_live_search(state: AgentState) -> AgentState:
    """Fan out to the live scrapers and persist what comes back.

    Reached only when :func:`route_after_retrieval` decides the index cannot
    answer this query. Runs no LLM: the scrapers return structured rows and
    ``services.job_persistence`` stores them structurally, so a 60-result
    search costs nothing in Groq quota.

    Every failure here is survivable. The user already has their indexed
    results, so a dead scraper, an unreachable cache or a failed write
    degrades this turn to "what the index knew" rather than erroring.
    """
    from agents.sourcing_policy import SOURCED_LOCK_TTL_SECONDS, sourced_lock_key
    from ingestion.engine import RealtimeScraperEngine
    from services.cache_backend import CacheBackend

    ctx = state.get("context", {})
    query = ctx.get("raw_query") or state["query"]
    location = ctx.get("location") or "India"
    is_remote = ctx.get("is_remote")

    try:
        results = await RealtimeScraperEngine.search_all(
            query=query,
            location=location,
            is_remote=is_remote,
            force_refresh=bool(state.get("force_refresh")),
        )
    except Exception as exc:
        logger.warning("Live sourcing failed for %r: %s", query, exc)
        return {
            "live_jobs": [],
            "sources_stats": {},
            "sourcing_decision": {"sourced": False, "reason": f"live search failed: {exc}"},
        }

    live_jobs = results.get("jobs", []) or []

    # Take the lock *after* a successful fan-out, so a failed attempt is
    # retried rather than locked out for eight hours.
    try:
        await CacheBackend.set(
            sourced_lock_key(query, location, is_remote), "1", SOURCED_LOCK_TTL_SECONDS
        )
    except Exception as exc:
        logger.debug("Could not set the sourcing lock: %s", exc)

    # Persist in the background: the user is waiting on this response, and
    # writing ~60 rows plus embeddings must not be on their critical path.
    if live_jobs:
        _schedule_live_persistence(live_jobs)

    return {
        "live_jobs": live_jobs,
        "sources_stats": results.get("sources_stats", {}),
        "sourcing_decision": {
            "sourced": True,
            "reason": state.get("sourcing_decision", {}).get("reason", "index could not answer"),
            "live_count": len(live_jobs),
        },
    }


def _schedule_live_persistence(live_jobs: list[dict[str, Any]]) -> None:
    """Write scraped jobs to Postgres without blocking the response."""
    import asyncio

    from ingestion.engine import job_dicts_to_entities
    from services.job_persistence import persist_live_jobs

    async def _run() -> None:
        try:
            await persist_live_jobs(job_dicts_to_entities(live_jobs))
        except Exception as exc:  # noqa: BLE001 - background work never surfaces
            logger.warning("Background persistence of live jobs failed: %s", exc)

    try:
        task = asyncio.create_task(_run())
        # Without a reference the task can be garbage-collected mid-flight,
        # which loses the write silently.
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    except RuntimeError:
        # No running loop (a synchronous caller or a test). Skipping is
        # correct: the search still returns, the rows are simply not stored.
        logger.debug("No event loop available; skipping background persistence")


#: Strong references to in-flight background tasks. asyncio only holds weak
#: ones, so without this a persistence task can vanish before it commits.
_BACKGROUND_TASKS: set[Any] = set()


async def node_merge_rank(state: AgentState) -> AgentState:
    """Merge indexed and live results into one ranked list.

    Deliberately thin: the ranking itself lives in :mod:`agents.merge_rank`
    as a pure function, so it can be tested without a database or a network.
    """
    from agents.merge_rank import merge_and_rank, to_retrieval_dict

    ctx = state.get("context", {})
    query = ctx.get("raw_query") or state["query"]
    limit = int(ctx.get("limit", 10) or 10)

    indexed = state.get("retrieved_jobs", []) or []
    live = state.get("live_jobs", []) or []

    if not live:
        # Nothing to merge. Returning early keeps the indexed ordering
        # (relevance from the vector search) rather than re-ranking it on
        # weaker signals.
        return {}

    # Normalised onto the RetrievalResult shape before leaving this node: the
    # orchestrator indexes r["job_id"] directly, and live rows carry "id".
    merged = [to_retrieval_dict(j) for j in merge_and_rank(indexed, live, query=query, limit=limit)]

    existing = state.get("final_response", {}) or {}
    summary = (
        f"Found {len(merged)} matching roles "
        f"({len(indexed)} from the index, {len(merged) - len(indexed)} newly sourced)."
        if len(merged) > len(indexed)
        else f"Found {len(merged)} matching roles."
    )

    return {
        "retrieved_jobs": merged,
        "total_retrieved": len(merged),
        "summary": summary,
        "final_response": {
            **existing,
            "success": True,
            "summary": summary,
            "metadata": {
                **(existing.get("metadata") or {}),
                "sources_stats": state.get("sources_stats", {}),
                "sourcing_decision": state.get("sourcing_decision", {}),
                "indexed_count": len(indexed),
                "live_count": len(live),
            },
        },
    }


async def route_after_retrieval(state: AgentState) -> Literal["node_live_search", "node_merge_rank"]:
    """Decide whether the index answered well enough, or we go to the internet.

    Async because the 8-hour sourcing lock lives in the cache and has to be
    read before deciding. The policy itself is in
    :mod:`agents.sourcing_policy` - deterministic, no LLM call, and pure, so
    every branch is testable; this function only gathers its inputs.
    """
    from agents.sourcing_policy import decide_sourcing, sourced_lock_key

    ctx = state.get("context", {})
    query = ctx.get("raw_query") or state.get("query", "")
    location = ctx.get("location") or "India"
    is_remote = ctx.get("is_remote")
    indexed = state.get("retrieved_jobs", []) or []

    newest: datetime | None = None
    for job in indexed:
        raw = job.get("posted_at") or job.get("created_at")
        if isinstance(raw, str) and raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if newest is None or parsed > newest:
                newest = parsed

    # Read here rather than inside the policy, so the policy stays pure. A
    # cache failure reads as "not locked", which errs towards fresher results
    # rather than staler ones.
    already_sourced = False
    try:
        from services.cache_backend import CacheBackend

        already_sourced = await CacheBackend.exists(
            sourced_lock_key(query, location, is_remote)
        )
    except Exception as exc:
        logger.debug("Could not read the sourcing lock: %s", exc)

    decision = decide_sourcing(
        query=query,
        indexed_count=len(indexed),
        newest_indexed_at=newest,
        already_sourced=already_sourced,
        force_refresh=bool(state.get("force_refresh")),
    )

    state["sourcing_decision"] = decision.as_dict()
    if decision.should_source:
        logger.info("Sourcing live for %r: %s", query, decision.reason)
        return "node_live_search"

    logger.debug("Not sourcing for %r: %s", query, decision.reason)
    return "node_merge_rank"


async def node_studio_agent(state: AgentState) -> AgentState:
    """
    Route COMPANY_INFO / CAREER_COACH / APPLICATION_TRACKER /
    PERSONAL_AGENT / RESUME_STUDIO intents to the matching thin agent.
    """
    from agents.studio_agents import (
        ApplicationAgent,
        CareerCoachAgent,
        CompanyAgent,
        PersonalAgent,
    )

    intent = state.get("intent", IntentType.GENERAL.value)
    user_id = state.get("user_id")
    result: dict[str, Any] = {"success": False, "error": "Unhandled intent", "data": None}

    if intent == IntentType.COMPANY_INFO.value:
        result = await CompanyAgent().profile(name=state.get("context", {}).get("company"))
    elif intent == IntentType.CAREER_COACH.value:
        if user_id:
            result = await CareerCoachAgent().recommend(user_id=user_id, persist=False)
        else:
            result = {"success": False, "error": "user_id required for career coach", "data": None}
    elif intent == IntentType.APPLICATION_TRACKER.value:
        if user_id:
            result = await ApplicationAgent().funnel(user_id=user_id)
        else:
            result = {"success": False, "error": "user_id required for application tracker", "data": None}
    elif intent == IntentType.PERSONAL_AGENT.value:
        if user_id:
            result = await PersonalAgent().next_action(user_id=user_id)
        else:
            result = {"success": False, "error": "user_id required for personal agent", "data": None}
    elif intent == IntentType.RESUME_STUDIO.value:
        result = {
            "success": True,
            "data": {"message": "Resume Studio requires the file-upload API."},
            "error": None,
        }

    data = result.get("data")
    # The recommendation, when a studio agent produced one, is the sentence the
    # copilot reads out. It used to be computed here and then dropped on the
    # floor because final_response hard-coded summary=None, so company_info and
    # application_tracker answers came back with nothing to say.
    summary = data.get("recommendation") if isinstance(data, dict) else None

    return {
        "retrieved_jobs": [],
        "total_retrieved": 0,
        "summary": summary,
        "final_response": {
            "success": result.get("success", False),
            "intent": intent,
            "summary": summary,
            "error": result.get("error"),
            "metadata": {"data": data},
        },
    }


async def node_general(state: AgentState) -> AgentState:
    """Terminal node for conversational and interview-prep turns.

    These intents have no retrieval or tool step of their own, but they are
    perfectly ordinary questions - "what should I do today?", "how do I prep
    for Thursday's round?". They used to fall through ``route_by_intent``
    into ``node_error``, so the copilot answered a greeting with "An
    unexpected error occurred." and ``POST /api/v1/query`` returned an error
    payload for anything it could not classify as a search.

    The node returns a *successful* empty result. ``services.copilot.chat``
    recognises that shape and grounds a reply in the user's own briefing;
    ``/query`` reports the intent honestly instead of inventing a failure.
    """
    intent = state.get("intent", IntentType.GENERAL.value)
    return {
        "retrieved_jobs": [],
        "total_retrieved": 0,
        "summary": None,
        "final_response": {
            "success": True,
            "intent": intent,
            "summary": None,
            "error": None,
            "metadata": {"handled_by": "general"},
        },
    }


async def node_error(state: AgentState) -> AgentState:
    """Terminal error node — formats an error response."""
    intent = state.get("intent", IntentType.GENERAL.value)
    error_msg = state.get("error", "An unexpected error occurred.")
    logger.error("Agent graph error node reached: intent=%s, error=%s", intent, error_msg)
    return {
        "final_response": {
            "success": False,
            "intent": intent,
            "summary": None,
            "error": error_msg,
            "metadata": {},
        }
    }


# ---------------------------------------------------------------------------
# Routing logic (conditional edge after classify)
# ---------------------------------------------------------------------------

#: Intents answered by retrieving job rows.
_RETRIEVAL_INTENTS = frozenset({
    IntentType.SEARCH_JOBS.value,
    IntentType.FIND_CANDIDATES.value,
})

#: Intents answered by a thin, deterministic service agent.
_STUDIO_INTENTS = frozenset({
    IntentType.COMPANY_INFO.value,
    IntentType.CAREER_COACH.value,
    IntentType.APPLICATION_TRACKER.value,
    IntentType.PERSONAL_AGENT.value,
    IntentType.RESUME_STUDIO.value,
})

#: Intents that are simply conversation. INTERVIEW_PREP lives here rather
#: than in _STUDIO_INTENTS because the mock-interview flow is its own
#: sub-graph (agents/interview/) reached from /api/v1/interview - asking
#: about it in chat is a question to answer, not a session to start.
_GENERAL_INTENTS = frozenset({
    IntentType.GENERAL.value,
    IntentType.INTERVIEW_PREP.value,
})


def route_by_intent(
    state: AgentState,
) -> Literal["node_rag_retrieve", "node_studio_agent", "node_general", "node_error"]:
    """Determine which node answers this turn, based on the classified intent.

    Every member of :class:`IntentType` must land in exactly one of the three
    sets above - ``tests/test_agents.py`` asserts that. An intent matching
    none of them is a bug in this module (someone added an enum member
    without wiring it), so it goes to ``node_error`` and is logged loudly.
    """
    intent = state.get("intent", IntentType.GENERAL.value)
    if intent in _RETRIEVAL_INTENTS:
        return "node_rag_retrieve"
    if intent in _STUDIO_INTENTS:
        return "node_studio_agent"
    if intent in _GENERAL_INTENTS:
        return "node_general"
    logger.error(
        "Intent %r has no route - add it to an intent set in agents/graph.py",
        intent,
    )
    return "node_error"


# ---------------------------------------------------------------------------
# Build and compile the graph
# ---------------------------------------------------------------------------

def build_agent_graph() -> Any:
    """Construct and compile the TalentRadar agent StateGraph."""
    builder: StateGraph = StateGraph(AgentState)

    # Register nodes
    builder.add_node("node_classify", node_classify)
    builder.add_node("node_rag_retrieve", node_rag_retrieve)
    builder.add_node("node_live_search", node_live_search)
    builder.add_node("node_merge_rank", node_merge_rank)
    builder.add_node("node_studio_agent", node_studio_agent)
    builder.add_node("node_general", node_general)
    builder.add_node("node_error", node_error)

    # Entry point
    builder.set_entry_point("node_classify")

    # Conditional routing after classification
    builder.add_conditional_edges(
        "node_classify",
        route_by_intent,
        {
            "node_rag_retrieve": "node_rag_retrieve",
            "node_studio_agent": "node_studio_agent",
            "node_general": "node_general",
            "node_error": "node_error",
        },
    )

    # After retrieval, decide whether the index answered or we go live. The
    # router is deterministic (agents/sourcing_policy.py) — no second LLM call
    # on the search path.
    builder.add_conditional_edges(
        "node_rag_retrieve",
        route_after_retrieval,
        {
            "node_live_search": "node_live_search",
            "node_merge_rank": "node_merge_rank",
        },
    )
    # Live results always flow through the merge, so ranking happens in
    # exactly one place whether or not sourcing ran.
    builder.add_edge("node_live_search", "node_merge_rank")

    # Terminal edges — all paths go to END
    builder.add_edge("node_merge_rank", END)
    builder.add_edge("node_studio_agent", END)
    builder.add_edge("node_general", END)
    builder.add_edge("node_error", END)

    return builder.compile()


# Singleton compiled graph — imported by the API layer
agent_graph = build_agent_graph()
