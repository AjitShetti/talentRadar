"""
domain/email_patterns.py
~~~~~~~~~~~~~~~~~~~~~~~~
A company's email address format, learned from addresses it published, and the
address that format gives a named person.

Pure: no I/O. The service layer decides which addresses are "found" (published at a
cited URL on the company's own domain); this module only reads names and local parts.

Rules this module keeps:

* **A format is learned, never assumed.** ``learn`` needs at least one example that
  only one format explains, and every explained example must agree. A one-word name,
  an inbox (``careers@``) or two disagreeing people teach nothing.
* **A guess is only for a plain name.** ``guess`` refuses one-word names, initials,
  digits and anything that is not Latin letters once accents are folded, rather than
  produce an address nobody would recognise.
* **A guess is never a fact.** Callers show it as "likely, unverified" and never
  store it (spec 2026-10-26-daily-outreach-pack-design.md, section 2).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from typing import Literal, get_args

from domain.company_identity import normalize_domain

Pattern = Literal[
    "first",
    "last",
    "first.last",
    "firstlast",
    "first_last",
    "f.last",
    "flast",
    "first.l",
    "firstl",
    "last.first",
    "lastf",
]

LocalPartKind = Literal["hiring", "drop", "personal"]

_BUILDERS: dict[Pattern, Callable[[str, str], str]] = {
    "first": lambda f, s: f,
    "last": lambda f, s: s,
    "first.last": lambda f, s: f"{f}.{s}",
    "firstlast": lambda f, s: f"{f}{s}",
    "first_last": lambda f, s: f"{f}_{s}",
    "f.last": lambda f, s: f"{f[0]}.{s}",
    "flast": lambda f, s: f"{f[0]}{s}",
    "first.l": lambda f, s: f"{f}.{s[0]}",
    "firstl": lambda f, s: f"{f}{s[0]}",
    "last.first": lambda f, s: f"{s}.{f}",
    "lastf": lambda f, s: f"{s}{f[0]}",
}
PATTERNS: tuple[Pattern, ...] = get_args(Pattern)

#: Local parts that reach a hiring team. ``recruit`` also covers recruiting@, recruitment@.
_HIRING = frozenset({"careers", "career", "jobs", "job", "hiring", "talent", "hr", "people"})
_HIRING_PREFIXES = ("recruit",)
#: Local parts that are a role, not a person, and no use to a job seeker.
_DROP = frozenset(
    [
        "info",
        "hello",
        "hi",
        "contact",
        "contactus",
        "support",
        "help",
        "sales",
        "press",
        "media",
        "pr",
        "privacy",
        "legal",
        "noreply",
        "no-reply",
        "donotreply",
        "do-not-reply",
        "admin",
        "team",
        "office",
        "marketing",
        "billing",
        "accounts",
        "security",
        "abuse",
        "webmaster",
        "postmaster",
        "partners",
        "partnerships",
        "investors",
        "ir",
        "feedback",
        "enquiries",
        "inquiries",
        "business",
        "mail",
    ]
)

_LETTERS = re.compile(r"[a-z]+")
_SEPARATORS = re.compile(r"[-'’]")


def local_part_kind(local: str) -> LocalPartKind:
    """Whether a local part reaches hiring, is a role to drop, or names a person."""
    base = local.strip().lower().split("+", 1)[0]
    if base in _HIRING or base.startswith(_HIRING_PREFIXES):
        return "hiring"
    if base in _DROP:
        return "drop"
    return "personal"


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def name_parts(name: str) -> tuple[str, str] | None:
    """``(first, last)`` as lowercase ASCII letters, or ``None`` for a name to leave alone.

    Middle names are ignored. Hyphens and apostrophes are dropped (``Mar-Tin`` is
    ``martin``). One-word names, initials, digits and non-Latin scripts are ``None``.
    """
    tokens = [_SEPARATORS.sub("", token).rstrip(".") for token in _fold(name).split()]
    tokens = [token for token in tokens if token]
    if len(tokens) < 2:
        return None
    if not all(_LETTERS.fullmatch(token) for token in tokens):
        return None
    first, last = tokens[0], tokens[-1]
    if len(first) < 2 or len(last) < 2:
        return None
    return first, last


def _explaining(name: str, address: str) -> set[Pattern]:
    local, _, _ = address.strip().lower().partition("@")
    if not local or local_part_kind(local) != "personal":
        return set()
    parts = name_parts(name)
    if parts is None:
        return set()
    first, last = parts
    return {pattern for pattern in PATTERNS if _BUILDERS[pattern](first, last) == local}


def learn(examples: Iterable[tuple[str, str]]) -> Pattern | None:
    """The one format every explainable ``(full name, address)`` example agrees on.

    Examples no format explains (``rs1987@``) are skipped. ``None`` unless the
    agreeing set is exactly one format and at least one example was unambiguous.
    """
    agreed: set[Pattern] | None = None
    unambiguous = False
    for name, address in examples:
        fits = _explaining(name, address)
        if not fits:
            continue
        unambiguous = unambiguous or len(fits) == 1
        agreed = fits if agreed is None else agreed & fits
    if agreed is None or len(agreed) != 1 or not unambiguous:
        return None
    return next(iter(agreed))


def email_domain_ok(address: str, company_domain: str | None) -> bool:
    """Whether ``address`` is on the company's own registrable domain (or a subdomain).

    A personal Gmail on a founder's GitHub profile is their private address, and an
    agency's inbox on a careers page is someone else's: neither is the company's.
    """
    company = normalize_domain(company_domain)
    _, _, host = address.strip().lower().rpartition("@")
    if not company or not host:
        return False
    return host == company or host.endswith(f".{company}")


def guess(pattern: Pattern, name: str, domain: str) -> str | None:
    """The address ``pattern`` gives ``name`` at ``domain``, or ``None`` for an odd name."""
    parts = name_parts(name)
    host = domain.strip().lower()
    if parts is None or not host:
        return None
    return f"{_BUILDERS[pattern](*parts)}@{host}"
