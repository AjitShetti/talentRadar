"""
services/base.py
~~~~~~~~~~~~~~~~
Shared helpers for the deterministic service/tool layer.

Every service function is deterministic where possible and returns
plain Pydantic/dict payloads (never ORM objects) so the agent layer
and API layer can consume them without leaking SQLAlchemy details.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    """Coerce a string to uuid.UUID, tolerating None / invalid input."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


def as_list(value: Any) -> list[str]:
    """Normalise a scalar/list/None into a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if v is not None]
