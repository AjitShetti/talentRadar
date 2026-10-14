"""
services/company_map/wikidata.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Bengaluru-headquartered businesses from Wikidata (CC0).

One SPARQL query selects every item whose headquarters (``P159``) is
Bengaluru, that is an instance of a business (``P31/P279*`` ``Q4830453``) and
that states an official website (``P856``), plus the optional facts the map
uses. The query service returns one row per combination of multi-valued
optionals, so :func:`parse` groups rows by item.

People are only what Wikidata states: founders (``P112``) and the CEO
(``P169``), each restricted to humans. Every person's ``source_url`` is the
company's Wikidata page; their profile links are built from the identifiers
Wikidata holds for them and never fetched.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit

import httpx

from domain.areas import in_bengaluru
from domain.company_identity import normalize_domain
from domain.company_taxonomy import sector_from_text, tier_for_import
from services.company_map.types import ImportedCompany, PersonCandidate

ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"
_TIMEOUT_S = 60.0

# ``hint:optimizer "None"`` makes Blazegraph evaluate the patterns in the
# order written: Bengaluru HQ first (a few hundred items), the class check
# last. Left to its optimizer it starts from every business on Wikidata and
# times out. ``BIND(EXISTS {...})`` comes back unbound on this endpoint, so
# the two flags are optional sub-selects instead.
SPARQL = """\
SELECT DISTINCT ?item ?itemLabel ?itemDescription ?website ?coord ?inception ?employees
       ?industryLabel ?xHandle ?linkedinCompany ?githubOrg ?logo ?isVc ?isPublic
       ?person ?personLabel ?role ?personX ?personLinkedin ?personGithub
