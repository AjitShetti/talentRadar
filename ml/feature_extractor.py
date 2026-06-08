"""
ml/feature_extractor.py
~~~~~~~~~~~~~~~~~~~~~~~
Extract structured features from resume and job description text.

Provides:
- Skill extraction (from known taxonomy + pattern-based)
- Experience parsing (years of experience)
- Education level classification
- Keyword extraction
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ml.config import (
    ALL_KNOWN_SKILLS,
    EDUCATION_HIERARCHY,
    EDUCATION_KEYWORDS,
)
from ml.preprocessing import CleanedText
from ml.utils import extract_year_range, get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Feature data structures
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SkillFeatures:
    """Extracted skill features from text.

    Attributes:
        matched_skills: Skills found in the text (from known taxonomy)
        all_tokens: All potential skill tokens extracted from text
        skill_density: Ratio of skill mentions to total words
    """

    matched_skills: list[str] = field(default_factory=list)
    all_tokens: list[str] = field(default_factory=list)
    skill_density: float = 0.0


@dataclass
class ExperienceFeatures:
    """Extracted experience features.

    Attributes:
        years_min: Minimum years of experience found
        years_max: Maximum years of experience found (inf if open-ended)
        raw_mentions: Raw text snippets mentioning experience
        has_experience_info: Whether any experience info was found
    """

    years_min: float = 0.0
    years_max: float = 0.0
    raw_mentions: list[str] = field(default_factory=list)
    has_experience_info: bool = False


@dataclass
class EducationFeatures:
    """Extracted education features.

    Attributes:
        highest_level: Highest education level detected (from hierarchy)
        level_score: Numeric score for education level (1-6)
        raw_mentions: Raw text snippets mentioning education
        has_education_info: Whether any education info was found
    """

    highest_level: str = ""
    level_score: int = 0
    raw_mentions: list[str] = field(default_factory=list)
    has_education_info: bool = False


@dataclass
class KeywordFeatures:
    """Extracted keyword features.

    Attributes:
        keywords: Important keywords/phrases extracted from text
        keyword_density: Ratio of keyword content to total text
        top_keywords: Top 10 most frequent meaningful words
    """

    keywords: list[str] = field(default_factory=list)
    keyword_density: float = 0.0
    top_keywords: list[str] = field(default_factory=list)


@dataclass
class ExtractedFeatures:
    """Complete feature set extracted from text.

    Attributes:
        skills: Skill features
        experience: Experience features
        education: Education features
        keywords: Keyword features
        word_count: Total word count
        text_type: "resume" or "job_description"
    """

    skills: SkillFeatures
    experience: ExperienceFeatures
    education: EducationFeatures
    keywords: KeywordFeatures
    word_count: int = 0
    text_type: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Common stop words to filter out during keyword extraction
# ─────────────────────────────────────────────────────────────────────────────

_STOP_WORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "must",
    "this", "that", "these", "those", "i", "you", "he", "she", "it", "we",
    "they", "me", "him", "her", "us", "them", "my", "your", "his", "its",
    "our", "their", "what", "which", "who", "whom", "when", "where", "why",
    "how", "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "no", "not", "only", "own", "same", "so", "than",
    "too", "very", "just", "also", "now", "here", "there", "then", "once",
    "if", "as", "about", "into", "through", "during", "before", "after",
    "above", "below", "between", "under", "again", "further", "while",
    "able", "also", "including", "related", "experience", "working",
    "work", "years", "year", "least", "minimum", "plus", "team", "role",
    "position", "responsibilities", "requirements", "qualifications",
    "skills", "skill", "strong", "excellent", "good", "proficient",
}


# ─────────────────────────────────────────────────────────────────────────────
# Skill extraction
# ─────────────────────────────────────────────────────────────────────────────


def extract_skills(
    text: str,
    known_skills: list[str] | None = None,
) -> SkillFeatures:
    """Extract skills from text using known skill taxonomy.

    Uses case-insensitive matching against a comprehensive skill list.
    Handles multi-word skills (e.g., "Machine Learning", "React.js").

    Args:
        text: Cleaned text to extract skills from
        known_skills: List of known skills to match against.
                     Defaults to ALL_KNOWN_SKILLS from config.

    Returns:
        SkillFeatures with matched skills and metadata

    Example:
        >>> text = "Experienced Python developer with FastAPI and PostgreSQL skills"
        >>> features = extract_skills(text)
        >>> features.matched_skills
        ['Python', 'FastAPI', 'PostgreSQL']
    """
    if known_skills is None:
        known_skills = list(ALL_KNOWN_SKILLS)

    text_lower = text.lower()
    matched: list[str] = []

    for skill in known_skills:
        # Escape regex special chars in skill name (e.g., "C++", ".NET")
        escaped_skill = re.escape(skill)
        # Use word boundary matching for short skills (1-2 chars like "R", "Go", "C#")
        # to avoid false positives (e.g., "R" in "mentioned")
        if len(skill) <= 2:
            pattern = r'\b' + escaped_skill + r'\b'
            if re.search(pattern, text_lower, re.IGNORECASE):
                matched.append(skill)
        else:
            if re.search(escaped_skill, text_lower, re.IGNORECASE):
                matched.append(skill)

    # Sort matched skills by length (longer/more specific skills first)
    matched.sort(key=lambda s: (-len(s), s))

    # Estimate skill density
    word_count = max(len(text.split()), 1)
    skill_density = min(len(matched) / max(word_count / 100, 1), 1.0)

    logger.debug(
        "Skills extracted",
        num_skills=len(matched),
        skills=matched[:10],  # Log first 10 for brevity
    )

    return SkillFeatures(
        matched_skills=matched,
        all_tokens=matched,
        skill_density=round(skill_density, 3),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Experience extraction
# ─────────────────────────────────────────────────────────────────────────────


def extract_experience(text: str) -> ExperienceFeatures:
    """Extract years of experience information from text.

    Searches for patterns like:
    - "5+ years of experience"
    - "3-5 years"
    - "minimum 5 years"
    - "at least 3 years"

    Args:
        text: Cleaned text to extract experience from

    Returns:
        ExperienceFeatures with parsed experience data

    Example:
        >>> text = "Requires 5+ years of Python experience"
        >>> features = extract_experience(text)
        >>> features.years_min
        5.0
        >>> features.years_max
        inf
    """
    # Common experience patterns
    experience_patterns = [
        r"(?:\d+\.?\d*\+?\s*(?:[-–to]+\s*\d+\.?\d*)?\s*years?)",
        r"(?:minimum|min\.?|at\s+least)\s+(\d+\.?\d*)\s*years?",
        r"(\d+\.?\d*)\s*(?:or\s+more|plus|\+)\s*years?",
        r"(\d+\.?\d*)\s*-\s*(\d+\.?\d*)\s*years?",
    ]

    raw_mentions: list[str] = []
    year_ranges: list[tuple[float, float]] = []

    # Search for experience-related sentences
    sentences = re.split(r"[.;,]", text)
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # Check if sentence mentions years/experience
        if re.search(r"years?|experience|exp\.", sentence, re.IGNORECASE):
            year_range = extract_year_range(sentence)
            if year_range is not None:
                raw_mentions.append(sentence)
                year_ranges.append(year_range)

    if not year_ranges:
        return ExperienceFeatures(has_experience_info=False)

    # Take the maximum range found (most demanding requirement for JD,
    # most experience claimed for resume)
    min_years = min(r[0] for r in year_ranges)
    max_years = max(r[1] for r in year_ranges)

    logger.debug(
        "Experience extracted",
        years_min=min_years,
        years_max=max_years if max_years != float("inf") else "inf",
        num_mentions=len(raw_mentions),
    )

    return ExperienceFeatures(
        years_min=min_years,
        years_max=max_years,
        raw_mentions=raw_mentions[:5],  # Keep top 5 mentions
        has_experience_info=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Education extraction
# ─────────────────────────────────────────────────────────────────────────────


def extract_education(text: str) -> EducationFeatures:
    """Extract education level from text.

    Searches for education-related keywords and determines the highest
    education level mentioned.

    Args:
        text: Cleaned text to extract education from

    Returns:
        EducationFeatures with parsed education data

    Example:
        >>> text = "MS in Computer Science from Stanford University"
        >>> features = extract_education(text)
        >>> features.highest_level
        'master'
        >>> features.level_score
        4
    """
    text_lower = text.lower()
    detected_levels: list[str] = []
    raw_mentions: list[str] = []

    # First try regex-based detection for abbreviations at word boundaries
    edu_abbrev_patterns = {
        "phd": [r"\bph\.?d\.?\b", r"\bdoctorate\b", r"\bdoctoral\b", r"\bdphil\b"],
        "mba": [r"\bmba\b", r"\bmaster\s+of\s+business\b"],
        "master": [
            r"\bmaster['']?\b", r"\bm\.s\.?\b", r"\bm\.a\.?\b",
            r"\bmsc\b", r"\bm\.sc\.?\b", r"\bm\.eng\b", r"\bmtech\b",
            r"\bms\b\s+(?:in|from|at)", r"\bma\b\s+(?:in|from|at)",
        ],
        "bachelor": [
            r"\bbachelor['']?\b", r"\bb\.s\.?\b", r"\bb\.a\.?\b",
            r"\bbsc\b", r"\bb\.sc\.?\b", r"\bundergraduate\b",
        ],
        "associate": [
            r"\bassociate['']?\b", r"\ba\.a\.?\b", r"\ba\.s\.?\b",
            r"\bcommunity\s+college\b",
        ],
        "high school": [
            r"\bhigh\s*school\b", r"\bhighschool\b",
            r"\bsecondary\s+school\b", r"\bdiploma\b",
        ],
    }

    for level, patterns in edu_abbrev_patterns.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                detected_levels.append(level)
                # Find the sentence/context containing the match
                sentences = text.split(".")
                for sentence in sentences:
                    if re.search(pattern, sentence.lower()):
                        raw_mentions.append(sentence.strip())
                        break
                break  # One match per level is enough

    if not detected_levels:
        return EducationFeatures(has_education_info=False)

    # Deduplicate and find highest level
    detected_levels = list(set(detected_levels))
    highest_level = max(
        detected_levels,
        key=lambda l: EDUCATION_HIERARCHY.get(l, 0),
    )
    level_score = EDUCATION_HIERARCHY.get(highest_level, 0)

    logger.debug(
        "Education extracted",
        highest_level=highest_level,
        level_score=level_score,
        all_detected=detected_levels,
    )

    return EducationFeatures(
        highest_level=highest_level,
        level_score=level_score,
        raw_mentions=raw_mentions[:5],
        has_education_info=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Keyword extraction
# ─────────────────────────────────────────────────────────────────────────────


def extract_keywords(text: str, top_n: int = 20) -> KeywordFeatures:
    """Extract meaningful keywords from text.

    Uses simple frequency-based extraction with stop word filtering.
    Returns the most frequent meaningful words.

    Args:
        text: Cleaned text to extract keywords from
        top_n: Number of top keywords to return

    Returns:
        KeywordFeatures with extracted keywords

    Example:
        >>> text = "Python developer building REST APIs with FastAPI"
        >>> features = extract_keywords(text, top_n=5)
        >>> features.top_keywords
        ['python', 'developer', 'building', 'rest', 'apis']
    """
    # Tokenize and filter
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    filtered_words = [w for w in words if w not in _STOP_WORDS]

    if not filtered_words:
        return KeywordFeatures()

    # Count frequencies
    freq: dict[str, int] = {}
    for word in filtered_words:
        freq[word] = freq.get(word, 0) + 1

    # Sort by frequency
    sorted_keywords = sorted(freq.items(), key=lambda x: -x[1])
    top_keywords = [kw for kw, _ in sorted_keywords[:top_n]]

    # Compute keyword density (what fraction of text is keywords)
    total_words = max(len(words), 1)
    keyword_density = len(filtered_words) / total_words

    return KeywordFeatures(
        keywords=[kw for kw, _ in sorted_keywords],
        keyword_density=round(keyword_density, 3),
        top_keywords=top_keywords,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API: Full feature extraction
# ─────────────────────────────────────────────────────────────────────────────


def extract_features(
    cleaned: CleanedText,
    *,
    text_type: str = "resume",
    known_skills: list[str] | None = None,
) -> ExtractedFeatures:
    """Extract all features from preprocessed text.

    This is the main entry point for feature extraction. It extracts:
    - Skills from known taxonomy
    - Experience (years)
    - Education level
    - Keywords

    Args:
        cleaned: CleanedText from preprocessing pipeline
        text_type: "resume" or "job_description"
        known_skills: Custom skill list to match against

    Returns:
        ExtractedFeatures with all feature categories

    Example:
        >>> from ml.preprocessing import preprocess_text
        >>> cleaned = preprocess_text(resume_text)
        >>> features = extract_features(cleaned, text_type="resume")
        >>> features.skills.matched_skills
        ['Python', 'FastAPI']
        >>> features.experience.years_min
        5.0
    """
    text = cleaned.cleaned

    # Use section-specific text if available for better extraction
    skills_text = cleaned.sections.get("skills", text)
    experience_text = cleaned.sections.get("experience", text)
    education_text = cleaned.sections.get("education", text)

    skills = extract_skills(skills_text, known_skills=known_skills)
    experience = extract_experience(experience_text)
    education = extract_education(education_text)
    keywords = extract_keywords(text)

    logger.info(
        "Features extracted",
        text_type=text_type,
        num_skills=len(skills.matched_skills),
        has_experience=experience.has_experience_info,
        education_level=education.highest_level,
        num_keywords=len(keywords.top_keywords),
    )

    return ExtractedFeatures(
        skills=skills,
        experience=experience,
        education=education,
        keywords=keywords,
        word_count=cleaned.word_count,
        text_type=text_type,
    )
