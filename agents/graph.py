"""
agents/graph.py
~~~~~~~~~~~~~~~
LangGraph state machine for the TalentRadar agent pipeline.

Graph topology:
    classify → route → retrieve → generate → END
                   ↘ trend_retrieve → END
                   ↘ error → END

Each node receives and returns the full AgentState dict.
The compiled graph is exposed as ``agent_graph`` for use in the API layer.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from agents.state import AgentResponse, IntentType, QueryContext

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
    """Classify user intent and extract query context via Groq LLM."""
    from agents.orchestrator import Orchestrator  # local import avoids cycles
    orchestrator = Orchestrator()
    context: QueryContext = await orchestrator._classify_intent(state["query"])
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


async def node_trend_retrieve(state: AgentState) -> AgentState:
    """Retrieve market trend data via TrendAgent."""
    from agents.trend_agent import TrendAgent

    trend = TrendAgent()
    response = await trend.get_market_trends(state["query"])
    return {
        "retrieved_jobs": [],
        "total_retrieved": 0,
        "summary": response.summary,
        "final_response": {
            "success": response.success,
            "intent": response.intent.value,
            "summary": response.summary,
            "error": response.error,
            "metadata": response.metadata,
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

def route_by_intent(
    state: AgentState,
) -> Literal["node_rag_retrieve", "node_trend_retrieve", "node_error"]:
    """Determine which retrieval node to call based on classified intent."""
    intent = state.get("intent", IntentType.GENERAL.value)
    if intent in (IntentType.SEARCH_JOBS.value, IntentType.FIND_CANDIDATES.value):
        return "node_rag_retrieve"
    elif intent == IntentType.MARKET_TRENDS.value:
        return "node_trend_retrieve"
    else:
        # COMPANY_INFO / GENERAL / unknown — fall through to error node
        # which returns a graceful "not yet supported" response
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
    builder.add_node("node_trend_retrieve", node_trend_retrieve)
    builder.add_node("node_error", node_error)

    # Entry point
    builder.set_entry_point("node_classify")

    # Conditional routing after classification
    builder.add_conditional_edges(
        "node_classify",
        route_by_intent,
        {
            "node_rag_retrieve": "node_rag_retrieve",
            "node_trend_retrieve": "node_trend_retrieve",
            "node_error": "node_error",
        },
    )

    # Terminal edges — both retrieval paths and error all go to END
    builder.add_edge("node_rag_retrieve", END)
    builder.add_edge("node_trend_retrieve", END)
    builder.add_edge("node_error", END)

    return builder.compile()


# Singleton compiled graph — imported by the API layer
agent_graph = build_agent_graph()
