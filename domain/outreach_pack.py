"""
The daily outreach pack: which companies make today's pack, and whom to write to.

Pure rules over what the outreach ranking already produced (spec 2026-10-26,
section 3). The service works out the excluded companies (seen and untouched in
the last ``PACK_REPEAT_DAYS``, applied to in the last ``PACK_APPLIED_DAYS``) and
whether a person can be emailed; these rules only choose.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Literal

from domain.outreach_rank import RankedPerson, Suggestion

PACK_MAX_COMPANIES = 5
PACK_MAX_PEOPLE = 2
PACK_REPEAT_DAYS = 14
PACK_APPLIED_DAYS = 30

INBOX_KIND = "careers_inbox"


@dataclass(frozen=True)
class Recipient:
    contact_id: str
    name: str | None
    title: str | None
    kind: str
    channel: Literal["email", "linkedin"]
    format: str


@dataclass(frozen=True)
class PackCompany:
    company_id: str
    reasons: tuple[str, ...]
    recipients: tuple[Recipient, ...]


@dataclass(frozen=True)
class PersonOption:
    """A ranked person and whether an address can be shown (found, saved or guessed)."""

    person: RankedPerson
    has_email: bool


def _recipient(option: PersonOption) -> Recipient:
    person = option.person
    if option.has_email:
        return Recipient(
            contact_id=person.contact_id,
            name=person.name,
            title=person.title,
            kind=person.kind,
            channel="email",
            format="email",
        )
    return Recipient(
        contact_id=person.contact_id,
        name=person.name,
        title=person.title,
        kind=person.kind,
        channel="linkedin",
        format="linkedin_note",
    )


def pick_recipients(
    people: Sequence[PersonOption], inbox_contact_id: str | None
) -> tuple[Recipient, ...]:
    """
    Up to ``PACK_MAX_PEOPLE`` recipients, best first.

    Never two of the same kind unless only that kind is left. A hiring inbox fills a
    place only when there are fewer than two named people.
    """
    chosen: list[PersonOption] = []
    for option in people:
        if len(chosen) == PACK_MAX_PEOPLE:
            break
        if all(c.person.kind != option.person.kind for c in chosen):
            chosen.append(option)
    for option in people:
        if len(chosen) == PACK_MAX_PEOPLE:
            break
        if option not in chosen:
            chosen.append(option)
    chosen.sort(key=lambda option: people.index(option))
    recipients = [_recipient(option) for option in chosen]
    if inbox_contact_id is not None and len(recipients) < PACK_MAX_PEOPLE:
        recipients.append(
            Recipient(
                contact_id=inbox_contact_id,
                name=None,
                title=None,
                kind=INBOX_KIND,
                channel="email",
                format="email",
            )
        )
    return tuple(recipients)


def choose_pack(
    ranked: Sequence[Suggestion],
    people: Mapping[str, Sequence[PersonOption]],
    inboxes: Mapping[str, str],
    excluded: Set[str],
) -> list[PackCompany]:
    """
    Up to ``PACK_MAX_COMPANIES`` companies in rank order, each with someone to reach.

    Fewer is fine and nothing is padded: an empty pack is a valid day.
    """
    pack: list[PackCompany] = []
    for suggestion in ranked:
        if len(pack) == PACK_MAX_COMPANIES:
            break
        if suggestion.company_id in excluded:
            continue
        recipients = pick_recipients(
            people.get(suggestion.company_id, ()), inboxes.get(suggestion.company_id)
        )
        if not recipients:
            continue
        pack.append(
            PackCompany(
                company_id=suggestion.company_id,
                reasons=suggestion.reasons,
                recipients=recipients,
            )
        )
    return pack
