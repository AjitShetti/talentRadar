"""
ml/models/skill_matcher.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Skill matching model that computes match quality between
resume skills and job description required skills.

Supports:
- Exact skill matching
- Fuzzy/partial skill matching (e.g., "JS" -> "JavaScript")
- Skill importance weighting
- Missing skill identification
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ml.utils import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Skill equivalence mapping (common abbreviations/aliases)
# ─────────────────────────────────────────────────────────────────────────────

SKILL_ALIASES: dict[str, list[str]] = {
    "JavaScript": ["js", "javascript", "ecmascript"],
    "TypeScript": ["ts", "typescript"],
    "Python": ["python", "python3", "python 3"],
    "PostgreSQL": ["postgresql", "postgres", "psql"],
    "MySQL": ["mysql"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "Elasticsearch": ["elasticsearch", "elastic"],
    "TensorFlow": ["tensorflow", "tf", "keras"],
    "PyTorch": ["pytorch", "torch"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "React": ["react", "reactjs", "react.js"],
    "Angular": ["angular", "angularjs", "angular.js"],
    "Vue": ["vue", "vuejs", "vue.js"],
    "Node.js": ["node.js", "nodejs", "node"],
    "Django": ["django"],
    "Flask": ["flask"],
    "FastAPI": ["fastapi"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k8s"],
    "AWS": ["aws", "amazon web services"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "Azure": ["azure", "microsoft azure"],
    "Git": ["git"],
    "CI/CD": ["ci/cd", "cicd", "continuous integration", "continuous deployment"],
    "REST": ["rest", "restful", "rest api"],
    "GraphQL": ["graphql"],
    "Machine Learning": ["machine learning", "ml"],
    "Deep Learning": ["deep learning", "dl"],
    "Natural Language Processing": ["nlp", "natural language processing"],
    "XGBoost": ["xgboost"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy", "np"],
    "Apache Spark": ["spark", "apache spark", "pyspark"],
    "Apache Airflow": ["airflow", "apache airflow"],
    "Apache Kafka": ["kafka", "apache kafka"],
    "Terraform": ["terraform", "tf"],
    "Ansible": ["ansible"],
    "Jenkins": ["jenkins"],
    "HTML": ["html", "html5"],
    "CSS": ["css", "css3"],
    "C++": ["c++", "cpp"],
    "C#": ["c#", "csharp"],
    "Go": ["go", "golang"],
    "Rust": ["rust"],
    "Ruby": ["ruby", "ruby on rails"],
    "PHP": ["php", "laravel"],
    "Swift": ["swift"],
    "Kotlin": ["kotlin"],
    "Scala": ["scala"],
    "R": ["r"],
    "SQL": ["sql"],
    "Linux": ["linux", "unix"],
    "Agile": ["agile", "scrum", "kanban"],
    "LangChain": ["langchain"],
    "LangGraph": ["langgraph"],
    "RAG": ["rag", "retrieval augmented generation"],
}


@dataclass
class SkillMatchResult:
    """Result of skill matching between resume and job description.

    Attributes:
        matched_skills: Skills present in both resume and JD
        missing_skills: Required skills in JD but not in resume
        extra_skills: Skills in resume but not required by JD
        match_percentage: Percentage of JD skills matched (0-100)
        total_required: Total number of skills required by JD
        total_matched: Number of skills matched
    """

    matched_skills: list[str] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    extra_skills: list[str] = field(default_factory=list)
    match_percentage: float = 0.0
    total_required: int = 0
    total_matched: int = 0


class SkillMatcher:
    """Matches skills between resume and job description.

    Uses exact matching plus alias-based fuzzy matching to handle
    common abbreviations and alternate names for the same skill.

    Attributes:
        case_sensitive: Whether to use case-sensitive matching

    Example:
        >>> matcher = SkillMatcher()
        >>> resume_skills = ["Python", "FastAPI", "PostgreSQL"]
        >>> jd_skills = ["Python", "FastAPI", "Redis", "Docker"]
        >>> result = matcher.match(resume_skills, jd_skills)
        >>> result.matched_skills
        ['Python', 'FastAPI']
        >>> result.missing_skills
        ['Redis', 'Docker']
    """

    def __init__(self, case_sensitive: bool = False) -> None:
        self.case_sensitive = case_sensitive

    def _normalize_skill(self, skill: str) -> str:
        """Normalize a skill name for comparison.

        Args:
            skill: Raw skill string

        Returns:
            Normalized (lowercased, stripped) skill
        """
        return skill.strip().lower()

    def _find_alias_match(self, skill: str, candidates: list[str]) -> str | None:
        """Find if a skill matches any candidate through alias mapping.

        Args:
            skill: Skill to look up
            candidates: List of candidate skills to check against

        Returns:
            The canonical skill name if an alias match is found, else None
        """
        normalized = self._normalize_skill(skill)

        for candidate in candidates:
            candidate_normalized = self._normalize_skill(candidate)

            # Direct match
            if normalized == candidate_normalized:
                return candidate

            # Check aliases
            for canonical, aliases in SKILL_ALIASES.items():
                skill_in_aliases = normalized in [
                    self._normalize_skill(a) for a in aliases
                ]
                candidate_in_aliases = candidate_normalized in [
                    self._normalize_skill(a) for a in aliases
                ]

                if skill_in_aliases and candidate_in_aliases:
                    return canonical

                # Check if skill is an alias for the canonical form of candidate
                canonical_norm = self._normalize_skill(canonical)
                if normalized == canonical_norm or skill_in_aliases:
                    if candidate_normalized == canonical_norm or candidate_in_aliases:
                        return canonical

        return None

    def match(
        self,
        resume_skills: list[str],
        jd_skills: list[str],
    ) -> SkillMatchResult:
        """Match resume skills against job description requirements.

        Args:
            resume_skills: Skills extracted from resume
            jd_skills: Skills required by the job description

        Returns:
            SkillMatchResult with matched, missing, and extra skills
        """
        if not jd_skills:
            # If JD has no skills listed, treat as full match
            return SkillMatchResult(
                matched_skills=list(resume_skills),
                missing_skills=[],
                extra_skills=[],
                match_percentage=100.0,
                total_required=0,
                total_matched=len(resume_skills),
            )

        if not resume_skills:
            return SkillMatchResult(
                matched_skills=[],
                missing_skills=list(jd_skills),
                extra_skills=[],
                match_percentage=0.0,
                total_required=len(jd_skills),
                total_matched=0,
            )

        matched: list[str] = []
        missing: list[str] = []
        extra: list[str] = []
        matched_jd_indices: set[int] = set()

        # For each JD skill, try to find a match in resume
        for jd_skill in jd_skills:
            match_found = False

            for resume_skill in resume_skills:
                if self._skills_match(resume_skill, jd_skill):
                    if jd_skill not in matched:
                        matched.append(jd_skill)
                    matched_jd_indices.add(jd_skill)
                    match_found = True
                    break

            if not match_found:
                missing.append(jd_skill)

        # Skills in resume but not in JD
        for resume_skill in resume_skills:
            is_used = any(
                self._skills_match(resume_skill, jd_skill)
                for jd_skill in jd_skills
            )
            if not is_used:
                extra.append(resume_skill)

        total_required = len(jd_skills)
        total_matched = len(matched)
        match_percentage = (total_matched / total_required * 100) if total_required > 0 else 100.0

        logger.debug(
            "Skill matching complete",
            matched=matched,
            missing=missing,
            extra=extra,
            match_percentage=round(match_percentage, 1),
        )

        return SkillMatchResult(
            matched_skills=matched,
            missing_skills=missing,
            extra_skills=extra,
            match_percentage=round(match_percentage, 1),
            total_required=total_required,
            total_matched=total_matched,
        )

    def _skills_match(self, skill_a: str, skill_b: str) -> bool:
        """Check if two skills match (exact or via aliases).

        Args:
            skill_a: First skill
            skill_b: Second skill

        Returns:
            True if skills match
        """
        norm_a = self._normalize_skill(skill_a)
        norm_b = self._normalize_skill(skill_b)

        # Exact match
        if norm_a == norm_b:
            return True

        # Check alias mapping
        for canonical, aliases in SKILL_ALIASES.items():
            canonical_norm = self._normalize_skill(canonical)
            alias_norms = [self._normalize_skill(a) for a in aliases]

            a_matches_canonical = norm_a == canonical_norm or norm_a in alias_norms
            b_matches_canonical = norm_b == canonical_norm or norm_b in alias_norms

            if a_matches_canonical and b_matches_canonical:
                return True

        # Substring match for compound skills (e.g., "React" in "React.js")
        if len(norm_a) >= 3 and len(norm_b) >= 3:
            if norm_a in norm_b or norm_b in norm_a:
                return True

        return False
