"""
domain/company_taxonomy.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
The vocabularies the company map filters on, and the rules that map what a
source says onto them.

* **Stage** — how far a company has raised. Moves with dated funding news.
* **Sector** — what it does. Derived from the curated ``industry`` string, a
  Wikidata industry *label*, or failing both the summary, by ordered keyword
  rules. Labels rather than item ids, so no id table has to be kept right.
* **Size** — a headcount bucket.
* **Type** is the existing ``companies.tier`` (``services/companies.py``),
  plus ``vc``.

Everything here is pure; a wrong mapping is a one-line table change.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

STAGES: dict[str, str] = {
    "bootstrapped": "Bootstrapped",
    "pre_seed": "Pre-seed",
    "seed": "Seed",
    "series_a": "Series A",
    "series_b": "Series B",
    "series_c_plus": "Series C+",
    "public": "Public",
    "acquired": "Acquired",
}

SECTORS: dict[str, str] = {
    "ai": "AI",
    "saas": "SaaS",
    "fintech": "Fintech",
    "consumer": "Consumer",
    "d2c": "D2C",
    "deeptech": "Deeptech",
    "edtech": "Edtech",
    "healthtech": "Healthtech",
    "logistics": "Logistics & mobility",
    "gaming": "Gaming",
    "devtools": "Developer tools",
    "it_services": "IT services",
    "other": "Other",
}

SIZES: dict[str, str] = {
    "1-50": "1–50",
    "51-200": "51–200",
    "201-1000": "201–1,000",
    "1000+": "1,000+",
}

#: Types whose companies are big enough for the drawer to lead with
#: headcount, leadership and LinkedIn searches rather than founders.
_BIG_TIERS = frozenset({"big_tech", "gcc", "unicorn", "services"})

#: A company founded this year or later, not listed, reads as a startup.
_STARTUP_SINCE = 2008


def _rule(words: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z])(?:{words})", re.IGNORECASE)


# Ordered: the first matching rule wins. Domain-specific sectors come before
# the generic ones, so "Health Tech & Medical AI" is healthtech, not AI, and
# "Fintech & Tax SaaS" is fintech, not SaaS. An explicit "SaaS" beats the
# hardware and consumer words ("Automotive Retail SaaS" sells software).
_SECTOR_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "fintech",
        _rule(
            r"fintech|payment|banking|bank\b|lending|neobank|insurtech|insurance|reinsurance|"
            r"wealth|investing|investment|stockbrok|credit|bnpl|financial|crypto|blockchain|"
            r"capital markets|spend management|expense|accounting|corporate cards|savings"
        ),
    ),
    ("it_services", _rule(r"services|consult|agency|studio|outsourc|product engineering")),
    ("edtech", _rule(r"edtech|education|learning|study abroad")),
    ("healthtech", _rule(r"health|medical|biotech|diagnostic|wellness|fitness|pharma|cardiac")),
    ("gaming", _rule(r"gaming|games?\b")),
    ("d2c", _rule(r"d2c|direct[- ]to[- ]consumer")),
    ("saas", _rule(r"saas\b")),
    ("logistics", _rule(r"logistics|shipping|supply[- ]chain|freight|fleet|mobility")),
    (
        "deeptech",
        _rule(
            r"semiconductor|aerospace|space tech|battery|robotic|hardware|automotive|industrial|"
            r"\beda\b|energy|ev charging|electric vehicle|telecom equipment|telecom infrastructure|"
            r"drone|manufactur|materials|electronics|aviation|agritech|deeptech"
        ),
    ),
    ("ai", _rule(r"ai\b|artificial intelligence|machine learning|generative|llm|computer vision")),
    (
        "devtools",
        _rule(
            r"developer|devops|observability|database|open source|api\b|apis\b|incident|"
            r"artifact|data streaming"
        ),
    ),
    (
        "consumer",
        _rule(
            r"e-?commerce|consumer|quick commerce|grocery|food|travel|social|media|streaming|"
            r"entertainment|rental|fashion|beauty|interiors|furniture|super app|proptech|jobs|"
            r"professional network|internet|audio|marketplace|commerce"
        ),
    ),
    (
        "saas",
        _rule(
            r"software|platform|cloud|crm|enterprise|resource planning|security|cyber|data|"
            r"analytics|marketing|adtech|\bhr\b|hr tech|identity|networking|telecom|"
            r"communications|messaging|intelligence|talent|storage|infrastructure|automation|"
            r"collaboration|design|tech"
        ),
    ),
)

_ROUND_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pre_seed", _rule(r"pre[- ]?seed")),
    ("seed", _rule(r"pre[- ]series[- ]a|seed|angel")),
    ("series_a", _rule(r"series[- ]a\b")),
    ("series_b", _rule(r"series[- ]b\b")),
    ("series_c_plus", _rule(r"series[- ][c-k]\b")),
    ("public", _rule(r"ipo\b|listed|public")),
    ("acquired", _rule(r"acqui")),
    ("bootstrapped", _rule(r"bootstrap")),
)


def stage_from_round(text: str | None) -> str | None:
    """The stage a funding round or status string implies; ``None`` for debt etc."""
    if not text:
        return None
    for stage, pattern in _ROUND_RULES:
        if pattern.search(text):
            return stage
    return None


def sector_from_text(text: str | None) -> str | None:
    """The sector an industry or summary string names, or ``None``."""
    if not text:
        return None
    for sector, pattern in _SECTOR_RULES:
        if pattern.search(text):
            return sector
    return None


def _range_floor(employee_count_range: str | None) -> int | None:
    if not employee_count_range:
        return None
    match = re.match(r"\s*(\d[\d,]*)", employee_count_range)
    return int(match.group(1).replace(",", "")) if match else None


def size_bucket(employee_count: int | None, employee_count_range: str | None) -> str | None:
    """A size bucket from a counted headcount, else the lower bound of a range."""
    size = employee_count if employee_count is not None else _range_floor(employee_count_range)
    if size is None:
        return None
    if size <= 50:
        return "1-50"
    if size <= 200:
        return "51-200"
    if size <= 1000:
        return "201-1000"
    return "1000+"


def tier_for_import(*, is_vc: bool, founded_year: int | None, is_public: bool) -> str | None:
    """
    The type for a company that did not come from the curated catalogue.

    Only the certain cases: a VC, or a young unlisted company. Big tech versus
    IT services cannot be told apart from an import, so it is left unset.
    """
    if is_vc:
        return "vc"
    if is_public or founded_year is None:
        return None
    return "startup" if founded_year >= _STARTUP_SINCE else None


def is_bigger_company(
    tier: str | None, employee_count: int | None, employee_count_range: str | None
) -> bool:
    if tier in _BIG_TIERS:
        return True
    if employee_count is not None:
        return employee_count >= 200
    floor = _range_floor(employee_count_range)
    return floor is not None and floor >= 201


# ---------------------------------------------------------------------------
# Tech employers
# ---------------------------------------------------------------------------
# The map lists tech employers only (decision 2026-10-15): startups,
# scale-ups, unicorns, Big Tech, GCCs and IT services. A source says so in a
# few words ("Indian enterprise software company"), so the rule reads words:
#
# * a STRONG tech word in the description or industry labels says yes, even
#   beside a non-tech word ("pharmaceutical ... online platform");
# * otherwise a NON-TECH word anywhere (the name included) says no
#   ("university", "hospital", "bank", "airline"...);
# * otherwise a WEAK signal says yes: a software-ish sector, a tech word in
#   the name, "startup".
#
# No text at all is not tech: the map never lists a guess.


def _words(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9])(?:{pattern})(?![a-z0-9])", re.IGNORECASE)


#: Tech words that name what a company builds. Specific enough that a
#: company's own homepage saying one is evidence.
_SPECIFIC_TECH_WORDS = (
    r"software|saas|paas|iaas|"
    r"fintech|edtech|healthtech|insurtech|regtech|adtech|"
    r"proptech|martech|agritech|legaltech|hrtech|deep-?tech|nanotech\w*|"
    r"information technology|it services|it consulting|"
    r"ai|a\.i\.|ai-powered|artificial intelligence|machine learning|generative ai|llm|"
    r"computer vision|analytics|cloud|cyber\w*|devops|api|apis|open[- ]source|"
    r"semiconductors?|embedded|firmware|iot|robotics?|drones?|blockchain|crypto|"
    r"electronics design|eda|cad|cae|"
    r"micro-?mobility|mobility (?:platform|startup)|ev charging|"
    r"employer of record|payments? (?:platform|gateway|infrastructure|api)|ride-?hailing|"
    r"developer tools?|devtools|foundation models?|coding|"
    r"computing|automation|"
    r"mobile games?|video games?|game (?:studio|developer|publisher)|"
    # The startup ecosystem the map has a type for (decision 2026-10-15).
    r"venture capital|venture fund|vc fund|vc firm|accelerators?|incubators?"
)

#: Tech words every consumer business's homepage also uses ("order online",
#: "our app", "digital gold"). They count when a directory or encyclopedia
#: describes the company with them, never from the homepage alone.
_GENERIC_TECH_WORDS = (
    r"app|apps|platform|platforms|internet|online|digital|"
    r"e-?commerce|quick[- ]commerce|"
    r"tech|technology|technologies|data|computer|"
    r"educational company|education company|learning company|"
    r"web|website|marketplace"
)

_STRONG_TECH = _words(f"{_SPECIFIC_TECH_WORDS}|{_GENERIC_TECH_WORDS}")
_SITE_TECH = _words(_SPECIFIC_TECH_WORDS)

#: Words that name a kind of organisation the map does not list. "Technical"
#: is deliberately not a tech word, and an "institute of technology" is a school.
_NON_TECH = _words(
    r"universit(?:y|ies)|college|colleges|school|schools|institute|institutes|academy|"
    r"hospitals?|clinics?|healthcare network|nursing|"
    r"banks?|banking|"
    r"real estate|properties|realty|housing|"
    r"brewer(?:y|ies)|distiller(?:y|ies)|liquor|alcohol\w*|"
    r"restaurants?|cafes?|food chain|fast casual|bakery|"
    r"airlines?|airways|"
    r"pharmaceuticals?|pharma|"
    r"hotels?|resorts?|textiles?|garments?|apparel|cement|steel|"
    r"mining|fertili[sz]ers?|sugar|tea|coffee|"
    r"temple|trust|church|ngo|foundation"
)

#: Sector buckets (``SECTORS``) that read as software or hardware product
#: companies. Weak: ``sector_from_text`` also reads "health" in "healthcare
#: network" and "bank" in "banking group", so a sector never outvotes a
#: non-tech word.
_TECH_SECTORS = frozenset({"ai", "saas", "devtools", "fintech", "edtech", "deeptech", "gaming"})

#: Words in a *name* that say technology.
_TECH_NAME = _words(
    r"technolog(?:y|ies)|software|softech|infotech|tech|ai|labs|cloud|"
    r"digital|data|robotics|semiconductors?|networks"
)

_WEAK_TECH = _words(r"startup|unicorn|telecom\w*")


def is_tech(
    *,
    name: str,
    description: str | None,
    industry_labels: Sequence[str] = (),
    sector: str | None,
    site_text: str | None = None,
) -> bool:
    """
    Whether this employer is a tech company, from what its sources say.

    ``description`` is what a curator, encyclopedia, directory or news item
    says (Wikidata's one-liner, YC's), ``industry_labels`` Wikidata industry
    labels, ``sector`` a ``SECTORS`` key. ``site_text`` is the company's own
    homepage line, and is weak (review 2026-10-15): it says yes only with a
    specific tech word, and only when neither the sources nor the name name
    a non-tech kind of organisation. Pure and deliberately conservative:
    silence is ``False``.
    """
    said = " ; ".join([description or "", *industry_labels])
    if _STRONG_TECH.search(said):
        return True
    if non_tech_named(name=name, text=said):
        return False
    if site_text and _SITE_TECH.search(site_text) and not _NON_TECH.search(site_text):
        return True
    if sector in _TECH_SECTORS:
        return True
    return bool(_TECH_NAME.search(name) or _WEAK_TECH.search(said))


def non_tech_named(*, name: str, text: str | None) -> bool:
    """Whether the name or a source's text names a kind of organisation the map does not list."""
    return bool(_NON_TECH.search(text or "") or _NON_TECH.search(name))
