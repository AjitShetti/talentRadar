"""
api/routers/public.py
~~~~~~~~~~~~~~~~~~~~~
The unauthenticated surface: public role pages and the posting check.

Read-only, no user data, and built only from postings that came from an
employer's own ATS API (see ``services/public_roles.py``). The role and
company endpoints are read by the frontend's static regeneration, not on a
visitor's page load. ``/public/check`` is the one endpoint here that makes an
outbound request on demand, so it sits on the strict rate limit
(``api/rate_limit.py``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from domain.posting_urls import MAX_URL_LENGTH
from services import public_roles

router = APIRouter(prefix="/public", tags=["Public"])


class CheckRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=MAX_URL_LENGTH)


@router.get("/roles")
async def list_public_roles(limit: int = Query(default=500, ge=1, le=2000)) -> dict[str, Any]:
    """Open public roles, newest sighting first. Feeds the sitemap."""
    return {"roles": await public_roles.list_roles(limit=limit)}


@router.get("/roles/{slug}")
async def get_public_role(slug: str) -> dict[str, Any]:
    role = await public_roles.get_role(slug)
    if role is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return role


@router.get("/companies/{slug}")
async def get_public_company(slug: str) -> dict[str, Any]:
    company = await public_roles.company_roles(slug)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.post("/check")
async def check_posting(body: CheckRequest) -> dict[str, Any]:
    """Is this posting still open? Answers only for sources we can verify."""
    return await public_roles.check_url(body.url)
