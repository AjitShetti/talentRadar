"""
domain/geo.py
~~~~~~~~~~~~~
India-focused location intelligence shared by ingestion, search, and the API.

TalentRadar's users are in India, so the pipeline keeps Indian postings only
and every search is scoped to India. This module is the single place that
knows what "in India" means for a free-text location string such as
``"Bengaluru, Karnataka"``, ``"Remote (India)"`` or ``"Hyderabad, Pakistan"``.
"""

from __future__ import annotations

import re

INDIA_COUNTRY_CODE = "IN"
INDIA_COUNTRY_NAME = "India"

#: Values the ``jobs.country`` column may legitimately hold for an Indian job.
INDIA_COUNTRY_VALUES: tuple[str, ...] = ("IN", "IND", "India", "india", "INDIA")

#: Canonical city -> aliases seen across Naukri / LinkedIn / Indeed / ATS boards.
INDIAN_CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "Bengaluru": ("bengaluru", "bangalore", "bangalore urban", "blr", "whitefield", "electronic city"),
    "Mumbai": ("mumbai", "bombay", "navi mumbai", "thane", "andheri", "powai"),
    "Delhi": ("delhi", "new delhi", "ncr", "delhi ncr"),
    "Gurugram": ("gurugram", "gurgaon"),
    "Noida": ("noida", "greater noida"),
    "Hyderabad": ("hyderabad", "secunderabad", "hitech city", "gachibowli"),
    "Pune": ("pune", "pimpri", "chinchwad", "hinjewadi"),
    "Chennai": ("chennai", "madras"),
    "Kolkata": ("kolkata", "calcutta"),
    "Ahmedabad": ("ahmedabad", "gandhinagar"),
    "Jaipur": ("jaipur",),
    "Kochi": ("kochi", "cochin", "ernakulam"),
    "Coimbatore": ("coimbatore",),
    "Indore": ("indore",),
    "Chandigarh": ("chandigarh", "mohali", "panchkula"),
    "Bhubaneswar": ("bhubaneswar",),
    "Nagpur": ("nagpur",),
    "Lucknow": ("lucknow",),
    "Thiruvananthapuram": ("thiruvananthapuram", "trivandrum"),
    "Mysuru": ("mysuru", "mysore"),
    "Vadodara": ("vadodara", "baroda"),
    "Surat": ("surat",),
    "Visakhapatnam": ("visakhapatnam", "vizag"),
    "Bhopal": ("bhopal",),
    "Nashik": ("nashik",),
    "Mangaluru": ("mangaluru", "mangalore"),
    "Goa": ("goa", "panaji", "panjim"),
    "Dehradun": ("dehradun",),
    "Guwahati": ("guwahati",),
    "Patna": ("patna",),
    "Raipur": ("raipur",),
    "Ludhiana": ("ludhiana",),
    "Kanpur": ("kanpur",),
    "Varanasi": ("varanasi",),
    "Rajkot": ("rajkot",),
    "Madurai": ("madurai",),
    "Amritsar": ("amritsar",),
    "Ranchi": ("ranchi",),
    "Vijayawada": ("vijayawada",),
}

#: Compact keyword list for SQL ``ILIKE`` matching against ``location_raw``.
#: Kept small on purpose — one OR clause per keyword ends up in every query.
INDIA_LOCATION_KEYWORDS: tuple[str, ...] = (
    "india", "bengaluru", "bangalore", "mumbai", "bombay", "delhi", "gurugram",
    "gurgaon", "noida", "hyderabad", "secunderabad", "pune", "chennai",
    "madras", "kolkata", "calcutta", "ahmedabad", "gandhinagar", "jaipur",
    "kochi", "cochin", "coimbatore", "indore", "chandigarh", "mohali",
    "bhubaneswar", "nagpur", "lucknow", "thiruvananthapuram", "trivandrum",
    "mysuru", "mysore", "vadodara", "surat", "visakhapatnam", "vizag",
    "bhopal", "nashik", "mangaluru", "mangalore", "guwahati", "dehradun",
)

#: Cities offered as location filter options in the UI (largest job markets first).
MAJOR_INDIAN_CITIES: tuple[str, ...] = (
    "Bengaluru", "Hyderabad", "Pune", "Chennai", "Mumbai", "Delhi",
    "Gurugram", "Noida", "Kolkata", "Ahmedabad", "Kochi", "Coimbatore",
    "Chandigarh", "Indore", "Jaipur", "Thiruvananthapuram",
)

_INDIAN_STATES: frozenset[str] = frozenset({
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya",
    "mizoram", "nagaland", "odisha", "orissa", "punjab", "rajasthan", "sikkim",
    "tamil nadu", "telangana", "tripura", "uttar pradesh", "uttarakhand",
    "west bengal", "jammu", "kashmir", "ladakh", "puducherry", "pondicherry",
    "andaman", "lakshadweep", "dadra", "daman", "diu",
})

_INDIA_WORDS: frozenset[str] = frozenset({"india", "bharat", "ind"})

