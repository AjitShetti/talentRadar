"""
ml/utils.py
~~~~~~~~~~~
Utility functions for the resume matching pipeline.

Provides:
- Structured logging setup
- Text processing helpers
- Score normalization and formatting
- Common validation utilities
"""

from __future__ import annotations

import logging
import re
from typing import Any

import structlog


# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────


def get_logger(name: str) -> Any:
    """Get a structured logger for the given module name.

    Uses structlog for consistent JSON-formatted logs in production
    and human-readable logs in development.

    Args:
        name: Module name, typically __name__

    Returns:
        Configured logger instance

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Processing resume", resume_id="abc123")
    """
    return structlog.get_logger(name)


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog for the ML pipeline.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=logging.StreamHandler(stream=None).stream,
        level=getattr(logging, level.upper(), logging.INFO),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Score utilities
# ─────────────────────────────────────────────────────────────────────────────


def clamp_score(score: float, min_val: float = 0.0, max_val: float = 100.0) -> float:
    """Clamp a score to the valid range [min_val, max_val].

    Args:
        score: Raw score value
        min_val: Minimum allowed value
        max_val: Maximum allowed value

    Returns:
        Clamped score rounded to 1 decimal place

    Example:
        >>> clamp_score(105.0)
        100.0
        >>> clamp_score(-5.0)
        0.0
        >>> clamp_score(85.55)
        85.6
    """
    return round(max(min_val, min(max_val, score)), 1)


def compute_weighted_score(
    scores: dict[str, float], weights: dict[str, float]
) -> float:
    """Compute a weighted average score from component scores.

    Args:
        scores: Dictionary of component scores (0-100 scale)
        weights: Dictionary of weights (should sum to 1.0)

    Returns:
        Weighted average score (0-100 scale)

    Raises:
        ValueError: If scores and weights have different keys

    Example:
        >>> scores = {"skills": 90, "experience": 80, "education": 85, "semantic": 82}
        >>> weights = {"skills": 0.4, "experience": 0.25, "education": 0.15, "semantic": 0.2}
        >>> compute_weighted_score(scores, weights)
        85.4
    """
    if set(scores.keys()) != set(weights.keys()):
        missing_in_scores = set(weights.keys()) - set(scores.keys())
        missing_in_weights = set(scores.keys()) - set(weights.keys())
        raise ValueError(
            f"Score and weight keys must match. "
            f"Missing in scores: {missing_in_scores}, "
            f"missing in weights: {missing_in_weights}"
        )

    weighted_sum = sum(scores[key] * weights[key] for key in scores)
    return clamp_score(weighted_sum)


# ─────────────────────────────────────────────────────────────────────────────
# Text helpers
# ─────────────────────────────────────────────────────────────────────────────


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncate text to maximum length, adding a suffix if truncated.

    Args:
        text: Input text
        max_length: Maximum allowed length
        suffix: Suffix to append if truncated

    Returns:
        Truncated text with suffix if needed

    Example:
        >>> truncate_text("Hello World", 8)
        'Hello...'
        >>> truncate_text("Hi", 10)
        'Hi'
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)].rstrip() + suffix


def extract_numbers(text: str) -> list[float]:
    """Extract all numbers (int and float) from text.

    Handles formats like: "5", "3.5", "5+", "5-10", "5 to 10".

    Args:
        text: Input text

    Returns:
        List of extracted numbers

    Example:
        >>> extract_numbers("5+ years experience, 3.5 years in Python")
        [5.0, 3.5]
    """
    # Match integers and decimals, with optional trailing '+'
    pattern = r"(\d+\.?\d*)\+?"
    return [float(match) for match in re.findall(pattern, text)]


def extract_year_range(text: str) -> tuple[float, float] | None:
    """Extract a year range from text like "5-10 years" or "5+ years".

    Args:
        text: Text containing year information

    Returns:
        Tuple of (min_years, max_years) or None if not found.
        max_years is float('inf') for open-ended ranges like "5+"

    Example:
        >>> extract_year_range("5+ years of experience")
        (5.0, inf)
        >>> extract_year_range("3-5 years")
        (3.0, 5.0)
    """
    # Pattern: "X+" years
    open_ended = re.search(r"(\d+\.?\d*)\s*\+\s*years?", text, re.IGNORECASE)
    if open_ended:
        return (float(open_ended.group(1)), float("inf"))

    # Pattern: "X-Y" years
    range_match = re.search(r"(\d+\.?\d*)\s*[-–to]+\s*(\d+\.?\d*)\s*years?", text, re.IGNORECASE)
    if range_match:
        return (float(range_match.group(1)), float(range_match.group(2)))

    # Pattern: "X" years (single number)
    single = re.search(r"(\d+\.?\d*)\s*years?", text, re.IGNORECASE)
    if single:
        val = float(single.group(1))
        return (val, val)

    return None
