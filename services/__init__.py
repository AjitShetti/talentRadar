"""
services/
~~~~~~~~~
Deterministic tool layer for TalentRadar.

Agents (LangGraph) and the BFF (FastAPI) call these pure functions only —
no ORM / DB logic lives in agent code. Each module is a single domain toolset:

    base         — shared helpers (parse_uuid, as_list)
    jobs         — search, match scoring, job ranking
    companies    — company lookup + intelligence
    market       — demand / salary trend analytics
    llm          — LLM-powered text generation (summaries, ATS, cover letters)
    resumes      — ATS analysis, tailoring, skill extraction
    interviews   — question generation, answer evaluation, adaptive difficulty
    applications — application tracker + funnel analytics
    career       — weaknesses + learning recommendations
    profiles     — onboarding / profile management
    agent_memory — personal-agent memory + next-best-action
"""

from services.base import as_list, parse_uuid  # noqa: F401
from services.profiles import (  # noqa: F401
    get_or_create_profile,
    get_profile,
    upsert_profile,
)
from services.jobs import (  # noqa: F401
    calculate_match,
    rank_jobs_for_profile,
    search_jobs,
    session_safe_search,
)
from services.companies import company_intel, get_company  # noqa: F401
from services.llm import (  # noqa: F401
    generate_ats_analysis,
    generate_career_advice,
    generate_cover_letter,
    get_llm,
)
from services.resumes import (  # noqa: F401
    analyze_resume,
    generate_cover_letter as resume_cover_letter,
    skill_gap,
    tailor_resume,
)
from services.interviews import (  # noqa: F401
    adaptive_difficulty,
    evaluate_answer,
    generate_prep_plan,
    generate_questions,
)
from services.applications import (  # noqa: F401
    tracker_analytics,
    update_application,
)
from services.career import identify_weaknesses, recommend_learning  # noqa: F401
from services.agent_memory import (  # noqa: F401
    get_memories,
    recommend_next_action,
    remember,
)

__all__ = [
    "adaptive_difficulty",
    "analyze_resume",
    "as_list",
    "calculate_match",
    "company_intel",
    "evaluate_answer",
    "generate_ats_analysis",
    "generate_career_advice",
    "generate_cover_letter",
    "generate_market_summary",
    "generate_prep_plan",
    "generate_questions",
    "get_company",
    "get_llm",
    "get_market_trends",
    "get_memories",
    "get_or_create_profile",
    "get_profile",
    "identify_weaknesses",
    "parse_uuid",
    "rank_jobs_for_profile",
    "recommend_learning",
    "recommend_next_action",
    "record_market_snapshot",
    "remember",
    "resume_cover_letter",
    "search_jobs",
    "session_safe_search",
    "skill_gap",
    "tailor_resume",
    "tracker_analytics",
    "update_application",
    "upsert_profile",
]
