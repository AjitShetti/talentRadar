"""
core/errors.py
~~~~~~~~~~~~~~~
Domain exception hierarchy — shared across all layers.

These exceptions carry enough context to be translated into HTTP responses
by the API layer without the API needing to know service-specific error types.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base for all domain-level errors.

    Attributes:
        message:  Human-readable error description.
        code:     Machine-readable error code (for API responses).
        details:  Extra structured context (field name, resource id, etc.).
    """

    def __init__(
        self,
        message: str,
        code: str = "domain_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class NotFoundError(DomainError):
    """A requested resource does not exist."""

    def __init__(self, resource: str, identifier: Any, **kwargs: Any) -> None:
        super().__init__(
            message=f"{resource} not found: {identifier}",
            code="not_found",
            details={"resource": resource, "identifier": str(identifier)},
            **kwargs,
        )


class ValidationError(DomainError):
    """Input failed business-rule validation."""

    def __init__(self, message: str, field: str | None = None, **kwargs: Any) -> None:
        details = kwargs.pop("details", {})
        if field:
            details["field"] = field
        super().__init__(
            message=message,
            code="validation_error",
            details=details,
            **kwargs,
        )


class ConflictError(DomainError):
    """Operation conflicts with current state (duplicate, stale update, etc.)."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message=message, code="conflict", **kwargs)


class UnauthorizedError(DomainError):
    """Actor lacks permission / authentication for the operation."""

    def __init__(self, message: str = "Unauthorized", **kwargs: Any) -> None:
        super().__init__(message=message, code="unauthorized", **kwargs )


class ExternalServiceError(DomainError):
    """An external service (LLM, scraper, storage) failed."""

    def __init__(self, service: str, message: str, **kwargs: Any) -> None:
        super().__init__(
            message=f"{service} error: {message}",
            code="external_service_error",
            details={"service": service},
            **kwargs,
        )
