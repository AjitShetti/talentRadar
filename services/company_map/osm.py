"""
services/company_map/osm.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Company offices in Bengaluru from OpenStreetMap, via the Overpass API (ODbL;
the map credits OpenStreetMap contributors).

One query selects every node, way and relation tagged ``office=company`` or
``office=it`` with a ``name`` and a ``website`` inside the Bengaluru bbox;
ways and relations come back with their ``center``. An office without a
website is not an employer we can key on (identity is the domain), so the
query never asks for it and :func:`parse` skips one if it appears.

Overpass is flaky in a particular way: a busy server answers HTTP 200 with
an HTML error page, or with JSON whose ``remark`` says the query ran out of
time. :func:`fetch` treats both as failures and tries the next public
instance; when every one fails it raises :class:`OverpassError` for the
caller to catch.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from domain.areas import BENGALURU_BBOX, in_bengaluru
from domain.company_identity import normalize_domain
from services.company_map.types import ImportedCompany

ENDPOINTS: tuple[str, ...] = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
USER_AGENT = "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"
_TIMEOUT_S = 120.0

_SOUTH, _WEST, _NORTH, _EAST = BENGALURU_BBOX

QUERY = f"""\
[out:json][timeout:90];
nwr["office"~"^(company|it)$"]["name"]["website"]({_SOUTH},{_WEST},{_NORTH},{_EAST});
out center tags;
"""


class OverpassError(RuntimeError):
    """Every Overpass instance failed or answered with something other than data."""


def _decode(response: httpx.Response) -> dict[str, Any]:
    """The JSON body of a successful Overpass answer, or :class:`OverpassError`."""
    if response.status_code != 200:
        raise OverpassError(f"HTTP {response.status_code}")
    try:
        payload = json.loads(response.content)
    except ValueError as exc:
        snippet = response.text[:120].replace("\n", " ")
        raise OverpassError(f"non-JSON body: {snippet!r}") from exc
    if not isinstance(payload, dict):
        raise OverpassError("JSON body is not an object")
    remark = payload.get("remark")
    if isinstance(remark, str) and "error" in remark.lower():
        raise OverpassError(f"query failed: {remark[:200]}")
    return payload


async def fetch(client: httpx.AsyncClient) -> dict[str, Any]:
    """Run :data:`QUERY` on the first Overpass instance that answers with data."""
    failures: list[str] = []
    for endpoint in ENDPOINTS:
        try:
            response = await client.post(
                endpoint,
                data={"data": QUERY},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=_TIMEOUT_S,
            )
            return _decode(response)
        except (OverpassError, httpx.HTTPError) as exc:
            failures.append(f"{endpoint}: {type(exc).__name__} {exc}".rstrip())
    raise OverpassError("all Overpass instances failed: " + "; ".join(failures))


_ADDRESS_TAGS = ("addr:housenumber", "addr:street", "addr:suburb", "addr:postcode")
_QID = re.compile(r"^Q\d+$")


def _tag(tags: dict[str, Any], key: str) -> str | None:
    value = tags.get(key)
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _position(element: dict[str, Any]) -> tuple[float, float] | None:
    """A node's own point; a way's or relation's ``center``."""
    source = element if element.get("type") == "node" else element.get("center")
    if not isinstance(source, dict):
        return None
    lat, lon = source.get("lat"), source.get("lon")
    if not isinstance(lat, int | float) or not isinstance(lon, int | float):
        return None
    return float(lat), float(lon)


def _website(tags: dict[str, Any]) -> str | None:
    """The ``website`` tag, with a scheme if the mapper left it off."""
    value = _tag(tags, "website")
    if value is None:
        return None
    # OSM allows several values separated by ";"; the first is the main one.
    value = value.split(";", 1)[0].strip()
    if not value:
        return None
    return value if "://" in value else f"https://{value}"


def _address(tags: dict[str, Any]) -> str | None:
    parts = [part for key in _ADDRESS_TAGS if (part := _tag(tags, key))]
    return ", ".join(parts) if parts else None


def _office(element: dict[str, Any]) -> ImportedCompany | None:
    tags = element.get("tags")
    if not isinstance(tags, dict):
        return None
    name = _tag(tags, "name")
    website_url = _website(tags)
    if name is None or website_url is None:
        return None
    domain = normalize_domain(website_url)
    position = _position(element)
    if domain is None or position is None or not in_bengaluru(*position):
        return None
    wikidata_id = _tag(tags, "wikidata")
    return ImportedCompany(
        name=name,
        domain=domain,
        website_url=website_url,
        wikidata_id=wikidata_id if wikidata_id and _QID.match(wikidata_id) else None,
        lat=position[0],
        lng=position[1],
        address=_address(tags),
    )


def parse(payload: dict[str, Any]) -> list[ImportedCompany]:
    """
    One :class:`ImportedCompany` per office element, in payload order.

    Skipped: an element with no name or website, a website that is someone
    else's profile page (no domain of its own), and a point outside the
    Bengaluru bbox. A payload without ``elements`` (an error answer) is
    simply empty.
    """
    elements = payload.get("elements")
    if not isinstance(elements, list):
        return []
    offices: list[ImportedCompany] = []
    for element in elements:
        if isinstance(element, dict) and (office := _office(element)) is not None:
            offices.append(office)
    return offices