WHERE {
  hint:Query hint:optimizer "None" .
  ?item wdt:P159 wd:Q1355 .
  ?item wdt:P856 ?website .
  ?item wdt:P31/wdt:P279* wd:Q4830453 .
  OPTIONAL { ?item wdt:P625 ?coord . }
  OPTIONAL { ?item wdt:P571 ?inception . }
  OPTIONAL { ?item wdt:P1128 ?employees . }
  OPTIONAL { ?item wdt:P452 ?industry . }
  OPTIONAL { ?item wdt:P2002 ?xHandle . }
  OPTIONAL { ?item wdt:P4264 ?linkedinCompany . }
  OPTIONAL { ?item wdt:P2037 ?githubOrg . }
  OPTIONAL { ?item wdt:P154 ?logo . }
  OPTIONAL { SELECT DISTINCT ?item ?isVc WHERE {
    ?item wdt:P31/wdt:P279* wd:Q3487908 . BIND(true AS ?isVc) } }
  OPTIONAL { SELECT DISTINCT ?item ?isPublic WHERE {
    ?item wdt:P414 ?exchange . BIND(true AS ?isPublic) } }
  OPTIONAL {
    { ?item wdt:P112 ?person . BIND("founder" AS ?role) }
    UNION
    { ?item wdt:P169 ?person . BIND("leader" AS ?role) }
    ?person wdt:P31 wd:Q5 .
    OPTIONAL { ?person wdt:P2002 ?personX . }
    OPTIONAL { ?person wdt:P6634 ?personLinkedin . }
    OPTIONAL { ?person wdt:P2037 ?personGithub . }
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""


async def fetch(client: httpx.AsyncClient) -> dict[str, Any]:
    """Run :data:`SPARQL` against the public query service. Raises on failure."""
    response = await client.get(
        ENDPOINT,
        params={"query": SPARQL, "format": "json"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
        timeout=_TIMEOUT_S,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Wikidata returned a non-object JSON body")
    return payload


_ENTITY = "http://www.wikidata.org/entity/"
_WIKI = "https://www.wikidata.org/wiki/"
_QID = re.compile(r"^Q\d+$")
_POINT = re.compile(r"^\s*Point\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)\s*$", re.IGNORECASE)
_YEAR = re.compile(r"^\+?(\d{1,4})-")
#: Handles a company keeps for customer service, not its main account.
_SUPPORT_HANDLE = re.compile(r"support|help|care|service", re.IGNORECASE)
_ROLE_ORDER = {"founder": 0, "leader": 1}

Row = dict[str, Any]


def _value(row: Row, key: str) -> str | None:
    cell = row.get(key)
    if not isinstance(cell, dict):
        return None
    value = cell.get("value")
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _values(rows: list[Row], key: str) -> list[str]:
    """Every distinct value of ``key`` across ``rows``, sorted."""
    return sorted({v for row in rows if (v := _value(row, key)) is not None})


def _qid(uri: str | None) -> str | None:
    if not uri or not uri.startswith(_ENTITY):
        return None
    qid = uri.removeprefix(_ENTITY)
    return qid if _QID.match(qid) else None


def _label(rows: list[Row], key: str) -> str | None:
    """An English label; the label service falls back to the bare QID, which is no name."""
    for label in _values(rows, key):
        if not _QID.match(label):
            return label
    return None


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _x_url(handles: list[str], owner: str) -> str | None:
    """
    The main account among several: not a support desk, then one carrying
    the owner's name (Infosys keeps ``Infosys`` and ``Finacle``), then
    alphabetical.
    """
    cleaned = [h.lstrip("@") for h in handles if h.lstrip("@")]
    if not cleaned:
        return None
    words = [w for w in (_compact(part) for part in owner.split()) if len(w) >= 3]

    def rank(handle: str) -> tuple[bool, bool, str, str]:
        named = any(word in _compact(handle) for word in words)
        return (bool(_SUPPORT_HANDLE.search(handle)), not named, handle.lower(), handle)

    return f"https://x.com/{min(cleaned, key=rank)}"


def _first_url(prefix: str, ids: list[str]) -> str | None:
    return f"{prefix}{ids[0]}" if ids else None


def _website(name: str, urls: list[str]) -> tuple[str, str] | None:
    """
    One official website and its domain.

    An item may list several (a regional site, an old domain). Prefer one
    whose domain carries the company's name, then the site's root, then
    https, then no path, then the shortest.
    """
    compact = _compact(name)
    ranked: list[tuple[tuple[bool, bool, bool, bool, int, str], str]] = []
    for url in urls:
        domain = normalize_domain(url)
        if domain is None:
            continue
        stem = domain.split(".", 1)[0]
        named = bool(compact) and (stem in compact or compact in stem)
        parts = urlsplit(url)
        root = (parts.hostname or "").lower() in {domain, f"www.{domain}"}
        secure = url.lower().startswith("https://")
        has_path = bool(parts.path.strip("/"))
        ranked.append(((not named, not root, not secure, has_path, len(url), url), domain))
    if not ranked:
        return None
    key, domain = min(ranked)
    return key[-1], domain


def _point(values: list[str]) -> tuple[float, float] | None:
    """The first ``Point(lng lat)`` inside Bengaluru; a point elsewhere pins nothing."""
    for value in values:
        match = _POINT.match(value)
        if not match:
            continue
        lng, lat = float(match.group(1)), float(match.group(2))
        if in_bengaluru(lat, lng):
            return lat, lng
    return None


def _founded_year(values: list[str]) -> int | None:
    years = [int(m.group(1)) for v in values if (m := _YEAR.match(v))]
    positive = [y for y in years if y > 0]
    return min(positive) if positive else None


def _employee_count(values: list[str]) -> int | None:
    counts: list[int] = []
    for value in values:
        try:
            count = int(float(value))
        except ValueError:
            continue
        if count > 0:
            counts.append(count)
    return max(counts) if counts else None


def _https(url: str) -> str:
    return "https://" + url.removeprefix("http://") if url.startswith("http://") else url


def _sector(labels: list[str]) -> str | None:
    """
    The sector the industry labels name together.

    Joined into one string so the taxonomy's own rule order decides between
    them: "payment system" with "software industry" is fintech, not SaaS.
    """
    named = [label for label in labels if not _QID.match(label)]
    return sector_from_text("; ".join(named)) if named else None


def _people(company_qid: str, rows: list[Row]) -> tuple[PersonCandidate, ...]:
    """
    Founders and the CEO, one entry per person.

    A founder who is also the CEO stays a founder, titled CEO: one person is
    one row in the drawer.
    """
    by_person: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        person_qid = _qid(_value(row, "person"))
        if person_qid:
            by_person[person_qid].append(row)

    source_url = f"{_WIKI}{company_qid}"
    people: list[PersonCandidate] = []
    for person_qid, person_rows in by_person.items():
        name = _label(person_rows, "personLabel")
        roles = set(_values(person_rows, "role")) & set(_ROLE_ORDER)
        if name is None or not roles:
            continue
        people.append(
            PersonCandidate(
                name=name,
                kind="founder" if "founder" in roles else "leader",
                title="CEO" if "leader" in roles else None,
                source_url=source_url,
                source_kind="wikidata",
                linkedin_url=_first_url(
                    "https://www.linkedin.com/in/", _values(person_rows, "personLinkedin")
                ),
                x_url=_x_url(_values(person_rows, "personX"), name),
                github_url=_first_url("https://github.com/", _values(person_rows, "personGithub")),
                profile_url=f"{_WIKI}{person_qid}",
            )
        )
    people.sort(key=lambda p: (_ROLE_ORDER[p.kind], p.name.lower(), p.name))
    return tuple(people)


def _company(qid: str, rows: list[Row]) -> ImportedCompany | None:
    name = _label(rows, "itemLabel")
    if name is None:
        return None
    site = _website(name, _values(rows, "website"))
    if site is None:
        return None
    website_url, domain = site

    point = _point(_values(rows, "coord"))
    founded_year = _founded_year(_values(rows, "inception"))
    is_vc = "true" in _values(rows, "isVc")
    is_public = "true" in _values(rows, "isPublic")
    logos = _values(rows, "logo")
    github_orgs = _values(rows, "githubOrg")
    return ImportedCompany(
        name=name,
        domain=domain,
        website_url=website_url,
        wikidata_id=qid,
        lat=point[0] if point else None,
        lng=point[1] if point else None,
        founded_year=founded_year,
        employee_count=_employee_count(_values(rows, "employees")),
        x_url=_x_url(_values(rows, "xHandle"), name),
        linkedin_url=_first_url(
            "https://www.linkedin.com/company/", _values(rows, "linkedinCompany")
        ),
        github_org=github_orgs[0] if github_orgs else None,
        logo_url=_https(logos[0]) if logos else None,
        description=_label(rows, "itemDescription"),
        sector=_sector(_values(rows, "industryLabel")),
        tier=tier_for_import(is_vc=is_vc, founded_year=founded_year, is_public=is_public),
        stage="public" if is_public else None,
        people=_people(qid, rows),
    )


def parse(payload: dict[str, Any]) -> list[ImportedCompany]:
    """
    One :class:`ImportedCompany` per Wikidata item, in first-seen order.

    Skipped: an item with no English label (its "name" would be its QID),
    and one whose every website is somebody else's profile page, since a
    company is keyed on its own domain.
    """
    results = payload.get("results")
    bindings = results.get("bindings") if isinstance(results, dict) else None
    if not isinstance(bindings, list):
        return []
    by_item: dict[str, list[Row]] = defaultdict(list)
    for row in bindings:
        if isinstance(row, dict) and (qid := _qid(_value(row, "item"))):
            by_item[qid].append(row)

    companies: list[ImportedCompany] = []
    for qid, rows in by_item.items():
        company = _company(qid, rows)
        if company is not None:
            companies.append(company)
    return companies
