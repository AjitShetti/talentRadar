"""
agents/rag_agent.py
~~~~~~~~~~~~~~~~~~~
RAG (Retrieve-And-Generate) agent for intelligent job search.

Retrieves relevant jobs from ChromaDB + PostgreSQL, reranks by
relevance, and generates natural-language summaries using Groq LLM.
"""

from __future__ import annotations

import logging
from typing import Any

from groq import AsyncGroq

from agents.state import (
    AgentResponse,
    IntentType,
    QueryContext,
    RetrievalResult,
)
from agents.prompts.rag_prompt import (
    SYSTEM_JOB_SEARCH,
    SYSTEM_RESULT_SUMMARY,
)
from config.settings import get_settings
from ingestion.embeddings.chroma_store import ChromaJobStore
from ingestion.embeddings.embedder import embed_texts
from storage.repository import UnitOfWork
from storage.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


class RAGAgent:
    """
    Retrieve-And-Generate agent for job search.

    Pipeline:
    1. Embed the user query
    2. Search ChromaDB for similar job descriptions
    3. Fetch full job records from PostgreSQL
    4. Rerank by relevance
    5. Generate summary via Groq LLM
    """

    def __init__(self):
        settings = get_settings()
        self._groq = AsyncGroq(api_key=settings.groq_api_key)
        self._chroma = ChromaJobStore()

    async def search_jobs(
        self, context: QueryContext
    ) -> AgentResponse:
        """
        Execute the RAG pipeline for job search.

        Parameters
        ----------
        context : QueryContext
            Parsed user query with intent and filters.

        Returns
        -------
        AgentResponse
            Retrieved jobs with optional LLM summary.
        """
        try:
            # Step 1: Embed query
            query_embedding = embed_texts([context.raw_query])[0]

            # Step 2: Search ChromaDB
            chroma_results = self._chroma.search(
                query_embedding=query_embedding,
                n_results=min(context.limit * 3, 50),  # Over-fetch for filtering
            )

            # Step 3: Build RetrievalResult list
            results = await self._build_results(chroma_results, context)

            # Step 4: Apply filters
            results = self._apply_filters(results, context)

            # Step 5: Truncate to limit
            results = results[context.offset : context.offset + context.limit]

            # Step 6: Generate summary (optional)
            summary = None
            if results:
                summary = await self._generate_summary(results, context)

            return AgentResponse(
                success=True,
                intent=IntentType.SEARCH_JOBS,
                results=results,
                summary=summary,
                metadata={"total_found": len(results)},
            )

        except Exception as exc:
            logger.error("RAG search failed: %s", exc, exc_info=True)
            return AgentResponse(
                success=False,
                intent=IntentType.SEARCH_JOBS,
                error=str(exc),
            )

    async def _build_results(
        self, chroma_results: dict[str, Any], context: QueryContext
    ) -> list[RetrievalResult]:
        """Convert ChromaDB results to RetrievalResult objects."""
        results = []

        if not chroma_results or "ids" not in chroma_results:
            return results

        ids = chroma_results.get("ids", [[]])[0]
        documents = chroma_results.get("documents", [[]])[0]
        metadatas = chroma_results.get("metadatas", [[]])[0]
        distances = chroma_results.get("distances", [[]])[0]

        async with AsyncSessionLocal() as session:
            uow = UnitOfWork(session)

            for idx, job_id in enumerate(ids):
                metadata = metadatas[idx] if idx < len(metadatas) else {}
                distance = distances[idx] if idx < len(distances) else 0.0
                score = 1.0 - distance  # Convert distance to score

                # Fetch full job record from DB
                job = await uow.jobs.get_by_external_id(job_id, "tavily")
                if not job:
                    continue

                result = RetrievalResult(
                    job_id=job_id,
                    title=job.title,
                    company=metadata.get("company", ""),
                    location=metadata.get("location"),
                    is_remote=metadata.get("is_remote", False),
                    seniority=metadata.get("seniority"),
                    skills=metadata.get("skills_str", "").split(", ") if metadata.get("skills_str") else [],
                    source_url=metadata.get("source_url"),
                    score=round(score, 3),
                    match_reason=f"Embedding similarity: {score:.3f}",
                )
                results.append(result)

        return results

    @staticmethod
    def _apply_filters(
        results: list[RetrievalResult], context: QueryContext
    ) -> list[RetrievalResult]:
        """Apply structured filters to retrieved results."""
        filtered = results

        # Filter by skills
        if context.skills:
            filtered = [
                r for r in filtered
                if any(s.lower() in [rs.lower() for rs in r.skills] for s in context.skills)
            ]

        # Filter by remote
        if context.is_remote is not None:
            filtered = [r for r in filtered if r.is_remote == context.is_remote]

        # Filter by seniority
        if context.seniority:
            filtered = [
                r for r in filtered
                if r.seniority and r.seniority.lower() == context.seniority.lower()
            ]

        # Filter by company
        if context.company:
            filtered = [
                r for r in filtered
                if context.company.lower() in r.company.lower()
            ]

        # Sort by score
        filtered.sort(key=lambda r: r.score, reverse=True)

        return filtered

    async def _generate_summary(
        self, results: list[RetrievalResult], context: QueryContext
    ) -> str:
        """Generate a natural-language summary of search results."""
        jobs_text = "\n".join(
            f"- {r.title} at {r.company} ({r.location or 'Remote'})"
            f" | Skills: {', '.join(r.skills[:5])}"
            for r in results[:10]
        )

        prompt = f"""\
User Query: {context.raw_query}

Search Results ({len(results)} jobs found):
{jobs_text}

Summarize the top results and highlight key insights.
"""

        try:
            response = await self._groq.chat.completions.create(
                model="llama-3.1-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_RESULT_SUMMARY},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=500,
            )
            return response.choices[0].message.content
        except Exception as exc:
            logger.warning("Summary generation failed: %s", exc)
            return None
