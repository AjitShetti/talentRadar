"""
ml/config.py
~~~~~~~~~~~~
Pipeline configuration for resume-to-job-description matching.

Provides configurable scoring weights, model paths, and processing parameters.
All weights must sum to 1.0 for proper percentage scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


# ─────────────────────────────────────────────────────────────────────────────
# Default scoring weights (must sum to 1.0)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SKILL_WEIGHT: Final[float] = 0.40
DEFAULT_EXPERIENCE_WEIGHT: Final[float] = 0.25
DEFAULT_EDUCATION_WEIGHT: Final[float] = 0.15
DEFAULT_SEMANTIC_WEIGHT: Final[float] = 0.20

# ─────────────────────────────────────────────────────────────────────────────
# Default model configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_EMBEDDING_MODEL: Final[str] = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MAX_RESUME_LENGTH: Final[int] = 50_000  # characters
DEFAULT_MAX_JD_LENGTH: Final[int] = 20_000  # characters

# ─────────────────────────────────────────────────────────────────────────────
# Known skills taxonomy (comprehensive list for skill extraction)
# ─────────────────────────────────────────────────────────────────────────────

COMMON_TECH_SKILLS: Final[list[str]] = [
    # Programming languages
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "Go", "Rust",
    "Ruby", "PHP", "Swift", "Kotlin", "Scala", "R", "MATLAB", "Perl",
    "Shell", "Bash", "SQL", "HTML", "CSS",

    # Web frameworks
    "React", "Angular", "Vue", "Next.js", "Node.js", "Express", "Django",
    "Flask", "FastAPI", "Spring Boot", "Rails", "Laravel", "ASP.NET",

    # Databases
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "DynamoDB",
    "Cassandra", "SQLite", "Oracle", "SQL Server", "CouchDB", "Neo4j",

    # Cloud & DevOps
    "AWS", "GCP", "Azure", "Docker", "Kubernetes", "Terraform", "Ansible",
    "Jenkins", "GitLab CI", "GitHub Actions", "CircleCI", "ArgoCD",

    # ML & Data
    "TensorFlow", "PyTorch", "scikit-learn", "XGBoost", "Pandas", "NumPy",
    "Spark", "Hadoop", "Airflow", "Kafka", "Databricks", "MLflow",
    "LangChain", "LangGraph", "RAG", "Transformers",

    # Tools & Methods
    "Git", "Agile", "Scrum", "REST", "GraphQL", "gRPC", "Microservices",
    "CI/CD", "TDD", "Linux", "Nginx", "Apache",
]

COMMON_SOFT_SKILLS: Final[list[str]] = [
    "Communication", "Leadership", "Teamwork", "Problem Solving",
    "Critical Thinking", "Time Management", "Adaptability",
    "Project Management", "Mentoring", "Collaboration",
]

ALL_KNOWN_SKILLS: Final[list[str]] = COMMON_TECH_SKILLS + COMMON_SOFT_SKILLS

# ─────────────────────────────────────────────────────────────────────────────
# Education level hierarchy (ordered by seniority)
# ─────────────────────────────────────────────────────────────────────────────

EDUCATION_HIERARCHY: Final[dict[str, int]] = {
    "high school": 1,
    "associate": 2,
    "bachelor": 3,
    "master": 4,
    "mba": 5,
    "phd": 6,
    "doctorate": 6,
}

EDUCATION_KEYWORDS: Final[dict[str, list[str]]] = {
    "high school": ["high school", "highschool", "secondary school", "diploma"],
    "associate": ["associate", "associate's", "a.a.", "a.s.", "community college"],
    "bachelor": ["bachelor", "bachelor's", "b.s.", "b.a.", "bsc", "b.sc", "undergraduate", " b.s ", " b.a "],
    "master": ["master", "master's", "m.s.", "m.a.", "msc", "m.sc", "m.eng", "mtech", " m.s ", " m.a ", " ms in", " ms from"],
    "mba": ["mba", "master of business"],
    "phd": ["phd", "ph.d", "doctorate", "doctoral", "dphil"],
}


@dataclass(frozen=True)
class ScoringWeights:
    """Immutable scoring weights for the matching pipeline.

    All weights are normalized to sum to 1.0.

    Attributes:
        skills: Weight for skills matching (default: 0.40)
        experience: Weight for experience matching (default: 0.25)
        education: Weight for education matching (default: 0.15)
        semantic: Weight for semantic/text similarity (default: 0.20)
    """

    skills: float = DEFAULT_SKILL_WEIGHT
    experience: float = DEFAULT_EXPERIENCE_WEIGHT
    education: float = DEFAULT_EDUCATION_WEIGHT
    semantic: float = DEFAULT_SEMANTIC_WEIGHT

    def __post_init__(self) -> None:
        """Validate weights sum to approximately 1.0."""
        total = self.skills + self.experience + self.education + self.semantic
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Scoring weights must sum to 1.0, got {total:.6f}. "
                f"skills={self.skills}, experience={self.experience}, "
                f"education={self.education}, semantic={self.semantic}"
            )

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary representation."""
        return {
            "skills": self.skills,
            "experience": self.experience,
            "education": self.education,
            "semantic": self.semantic,
        }


@dataclass(frozen=True)
class PipelineConfig:
    """Full configuration for the resume matching pipeline.

    Attributes:
        weights: ScoringWeights instance for score computation
        embedding_model: HuggingFace model name for semantic embeddings
        max_resume_length: Maximum allowed resume text length
        max_jd_length: Maximum allowed job description text length
        known_skills: List of known skills for extraction
        min_match_threshold: Minimum score to consider a match (0-100)
    """

    weights: ScoringWeights = field(default_factory=ScoringWeights)
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    max_resume_length: int = DEFAULT_MAX_RESUME_LENGTH
    max_jd_length: int = DEFAULT_MAX_JD_LENGTH
    known_skills: list[str] = field(default_factory=lambda: list(ALL_KNOWN_SKILLS))
    min_match_threshold: float = 30.0  # Below this is considered "no match"
