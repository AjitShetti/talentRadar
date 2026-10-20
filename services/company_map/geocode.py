"""
services/company_map/geocode.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Where a company is, and how much of that position to believe.

Nominatim (OpenStreetMap's geocoder) answers three kinds of question here:

* ``address`` — an address printed on the company's site → ``street``, but
  only when Nominatim matched a road or something finer (``place_rank`` ≥ 26).
  A short footer address such as "Koramangala, Bengaluru 560034" resolves to
  the suburb node; that places the company in a neighbourhood, not on a
  street, so it is snapped to the nearest area centroid → ``area``, exactly
  like a PIN code. A city-level hit (rank ≤ 16) places nothing, and a hit
  with no readable rank is treated as the lesser claim, ``area``.
* ``postcode`` — a PIN code, via a structured ``postalcode=`` lookup. The hit
  is only the PIN area's middle, so it is snapped to the nearest known area
  centroid → ``area``.

For every ``area`` hit this module returns the centroid itself; the caller
applies :func:`domain.areas.jitter` so pins sharing a centroid do not stack.
* ``name`` — a name search bounded to Bengaluru, believed only for an
  office, building or amenity whose OSM name nearly equals the company's
  → ``exact``.

A hit outside :data:`domain.areas.BENGALURU_BBOX` never pins a company, even
though the request is already bounded: a same-named place elsewhere must not
put a Bengaluru employer in Mumbai.

Usage policy (https://operations.osmfoundation.org/policies/nominatim/): an
identifying User-Agent, at most one request per second — :func:`geocode`
sleeps a second after every call — and results stored on the row by the
caller so nothing is looked up twice. :func:`geocode` never raises.
"""

from __future__ import annotations

import asyncio
import logging
from difflib import SequenceMatcher
from typing import Any

import httpx

from domain.areas import (
    BENGALURU_BBOX,
    area_for_point,
    area_for_text,
    centroid,
    in_bengaluru,
    jitter,
)
from services.company_map.types import GeoHit

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"

#: Seconds to wait after every Nominatim request (usage policy: 1 req/s).
_PAUSE_S = 1.0
_TIMEOUT_S = 10.0

#: OSM classes a name search may pin a company to.
_NAME_CLASSES = frozenset({"office", "building", "amenity"})
_NAME_SIMILARITY = 0.9

#: A PIN code's area spans several kilometres and its Nominatim point is the
#: middle of it, so the nearest area centroid within 5 km stands in for it —
#: twice the 2.5 km a street address gets. Beyond that, no area is claimed.
_POSTCODE_AREA_KM = 5.0

#: Nominatim's search rank (jsonv2 ``place_rank``,
#: https://nominatim.org/release-docs/latest/customize/Ranking/): 26–27 are
#: roads, 28–30 houses, buildings and POIs — a street pin is honest. 21–25 are
#: postcodes and blocks, 17–20 towns, suburbs and neighbourhoods — an area.
_STREET_RANK = 26
#: 16 and below is a city, county or state: no area can honestly be claimed.
_CITY_RANK = 16
#: How far an address-derived area hit may sit from the centroid it snaps to;
#: the same 2.5 km :func:`domain.areas.area_for_point` gives a street.
_ADDRESS_AREA_KM = 2.5

_MODES = frozenset({"address", "postcode", "name"})


def _viewbox() -> str:
    """Nominatim's ``viewbox``: ``west,north,east,south`` (lon,lat,lon,lat)."""
    south, west, north, east = BENGALURU_BBOX
    return f"{west},{north},{east},{south}"


