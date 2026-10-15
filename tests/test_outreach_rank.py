"""
The ranking rules behind Outreach suggestions and the people inside a company.

Everything under test is pure: a table of signals goes in, a score and its
reasons come out. The weights are the spec's section 5.1 / 5.2 tables, so the
numbers below are asserted literally on purpose: a change to a weight should
fail a test and be a deliberate edit.

A company needs 20 points to be suggested, so a weak signal on its own never
shows up in a result. Those cases ride on a fixed hiring signal (35 points)
and assert on the difference.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from domain.company_taxonomy import SECTORS
from domain.outreach_rank import (
    KIND_WEIGHTS,
    SUGGEST_THRESHOLD,
    CompanySignals,
    PersonSignals,
    SizeClass,
    Suggestion,
    UserSignals,
    rank_companies,
    rank_people,
    score_company,
    sectors_for_profile,
    size_class,
)

NOW = datetime(2026, 10, 15, tzinfo=UTC)
NO_USER = UserSignals(skills=(), sectors=frozenset())

HIRING = 35  # one matching role, see test_hiring_for_target_role_scores_35_or_40


def sig(**kw: object) -> CompanySignals:
    base: dict[str, object] = {
        "company_id": "c1",
        "name": "Acme Labs",
        "summary": None,
        "area": None,
        "stage": None,
        "sector": None,
        "last_funding_at": None,
        "last_round": None,
        "open_roles": 0,
        "matching_roles": 0,
        "target_role": None,
        "stack": (),
        "good_first_issues": 0,
        "has_named_person": False,
    }
    base.update(kw)
    return CompanySignals(**base)  # type: ignore[arg-type]


def hiring(**kw: object) -> CompanySignals:
    """A company with the fixed 35-point hiring signal plus whatever is under test."""
    return sig(**{"matching_roles": 1, "target_role": "Backend Engineer", **kw})


def person(**kw: object) -> PersonSignals:
    base: dict[str, object] = {
        "contact_id": "p1",
        "name": "Asha Rao",
        "title": None,
        "kind": "other",
        "verified": False,
        "user_saved": False,
        "has_direct_link": False,
        "has_public_email": False,
        "source_host": None,
    }
    base.update(kw)
    return PersonSignals(**base)  # type: ignore[arg-type]


def scored(c: CompanySignals, u: UserSignals = NO_USER) -> Suggestion:
    result = score_company(c, u, NOW)
    assert result is not None
    return result


# --- companies ----------------------------------------------------------------


def test_threshold_is_20() -> None:
    assert SUGGEST_THRESHOLD == 20


def test_hiring_for_target_role_scores_35_or_40() -> None:
    one = scored(sig(matching_roles=1, target_role="Backend Engineer"))
    assert one.score == 35
    assert one.reasons[0] == "Hiring Backend Engineer · 1 open"

    two = scored(sig(matching_roles=2, target_role="Backend Engineer"))
    assert two.score == 35
    assert two.reasons[0] == "Hiring Backend Engineer · 2 open"

    three = scored(sig(matching_roles=3, target_role="Backend Engineer"))
    assert three.score == 40
    assert three.reasons[0] == "Hiring Backend Engineer · 3 open"


def test_hiring_without_a_known_target_role_still_reads() -> None:
    result = scored(sig(matching_roles=2, target_role=None))
    assert result.score == 35
    assert result.reasons[0] == "Hiring for your target role · 2 open"


def test_matching_roles_replace_the_open_roles_signal() -> None:
    """A company hiring the role is not also credited 8 for 'any open role'."""
    result = scored(sig(matching_roles=1, open_roles=9, target_role="Backend Engineer"))
    assert result.score == 35
    assert result.reasons == ("Hiring Backend Engineer · 1 open",)


def test_open_roles_without_a_match_score_8() -> None:
    c = sig(open_roles=6, has_named_person=True)
    assert score_company(c, NO_USER, NOW) is None  # 8 + 5 = 13 < 20


def test_open_roles_reason_and_points() -> None:
    result = scored(sig(open_roles=6, last_funding_at=NOW - timedelta(days=10), last_round="Seed"))
    # 8 (open roles) + 30 (funding) = 38; the funding line is the stronger one.
    assert result.score == 38
    assert result.reasons == ("Raised Seed · 10 days ago", "6 open roles")

    one = scored(sig(open_roles=1, last_funding_at=NOW - timedelta(days=10)))
    assert "1 open role" in one.reasons


def test_a_named_person_adds_5_and_no_reason_line() -> None:
    plain = scored(hiring())
    named = scored(hiring(has_named_person=True))
    assert named.score - plain.score == 5
    assert named.reasons == plain.reasons


@pytest.mark.parametrize(
    ("days", "points"),
    [(0, 30), (30, 30), (31, 20), (90, 20), (91, 8), (180, 8), (181, 0)],
)
def test_funding_bands(days: int, points: int) -> None:
    result = scored(hiring(last_funding_at=NOW - timedelta(days=days), last_round="Series A"))
    assert result.score - HIRING == points
    raised = [r for r in result.reasons if r.startswith("Raised")]
    # Past 180 days the funding is not a signal and says nothing.
    assert bool(raised) is (points > 0)


def test_funding_reason_wording() -> None:
    def reason(days: int, rnd: str | None) -> str:
        r = scored(hiring(last_funding_at=NOW - timedelta(days=days), last_round=rnd))
        return next(x for x in r.reasons if x.startswith("Raised"))

    assert reason(12, "Series A") == "Raised Series A · 12 days ago"
    assert reason(40, None) == "Raised funding · 40 days ago"
    assert reason(1, "Seed") == "Raised Seed · 1 day ago"
    assert reason(0, "Seed") == "Raised Seed · today"


def test_funding_age_counts_whole_days_and_tolerates_naive_and_future_dates() -> None:
    # 30 days and 23 hours is still day 30.
    almost = NOW - timedelta(days=30, hours=23)
    assert scored(hiring(last_funding_at=almost)).score - HIRING == 30
    # A naive timestamp is read as UTC.
    naive = datetime(2026, 9, 15)  # 30 days before NOW
    assert scored(hiring(last_funding_at=naive)).score - HIRING == 30
    # A date after `now` (clock skew, a dated-ahead news item) is "today".
    future = NOW + timedelta(days=2)
    result = scored(hiring(last_funding_at=future, last_round="Seed"))
    assert result.score - HIRING == 30
    assert "Raised Seed · today" in result.reasons


def test_skills_overlap_caps_at_20() -> None:
    user = UserSignals(
        skills=("kafka", "PYTHON", "Go", "react", "SQL", "rust"),
        sectors=frozenset(),
    )
    c = sig(stack=("Python", "Kafka", "Go", "React", "Sql", "Ruby"))
    result = scored(c, user)
    assert result.score == 20  # 5 overlaps x 5 = 25, capped at 20
    assert result.reasons == ("Uses Python, Kafka",)  # the company's stack order


def test_skills_overlap_is_5_per_skill() -> None:
    user = UserSignals(skills=("python", "kafka"), sectors=frozenset())
    c = hiring(stack=("Kafka", "Python", "Java"))
    result = scored(c, user)
    assert result.score - HIRING == 10
    assert "Uses Kafka, Python" in result.reasons


def test_skills_overlap_counts_a_repeated_skill_once() -> None:
    user = UserSignals(skills=("python", "Python", "PYTHON"), sectors=frozenset())
    c = hiring(stack=("Python", "python"))
    result = scored(c, user)
    assert result.score - HIRING == 5
    assert "Uses Python" in result.reasons


def test_no_overlap_no_skills_line() -> None:
    user = UserSignals(skills=("python",), sectors=frozenset())
    result = scored(hiring(stack=("Java",)), user)
    assert result.score == HIRING
    assert not any(r.startswith("Uses") for r in result.reasons)


def test_sector_and_open_source_points() -> None:
    fintech_user = UserSignals(skills=(), sectors=frozenset({"fintech"}))

    sector = scored(hiring(sector="fintech"), fintech_user)
    assert sector.score - HIRING == 10
    assert SECTORS["fintech"] in sector.reasons

    other_sector = scored(hiring(sector="gaming"), fintech_user)
    assert other_sector.score == HIRING

    four = scored(hiring(good_first_issues=4))
    assert four.score - HIRING == 12
    assert "4 good first issues" in four.reasons

    one = scored(hiring(good_first_issues=1))
    assert one.score - HIRING == 12
    assert "1 good first issue" in one.reasons


def test_below_threshold_is_not_suggested_and_at_threshold_is() -> None:
    assert score_company(sig(good_first_issues=1, has_named_person=True), NO_USER, NOW) is None
    # 12 + 8 = 20: exactly the threshold.
    at = score_company(
        sig(good_first_issues=1, open_roles=2),
        NO_USER,
        NOW,
    )
    assert at is not None
    assert at.score == 20
    assert score_company(sig(), NO_USER, NOW) is None


def test_reasons_are_strongest_first_and_at_most_three() -> None:
    user = UserSignals(skills=("python", "kafka"), sectors=frozenset({"fintech"}))
    c = sig(
        matching_roles=3,
        target_role="Backend Engineer",
        last_funding_at=NOW - timedelta(days=12),
        last_round="Series A",
        stack=("Python", "Kafka"),
        sector="fintech",
        good_first_issues=4,
        has_named_person=True,
    )
    result = scored(c, user)
    # 40 hiring + 30 funding + 12 oss + 10 skills + 10 sector + 5 person
    assert result.score == 107
    assert result.reasons == (
        "Hiring Backend Engineer · 3 open",
        "Raised Series A · 12 days ago",
        "4 good first issues",
    )


def test_a_stronger_later_signal_outranks_an_earlier_weak_one() -> None:
    """Strength, not the order signals are computed in, decides the reason order."""
    user = UserSignals(skills=(), sectors=frozenset({"fintech"}))
    c = sig(
        last_funding_at=NOW - timedelta(days=100),  # 8
        last_round="Seed",
        sector="fintech",  # 10
        good_first_issues=2,  # 12
    )
    result = scored(c, user)
    assert result.score == 30
    assert result.reasons == (
        "2 good first issues",
        "Fintech",
        "Raised Seed · 100 days ago",
    )


def test_suggestion_carries_the_company_fields() -> None:
    funded = NOW - timedelta(days=5)
    result = scored(
        hiring(
            company_id="abc",
            name="Quill",
            summary="Payments for marketplaces",
            area="Indiranagar",
            stage="seed",
            sector="fintech",
            last_funding_at=funded,
        )
    )
    assert result.company_id == "abc"
    assert result.name == "Quill"
    assert result.summary == "Payments for marketplaces"
    assert result.area == "Indiranagar"
    assert result.stage == "seed"
    assert result.sector == "fintech"
    assert result.last_funding_at == funded


def test_rank_companies_orders_by_score_and_drops_the_weak() -> None:
    strong = hiring(company_id="s", name="Strong", matching_roles=3)
    middling = hiring(company_id="m", name="Middling")
    weak = sig(company_id="w", name="Weak", open_roles=2)
    ranked = rank_companies([weak, middling, strong], NO_USER, NOW)
    assert [s.company_id for s in ranked] == ["s", "m"]


def test_ties_break_by_funding_then_name() -> None:
    recent = NOW - timedelta(days=200)  # outside every band: no points
    older = NOW - timedelta(days=300)
    a = hiring(company_id="a", name="Alpha", last_funding_at=older)
    b = hiring(company_id="b", name="Beta", last_funding_at=recent)
    c = hiring(company_id="c", name="Charlie")  # never funded
    d = hiring(company_id="d", name="Delta")
    e = hiring(company_id="e", name="bravo")
    ranked = rank_companies([d, c, a, b, e], NO_USER, NOW)
    assert {s.score for s in ranked} == {HIRING}
    # Most recent funding first, then the unfunded by name (case-insensitive).
    assert [s.company_id for s in ranked] == ["b", "a", "e", "c", "d"]


def test_rank_companies_limit() -> None:
    cs = [hiring(company_id=f"c{i}", name=f"Co {i:02d}") for i in range(30)]
    assert len(rank_companies(cs, NO_USER, NOW)) == 25
    assert len(rank_companies(cs, NO_USER, NOW, limit=3)) == 3
    assert rank_companies(cs, NO_USER, NOW, limit=0) == []


def test_rank_companies_accepts_a_generator_and_empty_input() -> None:
    empty: tuple[CompanySignals, ...] = ()
    assert rank_companies(iter(empty), NO_USER, NOW) == []
    ranked = rank_companies(
        (hiring(company_id=str(i), name=f"N{i}") for i in range(2)), NO_USER, NOW
    )
    assert [s.company_id for s in ranked] == ["0", "1"]


# --- size class ---------------------------------------------------------------


def test_size_class() -> None:
    assert size_class("gcc", None, None, None) == "large"
    assert size_class(None, None, "11-50", None) == "small"
    assert size_class(None, None, None, "seed") == "small"
    assert size_class("scaleup", 120, None, "series_b") == "mid"
    assert size_class(None, 250, None, None) == "large"


def test_size_class_edges() -> None:
    assert size_class(None, None, None, None) == "mid"  # nothing known
    assert size_class(None, 40, None, "series_b") == "small"  # headcount wins over a late round
    assert size_class(None, None, None, "pre_seed") == "small"
    assert size_class(None, None, None, "series_a") == "small"
    assert size_class(None, None, None, "series_b") == "mid"
    assert size_class("big_tech", 30, None, "seed") == "large"  # the big-company rule goes first
    assert size_class(None, None, "1,001-5,000", None) == "large"


# --- people -------------------------------------------------------------------


def test_kind_weights_table() -> None:
    assert KIND_WEIGHTS == {
        "small": {
            "referral": 55,
            "founder": 50,
            "leader": 40,
            "engineer": 25,
            "recruiter": 20,
            "other": 15,
            "careers_inbox": 10,
        },
        "mid": {
            "referral": 55,
            "founder": 35,
            "leader": 45,
            "engineer": 30,
            "recruiter": 35,
            "other": 15,
            "careers_inbox": 15,
        },
        "large": {
            "referral": 55,
            "founder": 10,
            "leader": 35,
            "engineer": 40,
            "recruiter": 45,
            "other": 15,
            "careers_inbox": 20,
        },
    }


def _trio() -> list[PersonSignals]:
    return [
        person(contact_id="r", name="Rhea Recruiter", kind="recruiter"),
        person(contact_id="e", name="Evan Engineer", kind="engineer"),
        person(contact_id="f", name="Farah Founder", kind="founder"),
    ]


def test_people_small_company_founder_first() -> None:
    ranked = rank_people(_trio(), "small")
    assert [p.contact_id for p in ranked] == ["f", "e", "r"]
    assert [p.score for p in ranked] == [50, 25, 20]
    assert ranked[0].reason == "Founders read their own messages at this size"


def test_people_large_company_recruiter_and_engineer_first() -> None:
    people = [*_trio(), person(contact_id="l", name="Lena Lead", kind="leader")]
    ranked = rank_people(people, "large")
    assert [p.contact_id for p in ranked] == ["r", "e", "l", "f"]
    assert ranked[0].reason == "Recruiters run the pipeline at this size"
    assert ranked[1].reason == "An engineer here can refer you"


def test_people_mid_company() -> None:
    people = [*_trio(), person(contact_id="l", name="Lena Lead", kind="leader")]
    ranked = rank_people(people, "mid")
    assert [p.contact_id for p in ranked] == ["l", "f", "r", "e"]
    assert [p.score for p in ranked] == [45, 35, 35, 30]  # founder/recruiter tie by name
    assert ranked[0].reason == "Engineering leads hire for their own teams"


def test_fallback_reason_for_any_other_kind_and_size() -> None:
    ranked = rank_people([person(kind="careers_inbox")], "small")
    assert ranked[0].reason == "Careers inbox at this company"
    assert rank_people([person(kind="other")], "large")[0].reason == "Other at this company"
    assert rank_people([person(kind="founder")], "large")[0].reason == "Founder at this company"
    assert rank_people([person(kind="leader")], "small")[0].reason == "Leader at this company"


def test_bonuses() -> None:
    base = rank_people([person(kind="engineer")], "mid")[0].score
    assert base == 30

    def score(**kw: object) -> int:
        return rank_people([person(kind="engineer", **kw)], "mid")[0].score

    assert score(user_saved=True) - base == 15
    assert score(verified=True) - base == 10
    assert score(has_direct_link=True) - base == 8
    assert score(has_public_email=True) - base == 8
    assert score(has_direct_link=True, has_public_email=True) - base == 8  # not cumulative
    assert score(user_saved=True, verified=True, has_direct_link=True) - base == 33

    saved = rank_people([person(kind="engineer", user_saved=True)], "mid")[0]
    assert saved.reason == "Someone you know"
    assert saved.user_saved is True


def test_user_saved_referral_is_55_plus_bonus_at_every_size() -> None:
    sizes: tuple[SizeClass, ...] = ("small", "mid", "large")
    for size in sizes:
        got = rank_people([person(kind="referral", user_saved=True)], size)[0]
        assert got.score == 55 + 15
        assert got.reason == "Someone you know"


def test_a_known_person_outranks_a_cold_founder_at_a_small_company() -> None:
    people = [
        person(contact_id="f", name="Farah Founder", kind="founder"),
        person(contact_id="k", name="Kiran Known", kind="referral", user_saved=True),
    ]
    assert [p.contact_id for p in rank_people(people, "small")] == ["k", "f"]


def test_mid_leader_reason_with_bonuses_unsaved() -> None:
    ranked = rank_people([person(kind="leader", verified=True)], "mid")
    assert ranked[0].reason == "Engineering leads hire for their own teams"
    assert ranked[0].score == 55


def test_people_ties_break_by_name() -> None:
    people = [
        person(contact_id="2", name="zoya", kind="engineer"),
        person(contact_id="1", name="Arun", kind="engineer"),
        person(contact_id="3", name="Maya", kind="engineer"),
    ]
    assert [p.name for p in rank_people(people, "mid")] == ["Arun", "Maya", "zoya"]


def test_careers_page_is_not_a_person() -> None:
    people = [
        person(contact_id="page", name="Careers", kind="careers_page"),
        person(contact_id="e", name="Evan Engineer", kind="engineer"),
    ]
    assert [p.contact_id for p in rank_people(people, "mid")] == ["e"]
    assert rank_people([person(kind="careers_page")], "mid") == []


def test_ranked_person_carries_the_display_fields() -> None:
    p = person(
        contact_id="x",
        name="Dev Patel",
        title="VP Engineering",
        kind="leader",
        verified=True,
        source_host="example.test",
    )
    got = rank_people([p], "mid")[0]
    assert (got.contact_id, got.name, got.title, got.kind) == (
        "x",
        "Dev Patel",
        "VP Engineering",
        "leader",
    )
    assert got.verified is True
    assert got.user_saved is False
    assert got.source_host == "example.test"


def test_unknown_kind_is_weighted_like_other() -> None:
    got = rank_people([person(kind="mystery")], "mid")[0]
    assert got.score == KIND_WEIGHTS["mid"]["other"]
    assert got.reason == "Mystery at this company"


def test_rank_people_does_not_mutate_its_input() -> None:
    people = _trio()
    snapshot = list(people)
    rank_people(people, "small")
    assert people == snapshot


# --- profile ------------------------------------------------------------------


def test_sectors_for_profile() -> None:
    assert sectors_for_profile(
        ["Backend Engineer"], "I want to work in fintech payments"
    ) == frozenset({"fintech"})


def test_sectors_for_profile_reads_roles_and_every_clause_of_the_goals() -> None:
    got = sectors_for_profile(
        ["ML Engineer", "Game Developer"],
        "Interested in healthcare. Also fintech, and developer tools",
    )
    assert {"fintech", "healthtech", "devtools", "gaming"} <= got
    assert "other" not in got


def test_sectors_for_profile_empty_inputs() -> None:
    assert sectors_for_profile([], None) == frozenset()
    assert sectors_for_profile([], "") == frozenset()
    assert sectors_for_profile(["Backend Engineer"], None) == frozenset()
    assert sectors_for_profile(["", "  "], "   ") == frozenset()
