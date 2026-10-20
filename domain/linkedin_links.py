"""
domain/linkedin_links.py
~~~~~~~~~~~~~~~~~~~~~~~~
LinkedIn deep links, built at read time.

The server never requests any of these URLs. They open in the user's own
browser, inside their own LinkedIn session, and are never stored.

* **Company page known**: the company's people tab, filtered by a preset
  (``…/company/<slug>/people/?keywords=founder``).
* **Otherwise**: LinkedIn's people search for ``<company> <preset>``.
* **A named person without a profile link**: a people search for
  ``"<name>" <company>``.

A stored ``linkedin_url`` is never echoed into a link: only the company slug
is read out of it, against a strict pattern, and the URL is rebuilt. Anything
else (a profile URL, a lookalike host, a ``javascript:`` string) falls back to
search.
"""

from __future__ import annotations

import re
from urllib.parse import quote

#: (label, keywords) shown in the drawer's "Find on LinkedIn" row, in order.
PRESETS: tuple[tuple[str, str], ...] = (
    ("Founders", "founder"),
    ("Engineering leaders", "engineering manager"),
    ("Recruiters", "recruiter"),
)

_ORIGIN = "https://www.linkedin.com"
_PEOPLE_SEARCH = f"{_ORIGIN}/search/results/people/?keywords="
_COMPANY_PAGE = re.compile(
    r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/(?P<slug>[A-Za-z0-9\-_.%]+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_MAX_TERM = 80


def _flat(text: str | None) -> str:
    """One line, single-spaced, bounded."""
    return " ".join((text or "").split())[:_MAX_TERM].strip()


def _company_slug(linkedin_url: str | None) -> str | None:
    match = _COMPANY_PAGE.match((linkedin_url or "").strip())
    return match.group("slug") if match else None


def _encode(keywords: str) -> str:
    return quote(keywords, safe="")


def _link(company: str, slug: str | None, keywords: str) -> str:
    if slug:
        return f"{_ORIGIN}/company/{slug}/people/?keywords={_encode(keywords)}"
    return _PEOPLE_SEARCH + _encode(f"{company} {keywords}".strip())


def company_people_links(
    company: str, linkedin_url: str | None, target_role: str | None
) -> list[dict[str, str]]:
    """
    ``[{label, url}]`` for the drawer: the presets, then the user's target role.
    """
    name = _flat(company)
    slug = _company_slug(linkedin_url)
    presets = list(PRESETS)
    role = _flat(target_role)
    if role:
        presets.append((role, role))
    return [{"label": label, "url": _link(name, slug, keywords)} for label, keywords in presets]


def person_search_link(name: str, company: str) -> str:
    """A people search for one named person at a company, the name quoted."""
    person = _flat(name).replace('"', "")
    return _PEOPLE_SEARCH + _encode(f'"{person}" {_flat(company)}'.strip())
