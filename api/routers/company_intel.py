"""
api/routers/company_intel.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Company Intelligence endpoints.

GET /company-intel/{company_id}  - full intelligence report for a company
GET /company-intel/search?name=  - resolve a company by name
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.auth import get_current_user
from services.companies import company_intel, get_company

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/company-intel", tags=["Company Intelligence"])


async def get_current_user_id(user: Annotated[dict, Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


@router.get("/{company_id}")
async def intel_report(company_id: str, user_id: CurrentUserId):
    """Full company intelligence report by id."""
    result = await company_intel(company_id=company_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return result


@router.get("/")
async def search_company(user_id: CurrentUserId, name: str = Query(...)):
    """Resolve a company by name and return its profile + open jobs."""
    result = await get_company(name=name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No company found matching {name!r}")
    return result
