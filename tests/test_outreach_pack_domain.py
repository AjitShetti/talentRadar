"""The daily pack's selection rules: which companies, and whom at each."""

from __future__ import annotations

from domain.outreach_pack import (
    PACK_MAX_COMPANIES,
    PackCompany,
    PersonOption,
    Recipient,
    choose_pack,
    pick_recipients,
)
from domain.outreach_rank import RankedPerson, Suggestion


def _suggestion(company_id: str, score: int = 50) -> Suggestion:
    return Suggestion(
        company_id=company_id,
        name=f"Company {company_id}",
        summary=None,
        area=None,
        stage=None,
        sector=None,
        score=score,
        reasons=(f"Reason for {company_id}",),
        last_funding_at=None,
    )


def _option(contact_id: str, kind: str = "founder", *, has_email: bool = False) -> PersonOption:
    person = RankedPerson(
        contact_id=contact_id,
        name=f"Person {contact_id}",
        title=f"Title {contact_id}",
        kind=kind,
        verified=False,
        user_saved=False,
        source_host=None,
        score=10,
        reason="Founder at a small company",
    )
    return PersonOption(person=person, has_email=has_email)


def _people(*company_ids: str) -> dict[str, list[PersonOption]]:
    return {cid: [_option(f"{cid}-p1")] for cid in company_ids}


def test_takes_at_most_five_in_rank_order() -> None:
    ids = [f"c{i}" for i in range(7)]
    pack = choose_pack([_suggestion(i) for i in ids], _people(*ids), {}, set())
    assert PACK_MAX_COMPANIES == 5
    assert [c.company_id for c in pack] == ids[:5]
    assert pack[0].reasons == ("Reason for c0",)


def test_fewer_than_three_is_not_padded() -> None:
    pack = choose_pack([_suggestion("a"), _suggestion("b")], _people("a", "b"), {}, set())
    assert [c.company_id for c in pack] == ["a", "b"]


def test_empty_is_valid() -> None:
    assert choose_pack([], {}, {}, set()) == []


def test_excluded_companies_skipped() -> None:
    ids = ["a", "b", "c"]
    pack = choose_pack([_suggestion(i) for i in ids], _people(*ids), {}, {"b"})
    assert [c.company_id for c in pack] == ["a", "c"]


def test_company_without_person_or_inbox_skipped() -> None:
    ranked = [_suggestion("a"), _suggestion("b"), _suggestion("c")]
    pack = choose_pack(ranked, {"a": [_option("a1")], "b": []}, {"c": "c-inbox"}, set())
    assert [c.company_id for c in pack] == ["a", "c"]
    (inbox,) = pack[1].recipients
    assert inbox == Recipient(
        contact_id="c-inbox",
        name=None,
        title=None,
        kind="careers_inbox",
        channel="email",
        format="email",
    )


def test_two_people_prefer_different_kinds() -> None:
    options = [_option("f1", "founder"), _option("f2", "founder"), _option("e1", "engineer")]
    picked = pick_recipients(options, None)
    assert [r.contact_id for r in picked] == ["f1", "e1"]


def test_same_kind_allowed_when_only_kind() -> None:
    options = [_option("f1", "founder"), _option("f2", "founder"), _option("f3", "founder")]
    picked = pick_recipients(options, None)
    assert [r.contact_id for r in picked] == ["f1", "f2"]


def test_inbox_only_fills_when_fewer_than_two_named() -> None:
    two = [_option("f1", "founder"), _option("e1", "engineer")]
    assert [r.contact_id for r in pick_recipients(two, "inbox")] == ["f1", "e1"]
    one = [_option("f1", "founder")]
    assert [r.contact_id for r in pick_recipients(one, "inbox")] == ["f1", "inbox"]
    assert [r.contact_id for r in pick_recipients([], "inbox")] == ["inbox"]
    assert pick_recipients([], None) == ()


def test_channel_email_when_has_email_else_linkedin_note() -> None:
    options = [_option("f1", "founder", has_email=True), _option("e1", "engineer")]
    first, second = pick_recipients(options, None)
    assert (first.channel, first.format) == ("email", "email")
    assert (second.channel, second.format) == ("linkedin", "linkedin_note")
    assert (first.name, first.title, first.kind) == ("Person f1", "Title f1", "founder")


def test_pack_company_carries_recipients() -> None:
    (company,) = choose_pack([_suggestion("a")], _people("a"), {"a": "a-inbox"}, set())
    assert isinstance(company, PackCompany)
    assert [r.contact_id for r in company.recipients] == ["a-p1", "a-inbox"]
