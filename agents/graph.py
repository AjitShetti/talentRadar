"""
agents/graph.py
~~~~~~~~~~~~~~~
LangGraph state machine for the TalentRadar agent pipeline.

Graph topology:
    classify → route_by_intent → node_rag_retrieve  → END   (search_jobs, find_candidates)
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

    # Terminal edges — all paths go to END
    builder.add_edge("node_rag_retrieve", END)
    builder.add_edge("node_studio_agent", END)
    builder.add_edge("node_general", END)
    builder.add_edge("node_error", END)

    return builder.compile()


# Singleton compiled graph — imported by the API layer
agent_graph = build_agent_graph()