#: Countries that share city names with India (Hyderabad/PK, Delhi/CA, ...) or
#: simply dominate global ATS feeds. Their presence overrules a city match.
_FOREIGN_MARKERS: frozenset[str] = frozenset({
    "united states", "usa", "canada", "united kingdom", "uk",
    "england", "scotland", "ireland", "germany", "france", "spain", "italy",
    "netherlands", "poland", "portugal", "sweden", "norway", "denmark",
    "switzerland", "australia", "new zealand", "singapore", "malaysia",
    "indonesia", "philippines", "vietnam", "thailand", "japan", "china",
    "hong kong", "korea", "taiwan", "pakistan", "bangladesh", "sri lanka",
    "nepal", "uae", "dubai", "abu dhabi", "qatar", "saudi", "israel",
    "south africa", "nigeria", "kenya", "egypt", "brazil", "mexico",
    "argentina", "chile", "colombia", "emea", "apac", "latam",
})

#: Location strings that carry no country signal at all.
_LOCATION_NOISE: frozenset[str] = frozenset({
    "", "remote", "remote work", "work from home", "wfh", "anywhere",
    "hybrid", "onsite", "on site", "multiple locations", "various",
    "not specified", "n a", "none", "flexible",
})

_ALIAS_TO_CITY: dict[str, str] = {
    alias: city
    for city, aliases in INDIAN_CITY_ALIASES.items()
    for alias in aliases
}

_SPLIT_RE = re.compile(r"[,/|;()\-\n\t]+")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]+")


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace."""
    return " ".join(_PUNCT_RE.sub(" ", text.lower()).split())


def _tokens(raw: str) -> list[str]:
    """Split a location string into normalised comma/slash separated parts."""
    return [_normalise(part) for part in _SPLIT_RE.split(raw) if part.strip()]


def resolve_city(raw: str | None) -> str | None:
    """Return the canonical Indian city named in ``raw``, or ``None``."""
    if not raw:
        return None
    for part in _tokens(raw):
        if part in _ALIAS_TO_CITY:
            return _ALIAS_TO_CITY[part]
    normalised = _normalise(raw)
    for alias, city in _ALIAS_TO_CITY.items():
        if re.search(rf"\b{re.escape(alias)}\b", normalised):
            return city
    return None


def city_search_terms(name: str | None) -> tuple[str, ...]:
    """
    Every spelling of a city, for matching free-text location columns.

    Postings store whatever the board printed ("Bangalore", "Gurgaon"), so a
    filter on the canonical name has to search the aliases too.
    """
    if not name:
        return ()
    canonical = resolve_city(name) or name.strip()
    aliases = INDIAN_CITY_ALIASES.get(canonical, ())
    return tuple(sorted({canonical.lower(), *aliases}))


def mentions_foreign_country(raw: str | None) -> bool:
    """True when the location explicitly names a country other than India."""
    if not raw:
        return False
    normalised = _normalise(raw)
    parts = set(_tokens(raw))
    for marker in _FOREIGN_MARKERS:
        if marker in parts or re.search(rf"\b{re.escape(marker)}\b", normalised):
            return True
    return False


def is_india(raw: str | None) -> bool:
    """
    True when the location string clearly refers to a place in India.

    An explicit "India" wins over a foreign marker (``"Remote - India/US"``
    is still an Indian posting); otherwise a foreign country name disqualifies
    a city match, so ``"Hyderabad, Pakistan"`` is not treated as Indian.
    """
    if not raw:
        return False
    parts = set(_tokens(raw))
    normalised = _normalise(raw)
    if parts & _INDIA_WORDS or re.search(r"\bindia\b", normalised):
        return True
    if mentions_foreign_country(raw):
        return False
    if parts & _INDIAN_STATES:
        return True
    if any(re.search(rf"\b{re.escape(state)}\b", normalised) for state in _INDIAN_STATES):
        return True
    return resolve_city(raw) is not None


def is_location_unknown(raw: str | None) -> bool:
    """True when the string carries no usable geographic signal ('Remote', '')."""
    if not raw:
        return True
    return _normalise(raw) in _LOCATION_NOISE


def is_indian_job(
    location_raw: str | None,
    *,
    is_remote: bool = False,
    context: str | None = None,
) -> bool:
    """
    Decide whether a posting belongs on an India-only job board.

    Kept when the location resolves to India. When the location field is empty
    or says only "Remote" — common on ATS feeds — ``context`` (the job
    description) is searched for a country signal before falling back to
    keeping remote postings, since Indian boards routinely list remote roles
    with no region at all.
    """
    if is_india(location_raw):
        return True
    if mentions_foreign_country(location_raw):
        return False
    if not is_location_unknown(location_raw):
        # A readable but unrecognised location (some foreign town) is not India.
        return False
    if context:
        if is_india(context):
            return True
        if mentions_foreign_country(context):
            return False
    return bool(is_remote)


def resolve_location(
    raw: str | None, *, is_remote: bool = False
) -> tuple[str | None, str | None]:
    """
    Map a free-text location to ``(country, city)`` for the ``jobs`` columns.

    Returns ``("IN", "Bengaluru")``-style tuples for Indian postings and
    ``(None, None)`` when the location is foreign or unreadable.
    """
    city = resolve_city(raw)
    if is_india(raw) or (is_remote and is_location_unknown(raw)):
        return INDIA_COUNTRY_CODE, city
    if mentions_foreign_country(raw):
        return None, None
    return (INDIA_COUNTRY_CODE, city) if city else (None, None)
