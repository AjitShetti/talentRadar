"""Practice: the system design problems, how a day's one is picked, and how they load."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import pytest

from domain.practice_design import DESIGN_TOPICS, DesignProblem, pick_design
from domain.practice_pick import HistoryItem
from domain.practice_topics import Track
from services.practice.design_catalogue import load_problems, parse_problem

DAY = date(2026, 11, 3)
USER = "4f1c2d3e-0000-4000-8000-000000000001"

GOOD = {
    "slug": "url-shortener",
    "title": "Design a URL shortener",
    "tracks": ["backend", "general"],
    "minutes": 30,
    "topics": ["caching", "data-modelling"],
    "summary": "Short links that redirect fast.",
    "context": ["A million new links a day.", "Reads outnumber writes a hundred to one."],
    "must_handle": ["A link that goes viral"],
    "show_in_answer": ["Where the cache sits"],
}


def problem(
    slug: str,
    topics: tuple[str, ...] = ("caching",),
    tracks: tuple[Track, ...] = ("general",),
) -> DesignProblem:
    return DesignProblem(
        slug=slug,
        title=slug.replace("-", " ").title(),
        tracks=tracks,
        minutes=30,
        topics=topics,
        summary="A summary.",
        context=("Some context.",),
        must_handle=("One case.",),
        show_in_answer=("One thing.",),
    )


def done(slug: str, day: date, *, topic: str = "caching", is_done: bool = True) -> HistoryItem:
    return HistoryItem(
        day=day, slug=slug, topic=topic, difficulty="medium", done=is_done, feel=None
    )


def pick(
    problems: Sequence[DesignProblem],
    *,
    swaps: int = 0,
    track: Track = "backend",
    history: Sequence[HistoryItem] = (),
    user_id: str = USER,
    day: date = DAY,
):
    return pick_design(
        user_id=user_id, day=day, swaps=swaps, track=track, history=history, problems=problems
    )


SET = [problem(f"p-{n}", topics=(DESIGN_TOPICS[n % len(DESIGN_TOPICS)],)) for n in range(12)]


def test_the_same_inputs_give_the_same_pick() -> None:
    assert pick(SET) == pick(SET)


def test_the_order_of_the_list_does_not_change_the_pick() -> None:
    assert pick(SET) == pick(list(reversed(SET)))


def test_a_swap_gives_a_different_problem() -> None:
    first, second = pick(SET), pick(SET, swaps=1)
    assert first is not None and second is not None
    assert first.problem.slug != second.problem.slug


def test_only_the_persons_track_and_general_are_picked() -> None:
    mine = problem("mine", tracks=("backend",))
    everyone = problem("everyone", tracks=("general",))
    other = problem("other", tracks=("mobile",))
    slugs = set()
    for n in range(40):
        picked = pick([mine, everyone, other], user_id=f"user-{n}")
        assert picked is not None
        slugs.add(picked.problem.slug)
    assert slugs == {"mine", "everyone"}


def test_with_nothing_for_the_track_any_problem_is_picked() -> None:
    picked = pick([problem("other", tracks=("mobile",))], track="backend")
    assert picked is not None and picked.problem.slug == "other"


def test_a_done_problem_does_not_come_back_while_others_remain() -> None:
    history = [done("p-1", DAY - timedelta(days=30))]
    for n in range(40):
        picked = pick([problem("p-1"), problem("p-2")], history=history, user_id=f"user-{n}")
        assert picked is not None
        assert picked.problem.slug == "p-2"
        assert picked.repeat is False


def test_a_problem_swapped_away_and_not_done_can_come_back() -> None:
    history = [done("p-1", DAY - timedelta(days=1), is_done=False)]
    picked = pick([problem("p-1")], history=history)
    assert picked is not None
    assert picked.problem.slug == "p-1"
    assert picked.repeat is False


def test_when_all_are_done_the_oldest_comes_back_and_says_so() -> None:
    history = [
        done("oldest", DAY - timedelta(weeks=14)),
        done("newer", DAY - timedelta(weeks=3)),
    ]
    picked = pick([problem("newer"), problem("oldest")], history=history)
    assert picked is not None
    assert picked.repeat is True
    assert picked.problem.slug == "oldest"
    assert picked.reason == "You did this 14 weeks ago. See what you would change."


def test_a_repeat_from_days_ago_says_one_week() -> None:
    picked = pick([problem("only")], history=[done("only", DAY - timedelta(days=3))])
    assert picked is not None
    assert picked.reason == "You did this 1 week ago. See what you would change."


def test_what_was_done_today_does_not_change_todays_pick() -> None:
    before = pick(SET)
    assert before is not None
    after = pick(SET, history=[done(before.problem.slug, DAY)])
    assert after is not None
    assert after.problem.slug == before.problem.slug


def test_a_swap_past_every_problem_still_gives_one() -> None:
    picked = pick([problem("only")], swaps=3)
    assert picked is not None
    assert picked.problem.slug == "only"


def test_a_negative_swap_count_is_refused() -> None:
    with pytest.raises(ValueError):
        pick(SET, swaps=-1)


def test_no_problems_gives_none() -> None:
    assert pick([]) is None


def test_the_reason_splits_on_the_topic_separator() -> None:
    picked = pick([problem("only")])
    assert picked is not None
    assert picked.reason == "Caching — you have not practised it yet."


def test_the_reason_says_when_the_topic_was_last_practised() -> None:
    history = [done("other", DAY - timedelta(days=30))]
    picked = pick([problem("only"), problem("other")], history=history)
    assert picked is not None
    assert picked.reason == "Caching — you last practised it 30 days ago."


def test_the_reason_says_yesterday_rather_than_one_days_ago() -> None:
    history = [done("other", DAY - timedelta(days=1))]
    picked = pick([problem("only"), problem("other")], history=history)
    assert picked is not None
    assert picked.reason == "Caching — you last practised it yesterday."


def test_a_neglected_topic_is_drawn_more_often_than_a_recent_one() -> None:
    fresh = problem("fresh", topics=("queues",))
    rested = problem("rested", topics=("caching",))
    history = [done("old-cache", DAY - timedelta(days=1))]
    drawn = [pick([fresh, rested], history=history, user_id=f"user-{n}") for n in range(300)]
    assert sum(1 for item in drawn if item and item.problem.slug == "fresh") > 200


def test_parse_problem_reads_a_good_file() -> None:
    parsed = parse_problem(GOOD)
    assert parsed.slug == "url-shortener"
    assert parsed.tracks == ("backend", "general")
    assert parsed.context == (
        "A million new links a day.",
        "Reads outnumber writes a hundred to one.",
    )


def test_parse_problem_names_the_bad_field() -> None:
    with pytest.raises(ValueError, match="must_handle"):
        parse_problem({**GOOD, "must_handle": "x"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slug", "Not A Slug"),
        ("title", "  "),
        ("tracks", ["astronaut"]),
        ("tracks", []),
        ("minutes", 14),
        ("minutes", 61),
        ("minutes", True),
        ("topics", ["cooking"]),
        ("topics", []),
        ("summary", ""),
        ("context", []),
        ("show_in_answer", [""]),
    ],
)
def test_parse_problem_refuses_each_bad_value(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        parse_problem({**GOOD, field: value})


def test_parse_problem_refuses_an_unknown_key() -> None:
    with pytest.raises(ValueError, match="solution"):
        parse_problem({**GOOD, "solution": "secret"})


def test_parse_problem_refuses_a_missing_key() -> None:
    without = {key: value for key, value in GOOD.items() if key != "summary"}
    with pytest.raises(ValueError, match="summary"):
        parse_problem(without)


def test_parse_problem_refuses_what_is_not_an_object() -> None:
    with pytest.raises(ValueError):
        parse_problem(["not", "an", "object"])


def test_load_problems_skips_a_bad_file_and_keeps_the_rest(tmp_path: Path) -> None:
    (tmp_path / "01-good.json").write_text(json.dumps(GOOD), encoding="utf-8")
    (tmp_path / "02-bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "03-wrong.json").write_text(
        json.dumps({**GOOD, "slug": "wrong", "minutes": 5}), encoding="utf-8"
    )
    assert [item.slug for item in load_problems(tmp_path)] == ["url-shortener"]


def test_load_problems_keeps_the_first_of_a_repeated_slug(tmp_path: Path) -> None:
    (tmp_path / "01-a.json").write_text(json.dumps(GOOD), encoding="utf-8")
    (tmp_path / "02-b.json").write_text(json.dumps({**GOOD, "title": "Other"}), encoding="utf-8")
    assert [item.title for item in load_problems(tmp_path)] == ["Design a URL shortener"]


def test_load_problems_on_a_missing_folder_is_empty(tmp_path: Path) -> None:
    assert load_problems(tmp_path / "nowhere") == ()
