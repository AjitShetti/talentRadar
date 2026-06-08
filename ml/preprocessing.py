"""
ml/preprocessing.py
~~~~~~~~~~~~~~~~~~~
Text cleaning and normalization for resumes and job descriptions.

Provides:
- HTML tag removal
- Whitespace normalization
- Section detection (Experience, Education, Skills, etc.)
- Text tokenization and cleaning
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field

from ml.utils import get_logger, truncate_text

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns
# ─────────────────────────────────────────────────────────────────────────────

# HTML tags
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")

# Multiple whitespace -> single space
_MULTI_WHITESPACE = re.compile(r"\s+")

# Bullet points and special characters
_BULLET_PATTERN = re.compile(r"[\u2022\u2023\u25E6\u2043\u2219\u25AA\u25AB\u2022•\-*]\s*")

# Email addresses
_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

# URLs
_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")

# Phone numbers (basic patterns)
_PHONE_PATTERN = re.compile(
    r"(?:\+?\d{1,3}[-.\s]?)?"  # optional country code
    r"(?:\(?\d{3}\)?[-.\s]?)"  # area code
    r"\d{3}[-.\s]?\d{4}"       # number
)

# Section headers (common resume/JD sections)
_SECTION_HEADERS: dict[str, re.Pattern] = {
    "summary": re.compile(
        r"^(?:professional\s+)?(?:summary|profile|objective|about\s+me)",
        re.IGNORECASE | re.MULTILINE,
    ),
    "experience": re.compile(
        r"^(?:work\s+)?(?:experience|employment|work\s+history|professional\s+experience)",
        re.IGNORECASE | re.MULTILINE,
    ),
    "education": re.compile(
        r"^(?:education|academic\s+background|qualifications)",
        re.IGNORECASE | re.MULTILINE,
    ),
    "skills": re.compile(
        r"^(?:technical\s+)?(?:skills|competencies|technologies|tools)",
        re.IGNORECASE | re.MULTILINE,
    ),
    "certifications": re.compile(
        r"^(?:certifications|licenses|credentials)",
        re.IGNORECASE | re.MULTILINE,
    ),
    "projects": re.compile(
        r"^(?:projects|portfolio|personal\s+projects)",
        re.IGNORECASE | re.MULTILINE,
    ),
}


@dataclass
class CleanedText:
    """Container for cleaned text with metadata.

    Attributes:
        original: Original input text
        cleaned: Cleaned and normalized text
        sections: Detected sections with their text content
        word_count: Number of words in cleaned text
        char_count: Number of characters in cleaned text
    """

    original: str
    cleaned: str
    sections: dict[str, str] = field(default_factory=dict)
    word_count: int = 0
    char_count: int = 0

    def __post_init__(self) -> None:
        """Compute derived metadata."""
        self.word_count = len(self.cleaned.split())
        self.char_count = len(self.cleaned)


# ─────────────────────────────────────────────────────────────────────────────
# Text cleaning pipeline
# ─────────────────────────────────────────────────────────────────────────────


def remove_html(text: str) -> str:
    """Remove HTML tags from text.

    Args:
        text: Input text potentially containing HTML

    Returns:
        Text with HTML tags removed

    Example:
        >>> remove_html("<p>Hello <b>World</b></p>")
        'Hello World'
    """
    return _HTML_TAG_PATTERN.sub(" ", text)


def remove_personal_info(text: str) -> str:
    """Remove personal information (emails, URLs, phone numbers) from text.

    Args:
        text: Input text

    Returns:
        Text with personal information removed
    """
    text = _EMAIL_PATTERN.sub(" ", text)
    text = _URL_PATTERN.sub(" ", text)
    text = _PHONE_PATTERN.sub(" ", text)
    return text


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace: collapse multiple spaces/newlines into single spaces.

    Args:
        text: Input text

    Returns:
        Text with normalized whitespace

    Example:
        >>> normalize_whitespace("Hello   world\\n\\n  test")
        'Hello world test'
    """
    # Replace bullet points with spaces
    text = _BULLET_PATTERN.sub(" ", text)
    # Replace newlines with spaces
    text = text.replace("\n", " ").replace("\r", " ")
    # Collapse multiple whitespace
    text = _MULTI_WHITESPACE.sub(" ", text)
    return text.strip()


def remove_special_chars(text: str, keep_chars: str = "") -> str:
    """Remove special characters, keeping only alphanumeric and specified chars.

    Args:
        text: Input text
        keep_chars: Additional characters to keep (e.g., "+.#")

    Returns:
        Cleaned text with only alphanumeric and kept characters
    """
    allowed = set(string.ascii_letters + string.digits + " " + keep_chars)
    return "".join(char if char in allowed else " " for char in text)


