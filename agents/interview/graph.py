"""
agents/interview/graph.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Builds and compiles the LangGraph StateGraph for the interview agent.

Graph topology
--------------

    START
      │
      ▼
    node_generate_question ──────────────────────────────────┐
      │                                                       │
      │  (API returns answer to next request)                 │
      ▼                                                       │
    node_evaluate_answer                                      │
      │                                                       │
      ▼  router_after_eval                                    │
      ├── "followup"      → node_generate_followup ──────────┤  (loop)
      │                         (answer on next request)     │
      │                                                       │
      ├── "next_question" ────────────────────────────────────┘
      │
      └── "end"           → node_end_session → END

Design decisions
----------------
* The graph is STATELESS between API calls.  The frontend holds the
  full ``InterviewAgentState`` and submits it with each request.
  This avoids any server-side session storage for conversation context.
* Each "turn" is a single graph invocation starting from the appropriate
  entry node:
    - Session start   → invoke from "node_generate_question"
    - Answer submit   → invoke from "node_evaluate_answer"
  The API layer chooses the entry point via ``graph.ainvoke(state, config)``.
* The compiled graph is exposed as ``interview_graph`` for use in the
  API router (api/routers/interview.py).
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from agents.interview.nodes import (
    node_end_session,
    node_evaluate_answer,
    node_generate_followup,
    node_generate_question,
    router_after_eval,
)
from agents.interview.state import InterviewAgentState


def build_interview_graph() -> Any:
    """
    Construct and compile the interview agent StateGraph.

    Returns a compiled LangGraph that can be invoked with:

        result = await interview_graph.ainvoke(state)

    or with a specific starting node via the ``config`` parameter when
    using per-node invocation in the API layer.
    """
    builder: StateGraph = StateGraph(InterviewAgentState)

    # ------------------------------------------------------------------
    # Register nodes
    # ------------------------------------------------------------------
    builder.add_node("node_generate_question",  node_generate_question)
    builder.add_node("node_evaluate_answer",     node_evaluate_answer)
    builder.add_node("node_generate_followup",   node_generate_followup)
    builder.add_node("node_end_session",         node_end_session)

    # ------------------------------------------------------------------
    # Entry point — the graph starts by generating the first question
    # ------------------------------------------------------------------
    builder.set_entry_point("node_generate_question")

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------

    # After a question is generated the graph halts and waits for the
    # API layer to submit the answer.  The API then re-invokes starting
    # from node_evaluate_answer — so the edge below is only reached when
    # the graph is run end-to-end (e.g. in tests).
    builder.add_edge("node_generate_question", "node_evaluate_answer")

    # Conditional routing after evaluation
    builder.add_conditional_edges(
        "node_evaluate_answer",
        router_after_eval,
        {
            "node_generate_followup": "node_generate_followup",
            "node_generate_question": "node_generate_question",
            "node_end_session":       "node_end_session",
        },
    )

    # Follow-up loops back into evaluation after the answer is received
    builder.add_edge("node_generate_followup", "node_evaluate_answer")

    # End session terminates the graph
    builder.add_edge("node_end_session", END)

    return builder.compile()


# ---------------------------------------------------------------------------
# Module-level singleton — imported by the API router
# ---------------------------------------------------------------------------
interview_graph = build_interview_graph()
