"""
agents/orchestrator.py
~~~~~~~~~~~~~~~~~~~~~~
Intent classification and routing orchestrator.

Routes user queries to the appropriate agent:
  - RAGAgent for job search and candidate matching
  - TrendAgent for market trends and insights

Uses Groq LLM for intent classification and can also do
rule-based classification for simple queries.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from groq import AsyncGroq

from agents.prompts.intent_prompt import INTENT_EXTRACTION_PROMPT
from agents.rag_agent import RAGAgent
from agents.state import AgentResponse, CandidateProfile, IntentType, QueryContext
from config.settings import get_settings

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Main orchestrator for the TalentRadar agent system.

    Flow:
    1. Classify user intent (LLM or rule-based)
    2. Extract query context (filters, keywords)
    3. Route to appropriate agent
    4. Return unified response
    """

    def __init__(self):
        settings = get_settings()
        self._groq = AsyncGroq(api_key=settings.groq_api_key)
        self._rag_agent = RAGAgent()

    async def process_query(self, query: str, **kwargs: Any) -> AgentResponse:
        """
        Process a user query end-to-end via the compiled LangGraph agent graph.

        Parameters
        ----------
        query : str
            User's natural language query.
        **kwargs
            Additional filters (limit, offset, etc.).

        Returns
        -------
        AgentResponse
            Unified response from the appropriate agent.
        """
        from agents.graph import agent_graph  # avoid circular import at module level

        initial_state = {
            "query": query,
            "user_id": kwargs.get("user_id"),
        }

        try:
            final_state = await agent_graph.ainvoke(initial_state)
        except Exception as exc:
            logger.error("Agent graph invocation failed: %s", exc, exc_info=True)
            return AgentResponse(
                success=False,
                intent=IntentType.GENERAL,
                error=str(exc),
            )

        response_dict = final_state.get("final_response", {})
        results_raw = final_state.get("retrieved_jobs", [])

        # Re-hydrate RetrievalResult objects from the serialised dicts
        from agents.state import RetrievalResult
        results = [
            RetrievalResult(
                job_id=r["job_id"],
                title=r["title"],
                company=r["company"],
                location=r.get("location"),
                is_remote=r.get("is_remote", False),
                skills=r.get("skills", []),
                score=r.get("score", 0.0),
                match_reason=r.get("match_reason"),
                source_url=r.get("source_url"),
            )
            for r in results_raw
        ]

        return AgentResponse(
            success=response_dict.get("success", False),
            intent=IntentType(response_dict.get("intent", IntentType.GENERAL.value)),
            results=results,
            summary=response_dict.get("summary"),
            error=response_dict.get("error"),
            metadata=response_dict.get("metadata", {}),
        )

    async def match_candidate_to_jobs(
        self, candidate: CandidateProfile, limit: int = 10
    ) -> AgentResponse:
        """
        Phase 3: LLM-as-a-judge candidate matching.
        """
        return AgentResponse(
            success=False,
            intent=IntentType.FIND_CANDIDATES,
            summary="LLM-as-a-judge candidate matching will be implemented in Phase 3.",
            error="Not Implemented",
        )

    async def _classify_intent(self, query: str) -> QueryContext:
        """
        Classify user query intent using LLM with rule-based fallback.
        """
        # Try rule-based classification first for simple patterns
        rule_based = self._rule_based_classification(query)
        if rule_based:
            return rule_based

        # Fall back to LLM classification
        try:
            context = await self._llm_classify_intent(query)
            if context:
                return context
        except Exception as exc:
            logger.warning("LLM intent classification failed, using fallback: %s", exc)

        # Final fallback: default to job search
        return QueryContext(
            raw_query=query,
            intent=IntentType.SEARCH_JOBS,
            keywords=query.split(),
        )

    @staticmethod
    def _rule_based_classification(query: str) -> QueryContext | None:
        """Quick rule-based intent detection."""
        query_lower = query.lower().strip()

        # Company info
        if any(kw in query_lower for kw in ["about", "company", "organization", "employer"]) and any(
            kw in query_lower for kw in ["tell me", "info", "information", "what is"]
        ):
            return QueryContext(
                raw_query=query,
                intent=IntentType.COMPANY_INFO,
                keywords=query_lower.split(),
            )

        # Career coach
        if any(kw in query_lower for kw in ["career", "skill gap", "weakness", "learn", "learning", "upskill", "grow"]):
            return QueryContext(
                raw_query=query,
                intent=IntentType.CAREER_COACH,
                keywords=query_lower.split(),
            )

        # Application tracker
        if any(kw in query_lower for kw in ["application", "applied", "tracker", "status", "funnel", "where do i stand"]):
            return QueryContext(
                raw_query=query,
                intent=IntentType.APPLICATION_TRACKER,
                keywords=query_lower.split(),
            )

        # Interview prep
        if any(kw in query_lower for kw in ["interview", "mock", "prepare for interview", "practice question"]):
            return QueryContext(
                raw_query=query,
                intent=IntentType.INTERVIEW_PREP,
                keywords=query_lower.split(),
            )

        # Personal agent
        if any(kw in query_lower for kw in ["next step", "what should i do", "next action", "recommend me", "remind", "agent"]):
            return QueryContext(
                raw_query=query,
                intent=IntentType.PERSONAL_AGENT,
                keywords=query_lower.split(),
            )

        return None

    async def _llm_classify_intent(self, query: str) -> QueryContext | None:
        """Use Groq LLM to classify intent."""
        prompt = INTENT_EXTRACTION_PROMPT.format(query=query)

        response = await self._groq.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You must output a valid JSON object. No markdown formatting or extra text."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=200,
        )

        content = response.choices[0].message.content
        if not content:
            return None

        try:
            data = json.loads(content)
            return QueryContext(
                raw_query=query,
                intent=IntentType(data.get("intent", "general")),
                keywords=data.get("keywords", []),
                skills=data.get("skills", []),
                location=data.get("location"),
                is_remote=data.get("is_remote"),
                seniority=data.get("seniority"),
                employment_type=data.get("employment_type"),
                company=data.get("company"),
            )
        except (json.JSONDecodeError, ValueError):
            return None