def clean_text(
    text: str,
    *,
    remove_html_tags: bool = True,
    remove_personal: bool = True,
    normalize_ws: bool = True,
    remove_special: bool = False,
    keep_chars: str = "+.#-",
    lowercase: bool = False,
) -> str:
    """Full text cleaning pipeline.

    Applies a sequence of cleaning operations:
    1. Remove HTML tags (optional)
    2. Remove personal information (optional)
    3. Normalize whitespace (optional)
    4. Remove special characters (optional)
    5. Lowercase (optional)

    Args:
        text: Raw input text
        remove_html_tags: Whether to strip HTML tags
        remove_personal: Whether to remove emails, URLs, phone numbers
        normalize_ws: Whether to normalize whitespace
        remove_special: Whether to remove special characters
        keep_chars: Characters to keep when removing special chars
        lowercase: Whether to convert to lowercase

    Returns:
        Cleaned text

    Example:
        >>> clean_text("<p>Python Developer with 5+ years exp.</p>")
        'Python Developer with 5+ years exp.'
    """
    if not text or not text.strip():
        return ""

    if remove_html_tags:
        text = remove_html(text)

    if remove_personal:
        text = remove_personal_info(text)

    if normalize_ws:
        text = normalize_whitespace(text)

    if remove_special:
        text = remove_special_chars(text, keep_chars=keep_chars)

    if lowercase:
        text = text.lower()

    return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Section detection
# ─────────────────────────────────────────────────────────────────────────────


def detect_sections(text: str) -> list[tuple[str, int, int]]:
    """Detect document sections based on common header patterns.

    Args:
        text: Input text (cleaned)

    Returns:
        List of (section_name, start_pos, end_pos) tuples,
        ordered by position in text

    Example:
        >>> text = "EXPERIENCE\\nWorked at Google...\\nEDUCATION\\n BS CS"
        >>> detect_sections(text)
        [('experience', 0, 30), ('education', 30, 50)]
    """
    sections: list[tuple[str, int, int]] = []

    for section_name, pattern in _SECTION_HEADERS.items():
        matches = list(pattern.finditer(text))
        for match in matches:
            start = match.start()
            # End is either next section start or end of text
            sections.append((section_name, start, -1))

    # Sort by position
    sections.sort(key=lambda x: x[1])

    # Fill in end positions
    for i, (name, start, _) in enumerate(sections):
        if i + 1 < len(sections):
            end = sections[i + 1][1]
        else:
            end = len(text)
        sections[i] = (name, start, end)

    return sections


def extract_section_text(text: str) -> dict[str, str]:
    """Extract text content for each detected section.

    Args:
        text: Input text (cleaned)

    Returns:
        Dictionary mapping section names to their text content

    Example:
        >>> extract_section_text("SKILLS\\nPython, Java\\nEXPERIENCE\\n5 years...")
        {'skills': 'Python, Java', 'experience': '5 years...'}
    """
    sections = detect_sections(text)
    result: dict[str, str] = {}

    for section_name, start, end in sections:
        # Extract text after the header (skip the header itself)
        header_match = _SECTION_HEADERS[section_name].search(text)
        if header_match:
            content_start = header_match.end()
            section_content = text[content_start:end].strip()
            result[section_name] = section_content

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def preprocess_text(
    text: str,
    *,
    max_length: int = 50_000,
    detect_sections_flag: bool = True,
) -> CleanedText:
    """Full preprocessing pipeline for resume or job description text.

    This is the main entry point for text preprocessing. It:
    1. Truncates text if too long
    2. Cleans text (removes HTML, personal info, normalizes whitespace)
    3. Optionally detects and extracts sections

    Args:
        text: Raw input text
        max_length: Maximum allowed text length (truncates if longer)
        detect_sections_flag: Whether to detect and extract sections

    Returns:
        CleanedText object with cleaned text and metadata

    Raises:
        ValueError: If text is empty after cleaning

    Example:
        >>> resume_text = "<h1>John Doe</h1><p>Python developer...</p>"
        >>> result = preprocess_text(resume_text)
        >>> result.cleaned
        'John Doe Python developer...'
        >>> result.word_count
        4
    """
    if not text or not text.strip():
        raise ValueError("Input text is empty or None")

    # Truncate if too long
    text = truncate_text(text, max_length)

    # Clean text
    cleaned = clean_text(text)

    if not cleaned:
        raise ValueError("Text is empty after cleaning")

    # Detect sections
    sections: dict[str, str] = {}
    if detect_sections_flag:
        sections = extract_section_text(text)

    logger.debug(
        "Text preprocessed",
        original_length=len(text),
        cleaned_length=len(cleaned),
        word_count=len(cleaned.split()),
        sections_detected=list(sections.keys()),
    )

    return CleanedText(
        original=text,
        cleaned=cleaned,
        sections=sections,
    )
