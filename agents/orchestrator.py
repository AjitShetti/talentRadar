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

from agents.ml_scorer import MLScorer
from agents.prompts.intent_prompt import INTENT_EXTRACTION_PROMPT
from agents.rag_agent import RAGAgent
from agents.state import AgentResponse, CandidateProfile, IntentType, QueryContext
from agents.trend_agent import TrendAgent
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
        self._trend_agent = TrendAgent()
        self._ml_scorer = MLScorer()

    async def process_query(self, query: str, **kwargs: Any) -> AgentResponse:
        """
        Process a user query end-to-end.

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
        # Step 1: Classify intent and extract context
        context = await self._classify_intent(query)

        # Override with any explicit kwargs
        if "limit" in kwargs:
            context.limit = int(kwargs["limit"])
        if "offset" in kwargs:
            context.offset = int(kwargs["offset"])

        logger.info(
            "Processing query: intent=%s, keywords=%s, limit=%d",
            context.intent, context.keywords, context.limit,
        )

        # Step 2: Route to appropriate agent
        if context.intent == IntentType.SEARCH_JOBS:
            return await self._rag_agent.search_jobs(context)

        elif context.intent == IntentType.MARKET_TRENDS:
            return await self._trend_agent.get_market_trends(query)

        elif context.intent == IntentType.FIND_CANDIDATES:
            return await self._handle_candidate_matching(context)

        elif context.intent == IntentType.COMPANY_INFO:
            return await self._handle_company_info(context)

        else:
            return AgentResponse(
                success=False,
                intent=IntentType.GENERAL,
                summary="I can help you search for jobs, analyze market trends, or find candidates. Try asking something like 'Find remote Python engineer jobs' or 'What skills are in demand?'",
            )

    async def match_candidate_to_jobs(
        self, candidate: CandidateProfile, limit: int = 10
    ) -> AgentResponse:
        """
        Match a candidate profile to available jobs.

        Parameters
        ----------
        candidate : CandidateProfile
            The candidate's profile with skills, experience, etc.
        limit : int
            Maximum number of job matches to return.

        Returns
        -------
        AgentResponse
            Jobs ranked by match score.
        """
        try:
            # Search for jobs matching candidate's profile text
            if candidate.resume_text:
                search_context = QueryContext(
                    raw_query=candidate.resume_text[:1000],
                    intent=IntentType.FIND_CANDIDATES,
                    skills=candidate.skills,
                    desired_title=candidate.desired_title,
                    is_remote=candidate.is_remote,
                    limit=limit * 2,  # Over-fetch for scoring
                )
                search_results = await self._rag_agent.search_jobs(search_context)

                if not search_results.success:
                    return search_results

                # Score each job
                candidate_embedding = None  # Would generate from resume_text
                scored = self._ml_scorer.score_batch(
                    candidate, search_results.results, candidate_embedding
                )

                # Attach scores to results
                result_map = {r.job_id: r for r in search_results.results}
                for score in scored[:limit]:
                    if score.job_id in result_map:
                        result_map[score.job_id].score = score.overall_score
                        result_map[score.job_id].match_reason = score.reasoning

                return AgentResponse(
                    success=True,
                    intent=IntentType.FIND_CANDIDATES,
                    results=search_results.results[:limit],
                    summary=f"Found {len(scored)} job matches. Top match score: {scored[0].overall_score:.0%}" if scored else "No matches found.",
                    metadata={"match_scores": [s.overall_score for s in scored[:limit]]},
                )

            return AgentResponse(
                success=False,
                intent=IntentType.FIND_CANDIDATES,
                error="Candidate profile must include resume_text for matching.",
            )

        except Exception as exc:
            logger.error("Candidate matching failed: %s", exc, exc_info=True)
            return AgentResponse(
                success=False,
                intent=IntentType.FIND_CANDIDATES,
                error=str(exc),
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

        # Market trends
        if any(kw in query_lower for kw in ["trend", "market", "salary", "demand", "statistics", "analytics"]):
            return QueryContext(
                raw_query=query,
                intent=IntentType.MARKET_TRENDS,
                keywords=query_lower.split(),
            )

        # Company info
        if any(kw in query_lower for kw in ["about", "company", "organization", "employer"]) and any(
            kw in query_lower for kw in ["tell me", "info", "information", "what is"]
        ):
            return QueryContext(
                raw_query=query,
                intent=IntentType.COMPANY_INFO,
                keywords=query_lower.split(),
            )

        return None

    async def _llm_classify_intent(self, query: str) -> QueryContext | None:
        """Use Groq LLM to classify intent."""
        prompt = INTENT_EXTRACTION_PROMPT.format(query=query)

        response = await self._groq.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=200,
        )

        content = response.choices[0].message.content
        if not content:
            return None

        # Extract JSON from response
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            return None

        try:
            data = json.loads(json_match.group())
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

    @staticmethod
    async def _handle_candidate_matching(context: QueryContext) -> AgentResponse:
        """Handle candidate search/matching intent."""
        return AgentResponse(
            success=False,
            intent=IntentType.FIND_CANDIDATES,
            summary="Candidate matching requires a candidate profile. Please provide candidate details or use job search instead.",
        )

    @staticmethod
    async def _handle_company_info(context: QueryContext) -> AgentResponse:
        """Handle company information requests."""
        return AgentResponse(
            success=False,
            intent=IntentType.COMPANY_INFO,
            summary="Company information feature is coming soon. Try searching for jobs at a specific company instead.",
        )
