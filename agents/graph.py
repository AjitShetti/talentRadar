"""
agents/graph.py
~~~~~~~~~~~~~~~
LangGraph-style state machine for the agent orchestrator.

Defines the graph of nodes (classify -> retrieve -> rerank -> generate)
with TypedDict state flowing through the pipeline.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from agents.state import AgentResponse, IntentType


class AgentState(TypedDict, total=False):
    """State object that flows through the agent graph."""
    # Input
    query: str
    user_id: str | None

    # Intent classification
    intent: IntentType
    keywords: list[str]
    filters: dict[str, Any]

    # Retrieval
    retrieved_jobs: list[dict[str, Any]]
    total_retrieved: int

    # Reranking/scoring
    scored_jobs: list[dict[str, Any]]
    match_scores: list[dict[str, Any]]

    # Generation
    summary: str | None
    final_response: AgentResponse

    # Error handling
    error: str | None


# Graph node type definitions
NodeResult = dict[str, Any]
EdgeCondition = Literal[
    "to_retrieval",
    "to_reranking",
    "to_generation",
    "to_error",
    "to_end",
]
