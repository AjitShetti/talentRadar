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
from domain.geo import is_india, mentions_foreign_country
from ingestion.embeddings.chroma_store import ChromaJobStore
from ingestion.embeddings.embedder import embed_texts
from storage.repository import UnitOfWork
from storage.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

ROLE_KEYWORDS: set[str] = {
    "dev", "developer", "development", "engineer", "engineering",
    "software engineer", "software dev", "software developer",
    "programmer", "specialist", "fullstack", "full-stack", "full stack",
    "backend", "back-end", "back end", "frontend", "front-end", "front end",
    "lead", "senior", "junior", "mid", "principal", "staff", "architect",
    "software",
}


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
            # Step 1 & 2: Build filters and search ChromaDB
            _ = embed_texts([context.raw_query])
            where = {}
            if context.is_remote is not None:
                where["is_remote"] = context.is_remote
            if context.seniority:
                where["seniority"] = context.seniority
            if context.company:
                where["company"] = context.company

            # Use chroma store search
            chroma_results = self._chroma.search(
                query=context.raw_query,
                n_results=context.limit * 2,  # Slight over-fetch for python-side skill filtering
                where=where if where else None,
            )

            # Step 3: Build RetrievalResult list
            results = await self._build_results(chroma_results, context)

            # Step 4: Apply remaining filters (like skills, since Chroma doesn't natively do array contains well yet)
            results = self._apply_filters(results, context)

            # Step 5: Fallback to database search if vector store returns fewer than 3 results
            if len(results) < 3:
                logger.info(
                    "ChromaDB returned %d results for query %r; falling back to relational DB search",
                    len(results), context.raw_query,
                )
                db_results = await self._search_db_fallback(context)
                existing_ids = {r.job_id for r in results}
                for db_res in db_results:
                    if db_res.job_id not in existing_ids:
                        results.append(db_res)
                        existing_ids.add(db_res.job_id)

            # Step 6: Truncate to limit
            results = results[:context.limit]

            # Step 7: Generate summary (optional)
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

    async def _search_db_fallback(self, context: QueryContext) -> list[RetrievalResult]:
        """Fallback search against PostgreSQL via JobRepository.search()."""
        try:
            async with AsyncSessionLocal() as session:
                uow = UnitOfWork(session)
                jobs, _ = await uow.jobs.search(
                    query=context.raw_query,
                    is_remote=context.is_remote,
                    india_only=True,
                    limit=context.limit,
                )
                return [
                    RetrievalResult(
                        job_id=str(job.external_id or job.id),
                        title=job.title,
                        company=job.company.name if job.company else "Unknown",
                        location=job.location_raw or f"{job.city or ''}, {job.country or ''}".strip(", "),
                        is_remote=job.is_remote,
                        seniority=job.seniority.value if job.seniority else None,
                        skills=job.skills or [],
                        source_url=job.source_url,
                        score=0.80,
                        match_reason="PostgreSQL relational query match",
                    )
                    for job in jobs
                ]
        except Exception as exc:
            logger.warning("DB search fallback failed: %s", exc)
            return []

    async def _build_results(
        self, chroma_results: list[dict[str, Any]] | dict[str, Any], context: QueryContext
    ) -> list[RetrievalResult]:
        """Convert ChromaDB results to RetrievalResult objects."""
        results = []

        if not chroma_results:
            return results

        rows: list[dict[str, Any]] = []
        if isinstance(chroma_results, dict):
            raw_ids = chroma_results.get("ids", [[]])
            ids_list = raw_ids[0] if raw_ids and isinstance(raw_ids[0], list) else []
            if not ids_list:
                return []
            documents = (chroma_results.get("documents") or [[]])[0]
            metadatas = (chroma_results.get("metadatas") or [[]])[0]
            distances = (chroma_results.get("distances") or [[]])[0]
            for idx, item_id in enumerate(ids_list):
                rows.append({
                    "id": item_id,
                    "document": documents[idx] if idx < len(documents) else "",
                    "metadata": metadatas[idx] if idx < len(metadatas) else {},
                    "distance": distances[idx] if idx < len(distances) else 0.0,
                })
        elif isinstance(chroma_results, list):
            rows = chroma_results

        if not rows:
            return results

        ids = [r["id"] for r in rows]

        async with AsyncSessionLocal() as session:
            uow = UnitOfWork(session)

            # Fetch full job records from DB in a single batch.
            # NOTE: external_id is a stable MD5 fingerprint — no source filter needed.
            jobs = await uow.jobs.get_by_external_ids(ids)
            jobs_by_ext_id = {job.external_id: job for job in jobs}

            for row in rows:
                job_id = row["id"]
                metadata = row.get("metadata") or {}
                distance = row.get("distance", 0.0)
                score = 1.0 - distance  # Convert distance to score

                # Fetch full job record from memory
                job = jobs_by_ext_id.get(job_id)
                if not job:
                    continue

                result = RetrievalResult(
                    job_id=job_id,
                    title=job.title,
                    company=metadata.get("company", "Unknown"),
                    location=metadata.get("location") or job.location_raw,
                    is_remote=metadata.get("is_remote", job.is_remote),
                    seniority=metadata.get("seniority") or (job.seniority.value if job.seniority else None),
                    skills=metadata.get("skills_str", "").split(", ") if metadata.get("skills_str") else (job.skills or []),
                    source_url=metadata.get("source_url") or job.source_url,
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
        # India-only board: drop anything that names a foreign country. Results
        # with an unreadable or empty location are kept — at query time recall
        # matters more, and the ingestion pipeline is already strict.
        filtered = [
            r for r in results
            if not (r.location and mentions_foreign_country(r.location) and not is_india(r.location))
        ]

        if context.is_remote is not None:
            filtered = [r for r in filtered if r.is_remote == context.is_remote]

        if context.seniority:
            filtered = [
                r for r in filtered
                if r.seniority and context.seniority.lower() in r.seniority.lower()
            ]

        if context.company:
            filtered = [
                r for r in filtered
                if r.company and context.company.lower() in r.company.lower()
            ]

        # Filter by skills after sanitizing generic role words
        if context.skills:
            real_skills = [
                s for s in context.skills
                if s.lower().strip() not in ROLE_KEYWORDS
            ]
            if real_skills:
                filtered = [
                    r for r in filtered
                    if any(
                        s.lower() in [rs.lower() for rs in r.skills]
                        or s.lower() in r.title.lower()
                        for s in real_skills
                    )
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
                model="openai/gpt-oss-120b",
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
