"""
api/routers/company_intel.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Company Intelligence endpoints.

    GET    /company-intel/                         browsable company directory
    GET    /company-intel/facets                   filter options for the directory
    GET    /company-intel/resolve?name=            resolve one company by name
    GET    /company-intel/{id}                     full intelligence report
    GET    /company-intel/{id}/contacts            talent contacts for a company
    POST   /company-intel/{id}/contacts            save a contact privately
    POST   /company-intel/{id}/contacts/discover   sourced web lookup
    DELETE /company-intel/{id}/contacts/{cid}      remove a saved contact

Route order matters: ``/facets`` and ``/resolve`` are declared before
``/{company_id}`` so they are not swallowed by the path parameter.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from api.auth import get_current_user
from api.schemas.company_schemas import (
    ContactCreateSchema,
    ContactDiscoverySchema,
    ContactSchema,
)
from services.companies import (
    TIER_ORDER,
    company_intel,
    directory_facets,
    get_company,
    list_companies,
)
from services.company_contacts import (
    delete_contact,
    discover_contacts,
    list_contacts,
    save_contact,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/company-intel", tags=["Company Intelligence"])


async def get_current_user_id(user: Annotated[dict, Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]


@router.get("/")
async def directory(
    user_id: CurrentUserId,
    city: str | None = Query("Bengaluru", description="City with an engineering office"),
    tier: str | None = Query(None, description=f"One of: {', '.join(TIER_ORDER)}"),
    industry: str | None = Query(None),
    q: str | None = Query(None, description="Free-text search over name, industry and description"),
    has_open_roles: bool = Query(False, description="Only companies with live postings"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    name: str | None = Query(
        None,
        deprecated=True,
        description="Deprecated alias for `q`, kept for older clients.",
    ),
) -> dict[str, Any]:
    """The Company Intel directory — every catalogued company hiring in `city`."""
    if tier and tier not in TIER_ORDER:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown tier {tier!r}. Expected one of: {', '.join(TIER_ORDER)}",
        )
    return await list_companies(
        city=city,
        tier=tier,
        industry=industry,
        q=q or name,
        has_open_roles=has_open_roles,
        limit=limit,
        offset=offset,
    )


@router.get("/facets")
async def facets(
    user_id: CurrentUserId,
    city: str | None = Query("Bengaluru"),
) -> dict[str, Any]:
    """Cities, tiers and industries available in the directory, with counts."""
    return await directory_facets(city=city)


@router.get("/resolve")
async def resolve_company(user_id: CurrentUserId, name: str = Query(...)) -> dict[str, Any]:
    """Resolve a company by name and return its profile + open jobs."""
    result = await get_company(name=name)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No company found matching {name!r}")
    return result


@router.get("/{company_id}")
async def intel_report(company_id: str, user_id: CurrentUserId) -> dict[str, Any]:
    """
    Full intelligence report for one company, including its talent contacts so
    the detail panel opens in a single round trip.
    """
    result = await company_intel(company_id=company_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Company not found")
    result["contacts"] = await list_contacts(company_id, user_id)
    return result


@router.get("/{company_id}/contacts", response_model=list[ContactSchema])
async def company_contacts(company_id: str, user_id: CurrentUserId) -> list[dict[str, Any]]:
    """Curated public contacts for this company plus the caller's own saved ones."""
    return await list_contacts(company_id, user_id)


@router.post("/{company_id}/contacts", response_model=ContactSchema, status_code=201)
async def create_contact(
    company_id: str,
    user_id: CurrentUserId,
    payload: ContactCreateSchema = Body(...),
) -> dict[str, Any]:
    """Save a talent contact to the caller's private list for this company."""
    try:
        contact = await save_contact(
            company_id,
            user_id,
            kind=payload.kind,
            name=payload.name,
            title=payload.title,
            email=payload.email,
            linkedin_url=payload.linkedin_url,
            notes=payload.notes,
            source_url=payload.source_url,
            verified=payload.verified,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if contact is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return contact


@router.post("/{company_id}/contacts/discover", response_model=ContactDiscoverySchema)
async def discover(company_id: str, user_id: CurrentUserId) -> dict[str, Any]:
    """
    Search the public web for this company's talent contacts.

    Returns candidates read off real pages, each with the URL it came from and
    flagged unverified. Nothing is guessed from a naming pattern — if no page
    publishes an address, the result is empty and says so.
    """
    result = await discover_contacts(company_id)
    if result.get("company") is None and not result.get("available"):
        raise HTTPException(status_code=404, detail="Company not found")
    return result


@router.delete("/{company_id}/contacts/{contact_id}", status_code=204)
async def remove_contact(company_id: str, contact_id: str, user_id: CurrentUserId) -> None:
    """Delete one of the caller's saved contacts. Curated entries are shared and stay put."""
    if not await delete_contact(contact_id, user_id):
        raise HTTPException(
            status_code=404,
            detail="Contact not found, or it is a shared curated entry you cannot delete.",
        )
