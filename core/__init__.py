"""
core
~~~~
Shared cross-cutting utilities — pagination, error hierarchy, helpers.

These are framework-agnostic and safe to import from any layer
(api, services, agents, storage) without creating circular dependencies.
"""

from core.errors import (
    ConflictError,
    DomainError,
    ExternalServiceError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from core.pagination import PageParams, PageResult

__all__ = [
    "ConflictError",
    "DomainError",
    "ExternalServiceError",
    "NotFoundError",
    "PageParams",
    "PageResult",
    "UnauthorizedError",
    "ValidationError",
]
