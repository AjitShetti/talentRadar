"""
tests/test_agents.py
~~~~~~~~~~~~~~~~~~~~
Tests for the AI agent layer.

Covers:
- State objects
- LangGraph routing: every IntentType member must reach a terminal node
- Graceful degradation when Groq / ChromaDB are unreachable
"""

from unittest.mock import AsyncMock, patch

import pytest

from agents.state import QueryContext, RetrievalResult





class TestQueryContext:
    """Test QueryContext state object."""

    def test_default_values(self):
        """QueryContext should have sensible defaults."""
        ctx = QueryContext(raw_query="test")
        assert ctx.intent.value == "general"
        assert ctx.keywords == []
        assert ctx.skills == []
        assert ctx.limit == 10
        assert ctx.offset == 0

    def test_with_filters(self):
        """QueryContext should accept filters."""
        ctx = QueryContext(
            raw_query="remote python jobs",
            skills=["Python"],
            is_remote=True,
            seniority="senior",
            limit=20,
        )
        assert ctx.skills == ["Python"]
        assert ctx.is_remote is True
        assert ctx.seniority == "senior"
        assert ctx.limit == 20


class TestRetrievalResult:
    """Test RetrievalResult state object."""

    def test_basic_result(self):
        """RetrievalResult should store job info."""
        result = RetrievalResult(
            job_id="job1",
            title="Engineer",
            company="TechCorp",
            skills=["Python", "SQL"],
            score=0.85,
        )
        assert result.job_id == "job1"
        assert result.title == "Engineer"
        assert result.company == "TechCorp"
        assert result.score == 0.85
        assert len(result.skills) == 2


class TestAgentGraphRouting:
    """Every intent must have a home.

    ``route_by_intent`` used to send anything that was not a search or a
    studio intent to ``node_error``, which answers "An unexpected error
    occurred." Two of the nine IntentType members landed there:

    * ``INTERVIEW_PREP`` — produced by the rule-based classifier for any
      message containing the word "interview", which for a career copilot is
      one of the most likely questions a user can ask.
    * ``GENERAL`` — the classifier's own documented fallback, so a greeting
      or an unclassifiable question got an error too.

    These tests fail if a new IntentType member is added without wiring it.
    """

    def test_every_intent_has_a_route(self):
        from agents.graph import route_by_intent
        from agents.state import IntentType

        unrouted = [i.value for i in IntentType if route_by_intent({"intent": i.value}) == "node_error"]
        assert unrouted == [], f"IntentType members with no route: {unrouted}"

    @pytest.mark.parametrize(
        ("intent", "expected"),
        [
            ("search_jobs", "node_rag_retrieve"),
            ("find_candidates", "node_rag_retrieve"),
            ("company_info", "node_studio_agent"),
            ("career_coach", "node_studio_agent"),
            ("application_tracker", "node_studio_agent"),
            ("personal_agent", "node_studio_agent"),
            ("resume_studio", "node_studio_agent"),
            ("interview_prep", "node_general"),
            ("general", "node_general"),
        ],
    )
    def test_routes_to_expected_node(self, intent, expected):
        from agents.graph import route_by_intent

        assert route_by_intent({"intent": intent}) == expected

    def test_unknown_intent_falls_back_to_error_node(self):
        """A value outside the enum is a wiring bug and must be loud."""
        from agents.graph import route_by_intent

        assert route_by_intent({"intent": "not_a_real_intent"}) == "node_error"

    @pytest.mark.asyncio
    async def test_general_node_reports_success_not_failure(self):
        """Conversation is not an error condition.

        ``services.copilot.chat`` distinguishes "the agent had nothing
        structured to say" from "the agent broke" by this flag, and answers
        the former from the user's own briefing.
        """
        from agents.graph import node_general

        state = await node_general({"intent": "interview_prep"})
        response = state["final_response"]
        assert response["success"] is True
        assert response["error"] is None
        assert response["intent"] == "interview_prep"


class TestAgentGraphDegradation:
    """The graph must answer even when its dependencies are down."""

    @pytest.mark.asyncio
    async def test_classification_failure_does_not_raise(self):
        """A Groq outage used to raise straight out of ainvoke, so every
        caller of /query and /agent/chat got a 500 instead of a reply."""
        from agents.graph import node_classify

        with patch(
            "agents.orchestrator.Orchestrator._classify_intent",
            new_callable=AsyncMock,
            side_effect=RuntimeError("groq is down"),
        ):
            state = await node_classify({"query": "hello"})

        assert state["intent"] == "general"
        assert state["context"]["raw_query"] == "hello"

    @pytest.mark.asyncio
    async def test_studio_summary_survives_to_the_response(self):
        """node_studio_agent computed a summary and then discarded it,
        hard-coding final_response["summary"] to None."""
        from agents.graph import node_studio_agent

        with patch("agents.studio_agents.PersonalAgent.next_action", new_callable=AsyncMock) as mock_action:
            mock_action.return_value = {
                "success": True,
                "data": {"recommendation": "Follow up on the Swiggy application."},
                "error": None,
            }
            state = await node_studio_agent({"intent": "personal_agent", "user_id": "u1"})

        assert state["summary"] == "Follow up on the Swiggy application."
        assert state["final_response"]["summary"] == "Follow up on the Swiggy application."
