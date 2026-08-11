"""
domain/enums.py
~~~~~~~~~~~~~~~
Pure business enumerations — no SQLAlchemy, no ORM, no framework imports.

These enums are the single source of truth for status values, types, and
classifications used across storage, services, agents, and API layers.
"""

from __future__ import annotations

from enum import Enum

# ── Ingestion ──────────────────────────────────────────────────────────────

class IngestionStatus(str, Enum):
    """Lifecycle stages of a single ingestion run."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"       # completed with some errors
    FAILED  = "failed"


# ── Job postings ───────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    """Current state of a job posting."""
    ACTIVE    = "active"
    EXPIRED   = "expired"
    FILLED    = "filled"
    DUPLICATE = "duplicate"
    ARCHIVED  = "archived"


class EmploymentType(str, Enum):
    FULL_TIME  = "full_time"
    PART_TIME  = "part_time"
    CONTRACT   = "contract"
    INTERNSHIP = "internship"
    FREELANCE  = "freelance"


class SeniorityLevel(str, Enum):
    INTERN     = "intern"
    JUNIOR     = "junior"
    MID        = "mid"
    SENIOR     = "senior"
    LEAD       = "lead"
    PRINCIPAL  = "principal"
    STAFF      = "staff"
    DIRECTOR   = "director"
    VP         = "vp"
    C_LEVEL    = "c_level"


# ── Application funnel ─────────────────────────────────────────────────────

class ApplicationStatus(str, Enum):
    """Full job-seeker funnel: saved → applied → oa → interview → offer/rejected."""
    SAVED             = "saved"
    APPLIED           = "applied"
    ONLINE_ASSESSMENT = "online_assessment"
    SCREENING         = "screening"
    INTERVIEW         = "interview"
    OFFER             = "offer"
    REJECTED          = "rejected"
    WITHDRAWN         = "withdrawn"

    @staticmethod
    def funnel_order() -> dict[str, int]:
        """Position of each stage in the funnel (for analytics)."""
        return {
            ApplicationStatus.SAVED.value: 0,
            ApplicationStatus.APPLIED.value: 1,
            ApplicationStatus.ONLINE_ASSESSMENT.value: 2,
            ApplicationStatus.SCREENING.value: 3,
            ApplicationStatus.INTERVIEW.value: 4,
            ApplicationStatus.OFFER.value: 5,
            ApplicationStatus.WITHDRAWN.value: 5,
            ApplicationStatus.REJECTED.value: 5,
        }

    @classmethod
    def is_terminal(cls, value: str) -> bool:
        return value in {cls.OFFER.value, cls.REJECTED.value, cls.WITHDRAWN.value}


# ── Interview ──────────────────────────────────────────────────────────────

class InterviewTrack(str, Enum):
    """Available mock interview catalog tracks (round types)."""
    CODING         = "coding"
    TECHNICAL      = "technical"
    BEHAVIORAL     = "behavioral"
    SYSTEM_DESIGN  = "system_design"
    # Backward-compatible aliases for the old catalogue
    PYTHON_DSA     = "python_dsa"
    PYTHON_BACKEND = "python_backend"
    SQL            = "sql"


class InterviewDifficulty(str, Enum):
    """Difficulty level chosen by the user per session."""
    BEGINNER = "beginner"
    MID      = "mid"
    SENIOR   = "senior"


# ── Memory ─────────────────────────────────────────────────────────────────

class MemoryType(str, Enum):
    """Types of agent memory entries."""
    PREFERENCE   = "preference"
    GOAL         = "goal"
    NOTE         = "note"
    APPLICATION  = "application"
    WEAKNESS     = "weakness"
    ACHIEVEMENT  = "achievement"


# ── Learning tasks ─────────────────────────────────────────────────────────

class LearningTaskStatus(str, Enum):
    PENDING   = "pending"
    IN_PROGRESS = "in_progress"
    DONE      = "done"
    SKIPPED   = "skipped"
