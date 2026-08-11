"""
domain
~~~~~~
Pure business layer — enumerations and entities with no framework or ORM coupling.

Import from here for the canonical definitions:

    from domain.entities import Company, Job, Profile, Resume, ...
    from domain.enums import ApplicationStatus, InterviewTrack, ...
"""

from domain.entities import (
    AgentMemory,
    AnalyticsSnapshot,
    ApplicationEvent,
    ApplicationStatus,
    Company,
    CompanyProfile,
    CoverLetter,
    IngestionRun,
    InterviewAnswerScore,
    InterviewPrepPlan,
    InterviewSession,
    Job,
    JobApplication,
    LearningTask,
    MarketSnapshot,
    Profile,
    Resume,
    Skill,
    User,
    UserSkill,
)
from domain.enums import (
    EmploymentType,
    IngestionStatus,
    InterviewDifficulty,
    InterviewTrack,
    JobStatus,
    LearningTaskStatus,
    MemoryType,
    SeniorityLevel,
)

__all__ = [
    # Entities
    "AgentMemory",
    "AnalyticsSnapshot",
    "ApplicationEvent",
    "Company",
    "CompanyProfile",
    "CoverLetter",
    "IngestionRun",
    "InterviewAnswerScore",
    "InterviewPrepPlan",
    "InterviewSession",
    "Job",
    "JobApplication",
    "LearningTask",
    "MarketSnapshot",
    "Profile",
    "Resume",
    "Skill",
    "User",
    "UserSkill",
    # Enums
    "ApplicationStatus",
    "EmploymentType",
    "IngestionStatus",
    "InterviewDifficulty",
    "InterviewTrack",
    "JobStatus",
    "LearningTaskStatus",
    "MemoryType",
    "SeniorityLevel",
]