def _point(hit: dict[str, Any]) -> tuple[float, float] | None:
    """The hit's coordinate (Nominatim sends strings), if it parses."""
    try:
        return float(hit["lat"]), float(hit["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def _osm_class(hit: dict[str, Any]) -> str:
    # format=jsonv2 calls it "category"; format=json calls it "class".
    return str(hit.get("category") or hit.get("class") or "")


def _place_rank(hit: dict[str, Any]) -> int | None:
    """The hit's ``place_rank`` (an int in jsonv2), or ``None`` if unreadable."""
    rank = hit.get("place_rank")
    if isinstance(rank, bool):
        return None
    if isinstance(rank, int):
        return rank
    if isinstance(rank, str) and rank.strip().isdigit():
        return int(rank)
    return None


def _area_hit(lat: float, lng: float, *, max_km: float, source: str) -> GeoHit | None:
    """The nearest area's centroid as an ``area`` hit, or ``None`` if none is near."""
    area = area_for_point(lat, lng, max_km=max_km)
    middle = centroid(area) if area else None
    if area is None or middle is None:
        return None
    return GeoHit(lat=middle[0], lng=middle[1], precision="area", source=source, area=area)


def _similar(a: str, b: str) -> bool:
    ratio = SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()
    return ratio >= _NAME_SIMILARITY


def parse_nominatim(
    payload: list[dict[str, Any]], *, mode: str, name: str | None = None
) -> GeoHit | None:
    """
    The first usable hit in a Nominatim result list, or ``None``.

    A hit is usable when its coordinate parses, lies inside the Bengaluru
    bbox, and satisfies ``mode``: ``address`` gives ``street`` at
    ``place_rank`` ≥ 26, is unusable at ≤ 16, and otherwise (rank missing
    included) needs an area within :data:`_ADDRESS_AREA_KM` and gives that
    ``area``; ``postcode`` needs an area within :data:`_POSTCODE_AREA_KM`;
    ``name`` needs an office/building/amenity whose name is ≥ 0.9 similar to
    ``name`` (no ``name``, no match). An ``area`` hit is the centroid,
    unjittered.
    """
    if mode not in _MODES:
        raise ValueError(f"unknown geocode mode: {mode!r}")
    if mode == "name" and not (name and name.strip()):
        return None

    for hit in payload:
        if not isinstance(hit, dict):
            continue
        point = _point(hit)
        if point is None or not in_bengaluru(*point):
            continue
        lat, lng = point

        if mode == "address":
            rank = _place_rank(hit)
            if rank is not None and rank >= _STREET_RANK:
                return GeoHit(
                    lat=lat,
                    lng=lng,
                    precision="street",
                    source="nominatim",
                    area=area_for_point(lat, lng),
                )
            if rank is not None and rank <= _CITY_RANK:
                continue
            # A suburb, neighbourhood or postcode, or no rank at all (believed
            # as the lesser claim): never a solid pin.
            area_hit = _area_hit(lat, lng, max_km=_ADDRESS_AREA_KM, source="nominatim")
            if area_hit is None:
                continue
            return area_hit

        if mode == "postcode":
            pin_hit = _area_hit(lat, lng, max_km=_POSTCODE_AREA_KM, source="pincode")
            if pin_hit is None:
                continue
            return pin_hit

        # mode == "name"
        hit_name = hit.get("name")
        if _osm_class(hit) not in _NAME_CLASSES or not isinstance(hit_name, str):
            continue
        if name is not None and _similar(name, hit_name):
            return GeoHit(
                lat=lat,
                lng=lng,
                precision="exact",
                source="nominatim",
                area=area_for_point(lat, lng),
            )
    return None


async def geocode(
    client: httpx.AsyncClient,
    *,
    address: str | None = None,
    postcode: str | None = None,
    name: str | None = None,
) -> GeoHit | None:
    """
    One Nominatim lookup, bounded to Bengaluru. Never raises.

    Exactly one input is used — ``address`` if given, else ``postcode``,
    else ``name`` — so each call is one request. With nothing to look up no
    request is made. After a request (successful or not) it sleeps one
    second, which keeps a sequential caller inside the 1 req/s policy.
    """
    params: dict[str, str] = {
        "format": "jsonv2",
        "viewbox": _viewbox(),
        "bounded": "1",
        "countrycodes": "in",
        "limit": "5",
    }
    query_name: str | None = None
    if address and address.strip():
        mode = "address"
        params["q"] = address.strip()
    elif postcode and postcode.strip():
        mode = "postcode"
        params["postalcode"] = postcode.strip()
    elif name and name.strip():
        mode = "name"
        query_name = name.strip()
        params["q"] = query_name
    else:
        return None

    try:
        response = await client.get(
            NOMINATIM_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=_TIMEOUT_S,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return None
        return parse_nominatim(payload, mode=mode, name=query_name)
    except Exception as exc:  # never raise from a geocode
        logger.warning("nominatim %s lookup failed: %s", mode, exc)
        return None
    finally:
        await asyncio.sleep(_PAUSE_S)


def resolve_location(
    company_id: str, *, known: GeoHit | None, area_hint: str | None
) -> GeoHit | None:
    """
    The position to store for a company. Pure.

    ``known`` (a curated, Wikidata, OSM or Nominatim hit) wins outright.
    Otherwise ``area_hint`` — a canonical area name or free text naming one
    — places the company at that area's centroid, nudged by
    ``jitter(company_id, …)`` so neighbours do not stack, as ``area``.
    Otherwise ``None``: the company is listed but not pinned.
    """
    if known is not None:
        return known
    if not area_hint:
        return None
    area = area_hint if centroid(area_hint) is not None else area_for_text(area_hint)
    middle = centroid(area) if area else None
    if area is None or middle is None:
        return None
    lat, lng = jitter(company_id, *middle)
    return GeoHit(lat=lat, lng=lng, precision="area", source="area", area=area)
