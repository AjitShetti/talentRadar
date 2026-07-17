"""
agents/interview/__init__.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Mock interview agent package.

Public surface:
    build_interview_graph  — factory that returns a compiled LangGraph
    InterviewAgentState    — TypedDict state flowing through the graph
"""

from agents.interview.graph import build_interview_graph
from agents.interview.state import InterviewAgentState

__all__ = ["build_interview_graph", "InterviewAgentState"]
