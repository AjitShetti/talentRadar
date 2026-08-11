"""
core/pagination.py
~~~~~~~~~~~~~~~~~~
Shared pagination types used across API routers and services.

Provides a consistent pagination contract so every list endpoint returns
the same shape and enforces uniform limits.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PageParams(BaseModel):
    """Incoming pagination parameters for list endpoints."""

    model_config = ConfigDict(frozen=True)

    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    page_size: int = Field(
        default=20, ge=1, le=100, description="Items per page (max 100)"
    )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


class PageResult(BaseModel):
    """Paginated response envelope returned by list endpoints."""

    model_config = ConfigDict(frozen=True)

    items: list
    total: int = Field(ge=0, description="Total items matching the query")
    page: int = Field(ge=1, description="Current page number")
    page_size: int = Field(ge=1, description="Items per page requested")
    pages: int = Field(ge=0, description="Total number of pages")

    @classmethod
    def create(
        cls,
        items: list,
        total: int,
        page_params: PageParams,
    ) -> PageResult:
        """Build a PageResult from items + total + params, computing pages."""
        pages = (total + page_params.page_size - 1) // page_params.page_size if total > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page_params.page,
            page_size=page_params.page_size,
            pages=pages,
        )
