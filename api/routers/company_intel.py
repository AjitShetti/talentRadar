"""
api/routers/company_intel.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Company Intelligence endpoints.

    GET    /company-intel/                         browsable company directory
    GET    /company-intel/facets                   filter options for the directory
    GET    /company-intel/map?city=                every listed company as a map row
    GET    /company-intel/map/for-me               companies hiring for my target role
    GET    /company-intel/news                     the map's news strip
    GET    /company-intel/resolve?name=            resolve one company by name
    GET    /company-intel/{id}                     full intelligence report
    GET    /company-intel/{id}/contacts            talent contacts for a company
    POST   /company-intel/{id}/contacts            save a contact privately
    POST   /company-intel/{id}/contacts/discover   sourced web lookup
    POST   /company-intel/{id}/people/discover     sourced founder lookup (Tavily)
    DELETE /company-intel/{id}/contacts/{cid}      remove a saved contact

Route order matters: ``/facets``, ``/map``, ``/map/for-me``, ``/news`` and
``/resolve`` are declared before ``/{company_id}`` so they are not swallowed
by the path parameter.

Nothing here calls an LLM or fetches LinkedIn. The map, list, drawer and news
read stored rows; ``/people/discover`` is the one outside call, made on an
explicit click and cached per company.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status

from api.auth import get_current_user
from api.schemas.company_schemas import (
    ContactCreateSchema,
    ContactDiscoverySchema,
    ContactSchema,
    FounderDiscoverySchema,
    HiringForYouSchema,
    NewsItemSchema,
)
from domain.areas import BENGALURU_AREAS
from domain.company_taxonomy import SECTORS, SIZES, STAGES
from services.companies import (
    ALL_TIERS,
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
from services.company_map import people as company_map_people
from services.company_map import read as company_map_read

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/company-intel", tags=["Company Intelligence"])


async def get_current_user_id(user: Annotated[dict, Depends(get_current_user)]) -> str:
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: missing sub claim")
    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]

_AREA_NAMES = frozenset(area.name for area in BENGALURU_AREAS)
NewsCategory = Literal["funding", "acquisition", "launch", "other"]


def _check_vocabulary(
    tier: str | None = None,
    stage: str | None = None,
    sector: str | None = None,
    area: str | None = None,
    size: str | None = None,
) -> None:
    """422 for a filter value outside its vocabulary, naming what is allowed."""
    checks: tuple[tuple[str, str | None, Any], ...] = (
        ("tier", tier, ALL_TIERS),
        ("stage", stage, STAGES),
        ("sector", sector, SECTORS),
        ("area", area, _AREA_NAMES),
        ("size", size, SIZES),
    )
    for field, value, allowed in checks:
        if value and value not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown {field} {value!r}. Expected one of: {', '.join(sorted(allowed))}",
            )


@router.get("/")
async def directory(
    user_id: CurrentUserId,
    city: str | None = Query("Bengaluru", description="City with an engineering office"),
    tier: str | None = Query(None, description=f"One of: {', '.join(ALL_TIERS)}"),
    industry: str | None = Query(None),
    q: str | None = Query(None, description="Free-text search over name, industry and description"),
    has_open_roles: bool = Query(False, description="Only companies with live postings"),
    stage: str | None = Query(None, description=f"One of: {', '.join(STAGES)}"),
    sector: str | None = Query(None, description=f"One of: {', '.join(SECTORS)}"),
    area: str | None = Query(None, description="A canonical Bengaluru area, e.g. 'HSR Layout'"),
    size: str | None = Query(None, description=f"One of: {', '.join(SIZES)}"),
    raised_within_days: int | None = Query(
        None, ge=1, le=3650, description="Only companies with funding news this recent"
    ),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    name: str | None = Query(
        None,
        deprecated=True,
        description="Deprecated alias for `q`, kept for older clients.",
    ),
) -> dict[str, Any]:
    """The Company Intel directory — every listed company in `city`."""
    _check_vocabulary(tier=tier, stage=stage, sector=sector, area=area, size=size)
    return await list_companies(
        city=city,
        tier=tier,
        industry=industry,
        q=q or name,
        has_open_roles=has_open_roles,
        stage=stage,
        sector=sector,
        area=area,
        size=size,
        raised_within_days=raised_within_days,
        limit=limit,
        offset=offset,
    )


@router.get("/facets")
async def facets(
    user_id: CurrentUserId,
    city: str | None = Query("Bengaluru"),
    stage: str | None = Query(None),
    sector: str | None = Query(None),
    area: str | None = Query(None),
    size: str | None = Query(None),
    raised_within_days: int | None = Query(None, ge=1, le=3650),
) -> dict[str, Any]:
    """Cities, tiers, industries, stages, sectors, areas and sizes, with counts."""
    _check_vocabulary(stage=stage, sector=sector, area=area, size=size)
    return await directory_facets(
        city=city,
        stage=stage,
        sector=sector,
        area=area,
        size=size,
        raised_within_days=raised_within_days,
    )


@router.get("/map")
async def company_map(
    request: Request,
    user_id: CurrentUserId,
    city: str = Query("Bengaluru", min_length=2, max_length=64),
) -> Response:
    """
    Every listed company in `city` as one positional row:
    `[id, name, lat, lng, precision, tier, stage, sector, area, size,
    open_roles, raised_90d]`. `lat`/`lng` are null at `city` precision.

    The same for every user, so it is cached server-side for an hour and
    carries an ETag; a matching `If-None-Match` gets a bodiless 304.
    """
    payload = await company_map_read.map_rows(city=city)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if etag in request.headers.get("if-none-match", ""):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@router.get("/map/for-me", response_model=HiringForYouSchema)
async def company_map_for_me(user_id: CurrentUserId) -> dict[str, Any]:
    """Ids of listed companies with an open Bengaluru role matching your target role."""
    return {"hiring_for_you": await company_map_read.hiring_for_you(user_id)}


@router.get("/news", response_model=list[NewsItemSchema])
async def company_news(
    user_id: CurrentUserId,
    limit: int = Query(10, ge=1, le=50),
    category: NewsCategory | None = Query(None),
) -> list[dict[str, Any]]:
    """The newest stored news items linked to a listed company."""
    return await company_map_read.latest_news(limit=limit, category=category)


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
    result = await company_intel(company_id=company_id, user_id=user_id)
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


@router.post("/{company_id}/people/discover", response_model=FounderDiscoverySchema)
async def discover_people(company_id: str, user_id: CurrentUserId) -> dict[str, Any]:
    """
    Look up this company's founders on the public web.

    One search per company, ever; the answer is cached and the people found
    are stored with the search result as their source. A person is accepted
    only from a result that is itself a LinkedIn profile naming a founder or
    CEO role at this company. LinkedIn itself is never requested. When the
    monthly search allowance is spent, `available` is false and `message`
    says so.
    """
    return await company_map_people.discover_founders(company_id)
