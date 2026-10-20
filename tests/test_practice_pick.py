"""Practice: role tracks, the difficulty band, topic weights and the deterministic daily pick.

Everything here is pure: no clock, no database, no network. Test catalogues use
obviously synthetic slugs (``q-001``), never a problem statement.
"""

from __future__ import annotations

import dataclasses
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import pytest

from domain import practice_pick
from domain.practice_pick import (
    Difficulty,
    Feel,
    HistoryItem,
    Pick,
    Question,
    difficulty_band,
    pick_question,
    topic_label,
    topic_weights,
)
from domain.practice_topics import (
    TRACK_KEYWORDS,
    TRACK_LABELS,
    TRACK_TOPICS,
    Track,
    track_for_roles,
)

TODAY = date(2026, 10, 15)
DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "medium", "hard")


# --------------------------------------------------------------------------- helpers


def make_catalogue(per_cell: int = 4) -> list[Question]:
    """``per_cell`` synthetic questions for every backend topic at every difficulty."""
    questions: list[Question] = []
    number = 0
    for topic in sorted(TRACK_TOPICS["backend"]):
        for difficulty in DIFFICULTIES:
            for _ in range(per_cell):
                number += 1
                questions.append(
                    Question(
                        id=number,
                        slug=f"q-{number:03d}",
                        title=f"Question {number}",
                        difficulty=difficulty,
                        topics=(topic,),
                    )
                )
    return questions


def question(
    number: int, difficulty: Difficulty = "hard", topics: tuple[str, ...] = ("graph",)
) -> Question:
    return Question(
        id=number,
        slug=f"q-{number:03d}",
        title=f"Question {number}",
        difficulty=difficulty,
        topics=topics,
    )


def done(
    slug: str,
    topic: str,
    days_ago: int,
    *,
    difficulty: Difficulty = "medium",
    feel: Feel | None = None,
    is_done: bool = True,
) -> HistoryItem:
    return HistoryItem(
        day=TODAY - timedelta(days=days_ago),
        slug=slug,
        topic=topic,
        difficulty=difficulty,
        done=is_done,
        feel=feel,
    )


def pick(
    catalogue: Sequence[Question],
    *,
    user_id: str = "user-1",
    day: date = TODAY,
    swaps: int = 0,
    track: Track = "backend",
    years: float | None = 2,
    history: Sequence[HistoryItem] = (),
) -> Pick | None:
    return pick_question(
        user_id=user_id,
        day=day,
        swaps=swaps,
        track=track,
        years=years,
        history=history,
        catalogue=catalogue,
    )


def must_pick(catalogue: Sequence[Question], **kwargs: Any) -> Pick:
    result = pick(catalogue, **kwargs)
    assert result is not None
    return result


@pytest.fixture
def only_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backend interviews practise graphs only, and the band asks for mediums only."""
    monkeypatch.setitem(TRACK_TOPICS, "backend", {"graph": 1.0})
    monkeypatch.setattr(
        practice_pick, "difficulty_band", lambda years, feels: {"easy": 0, "medium": 100, "hard": 0}
    )


# --------------------------------------------------------------------------- tracks


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (["Senior Backend Engineer"], "backend"),
        (["Data Scientist"], "ml"),
        (["Data Engineer"], "data"),
        (["React Developer"], "frontend"),
        (["Android Engineer"], "mobile"),
        (["SRE"], "devops"),
        (["SDE 2"], "backend"),
        ([], "general"),
        (None, "general"),
        (["Machine Learning Engineer"], "ml"),
        (["AI/ML Engineer"], "ml"),
        (["Business Analyst"], "data"),
        (["Front-end Engineer"], "frontend"),
        (["iOS Developer"], "mobile"),
        (["Platform Engineer"], "devops"),
        (["Site Reliability Engineer"], "devops"),
        (["Java Developer"], "backend"),
        (["Back End Engineer"], "backend"),
        (["Product Manager"], "general"),
        ([""], "general"),
        (["   ", ""], "general"),
        # Mobile roles that name a UI framework are mobile, not frontend.
        (["React Native Developer"], "mobile"),
        (["Flutter UI Engineer"], "mobile"),
        (["Mobile UI Engineer"], "mobile"),
        (["UI Engineer"], "frontend"),
        # Common Indian titles.
        (["SDE2"], "backend"),
        (["SDE-3"], "backend"),
        (["Sde 1"], "backend"),
        (["Java8 Engineer"], "backend"),
        (["Full Stack Engineer"], "backend"),
        (["Full-stack Engineer"], "backend"),
        (["Full-stack Developer"], "backend"),
        (["Fullstack Engineer"], "backend"),
    ],
)
def test_track_for_roles(roles: list[str] | None, expected: Track) -> None:
    assert track_for_roles(roles) == expected


def test_track_keywords_match_whole_words_only() -> None:
    # "ml" is inside "HTML" and "ios" inside "radios"; neither is a machine-learning or mobile role.
    assert track_for_roles(["HTML Developer"]) == "backend"
    assert track_for_roles(["Radios Technician"]) == "general"


def test_first_listed_role_that_matches_decides_the_track() -> None:
    assert track_for_roles(["Backend Engineer", "Data Scientist"]) == "backend"
    assert track_for_roles(["Data Scientist", "Backend Engineer"]) == "ml"
    assert track_for_roles(["Retail Associate", "SRE"]) == "devops"


def test_track_keyword_order_is_the_documented_one() -> None:
    assert [track for track, _ in TRACK_KEYWORDS] == [
        "ml",
        "data",
        "mobile",
        "frontend",
        "devops",
        "backend",
    ]


def test_react_native_is_mobile_but_react_is_frontend() -> None:
    assert track_for_roles(["React Native Engineer"]) == "mobile"
    assert track_for_roles(["React Developer"]) == "frontend"
    assert track_for_roles(["React Engineer"]) == "frontend"
    assert track_for_roles(["Android Engineer"]) == "mobile"
    assert track_for_roles(["iOS Developer"]) == "mobile"


def test_a_digit_stuck_to_a_title_word_does_not_hide_it() -> None:
    # "SDE2" and "SDE 2" are the same role; so are "Python3 Engineer" and "Python 3 Engineer".
    assert track_for_roles(["SDE2"]) == track_for_roles(["SDE 2"]) == "backend"
    assert track_for_roles(["Python3 Engineer"]) == track_for_roles(["Python 3 Engineer"])
    assert track_for_roles(["Python3 Engineer"]) == "backend"
    # ...but a word that merely starts like a keyword is still not that keyword.
    assert track_for_roles(["Sdeo Specialist"]) == "general"


def test_every_track_has_a_label_and_a_topic_table() -> None:
    tracks = {"backend", "frontend", "data", "ml", "mobile", "devops", "general"}
    assert set(TRACK_LABELS) == tracks
    assert set(TRACK_TOPICS) == tracks
    for table in TRACK_TOPICS.values():
        assert len(table) >= 8
        assert all(weight > 0 for weight in table.values())
        assert all(re.fullmatch(r"[a-z]+(?:-[a-z]+)*", slug) for slug in table)


def test_backend_table_is_the_specified_one() -> None:
    assert TRACK_TOPICS["backend"] == {
        "array": 3,
        "hash-table": 3,
        "string": 2,
        "two-pointers": 2,
        "sliding-window": 2,
        "binary-search": 2,
        "tree": 2,
        "graph": 2,
        "heap-priority-queue": 2,
        "dynamic-programming": 2,
        "linked-list": 1,
        "stack": 1,
        "database": 1,
        "concurrency": 1,
    }
    assert TRACK_TOPICS["data"]["database"] == 4


# --------------------------------------------------------------------------- difficulty band


@pytest.mark.parametrize(
    ("years", "expected"),
    [
        (0.5, {"easy": 60, "medium": 40, "hard": 0}),
        (2, {"easy": 30, "medium": 60, "hard": 10}),
        (4, {"easy": 10, "medium": 70, "hard": 20}),
        (8, {"easy": 0, "medium": 65, "hard": 35}),
        (None, {"easy": 30, "medium": 60, "hard": 10}),
    ],
)
def test_difficulty_band_by_years(years: float | None, expected: dict[str, int]) -> None:
    assert difficulty_band(years, []) == expected


@pytest.mark.parametrize(
    ("years", "expected_years"),
    [(0, 0.5), (0.99, 0.5), (1, 2), (2.99, 2), (3, 4), (5.99, 4), (6, 8), (30, 8), (-1, 0.5)],
)
def test_difficulty_band_boundaries(years: float, expected_years: float) -> None:
    assert difficulty_band(years, []) == difficulty_band(expected_years, [])


def test_three_hard_of_five_moves_the_band_one_step_easier() -> None:
    assert difficulty_band(4, ["hard", "hard", "hard", "right", "easy"]) == difficulty_band(2, [])


def test_three_easy_of_five_moves_the_band_one_step_harder() -> None:
    assert difficulty_band(2, ["easy", "easy", "easy", "right", "hard"]) == difficulty_band(4, [])


def test_two_of_five_does_not_move_the_band() -> None:
    assert difficulty_band(4, ["hard", "hard", "right", "right", "easy"]) == difficulty_band(4, [])


def test_the_band_stays_inside_the_table() -> None:
    assert difficulty_band(0.5, ["hard"] * 5) == {"easy": 60, "medium": 40, "hard": 0}
    assert difficulty_band(8, ["easy"] * 5) == {"easy": 0, "medium": 65, "hard": 35}


def test_only_the_last_five_ratings_count() -> None:
    old_hard: list[Feel] = ["hard"] * 6
    recent: list[Feel] = ["right", "right", "right", "easy", "hard"]
    assert difficulty_band(4, [*old_hard, *recent]) == difficulty_band(4, [])


def test_difficulty_band_returns_a_fresh_dict() -> None:
    band = difficulty_band(2, [])
    band["easy"] = 99
    assert difficulty_band(2, []) == {"easy": 30, "medium": 60, "hard": 10}


# --------------------------------------------------------------------------- topic weights


def test_topic_not_practised_in_21_days_is_doubled() -> None:
    weights = topic_weights("backend", [], TODAY)
    assert weights["database"] == TRACK_TOPICS["backend"]["database"] * 2.0
    assert set(weights) == set(TRACK_TOPICS["backend"])


def test_topic_done_yesterday_is_damped() -> None:
    weights = topic_weights("backend", [done("q-001", "graph", 1)], TODAY)
    assert weights["graph"] == pytest.approx(TRACK_TOPICS["backend"]["graph"] * 0.3)
    assert weights["tree"] == TRACK_TOPICS["backend"]["tree"] * 2.0


def test_topic_whose_last_question_was_hard_is_boosted() -> None:
    weights = topic_weights("backend", [done("q-001", "graph", 10, feel="hard")], TODAY)
    assert weights["graph"] == pytest.approx(TRACK_TOPICS["backend"]["graph"] * 1.5)


def test_hard_and_neglected_multiply() -> None:
    weights = topic_weights("backend", [done("q-001", "graph", 30, feel="hard")], TODAY)
    assert weights["graph"] == pytest.approx(TRACK_TOPICS["backend"]["graph"] * 2.0 * 1.5)


def test_a_topic_practised_a_fortnight_ago_keeps_its_base_weight() -> None:
    weights = topic_weights("backend", [done("q-001", "graph", 14, feel="right")], TODAY)
    assert weights["graph"] == TRACK_TOPICS["backend"]["graph"]


@pytest.mark.parametrize(
    ("days_ago", "factor"),
    [(0, 0.3), (1, 0.3), (2, 0.3), (3, 1.0), (21, 1.0), (22, 2.0)],
)
def test_gap_thresholds(days_ago: int, factor: float) -> None:
    weights = topic_weights("backend", [done("q-001", "graph", days_ago)], TODAY)
    assert weights["graph"] == pytest.approx(TRACK_TOPICS["backend"]["graph"] * factor)


def test_only_the_most_recent_question_on_a_topic_sets_the_feel() -> None:
    history = [done("q-001", "graph", 12, feel="hard"), done("q-002", "graph", 8, feel="right")]
    assert topic_weights("backend", history, TODAY)["graph"] == TRACK_TOPICS["backend"]["graph"]
    flipped = [done("q-001", "graph", 12, feel="right"), done("q-002", "graph", 8, feel="hard")]
    assert topic_weights("backend", flipped, TODAY)["graph"] == pytest.approx(
        TRACK_TOPICS["backend"]["graph"] * 1.5
    )


def test_history_order_does_not_matter_to_weights() -> None:
    history = [done("q-001", "graph", 12, feel="hard"), done("q-002", "graph", 8, feel="right")]
    assert topic_weights("backend", history, TODAY) == topic_weights(
        "backend", list(reversed(history)), TODAY
    )


def test_two_done_items_on_one_day_and_topic_resolve_by_slug_not_input_order() -> None:
    # The later slug is the "last question" on the topic, whichever order the rows arrive in.
    hard_then_right = [
        done("q-001", "graph", 8, feel="hard"),
        done("q-002", "graph", 8, feel="right"),
    ]
    right_then_hard = [
        done("q-001", "graph", 8, feel="right"),
        done("q-002", "graph", 8, feel="hard"),
    ]
    base = TRACK_TOPICS["backend"]["graph"]
    for history in (hard_then_right, list(reversed(hard_then_right))):
        assert topic_weights("backend", history, TODAY)["graph"] == base
    for history in (right_then_hard, list(reversed(right_then_hard))):
        assert topic_weights("backend", history, TODAY)["graph"] == pytest.approx(base * 1.5)


def test_ratings_on_one_day_reach_the_band_in_slug_order_whatever_the_input_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[Feel]] = []

    def spy(years: float | None, feels: Sequence[Feel]) -> dict[Difficulty, int]:
        seen.append(list(feels))
        return {"easy": 0, "medium": 100, "hard": 0}

    monkeypatch.setattr(practice_pick, "difficulty_band", spy)
    rows = [
        done("q-001", "graph", 5, feel="hard"),
        done("q-002", "graph", 5, feel="right"),
        done("q-003", "tree", 3, feel="easy"),
    ]
    catalogue = make_catalogue(1)
    must_pick(catalogue, history=rows)
    must_pick(catalogue, history=list(reversed(rows)))
    assert seen == [["hard", "right", "easy"]] * 2


def test_a_question_that_was_not_done_is_not_practice() -> None:
    history = [done("q-001", "graph", 1, is_done=False)]
    assert topic_weights("backend", history, TODAY)["graph"] == (
        TRACK_TOPICS["backend"]["graph"] * 2.0
    )


def test_history_on_a_topic_outside_the_track_is_ignored() -> None:
    weights = topic_weights("backend", [done("q-001", "geometry", 1)], TODAY)
    assert set(weights) == set(TRACK_TOPICS["backend"])


# --------------------------------------------------------------------------- the daily pick


def test_same_inputs_give_the_same_pick() -> None:
    catalogue = make_catalogue()
    assert len(catalogue) >= 20
    first, second = must_pick(catalogue), must_pick(catalogue)
    assert first == second
    assert first.question.slug == second.question.slug


def test_pick_does_not_depend_on_catalogue_order() -> None:
    catalogue = make_catalogue()
    assert must_pick(catalogue) == must_pick(list(reversed(catalogue)))


def test_a_swap_gives_a_different_question() -> None:
    catalogue = make_catalogue()
    assert must_pick(catalogue, swaps=1).question.slug != must_pick(catalogue).question.slug


def test_every_swap_is_a_new_question_for_any_user_or_day() -> None:
    catalogue = make_catalogue()
    for number in range(40):
        user_id = f"user-{number}"
        day = TODAY + timedelta(days=number)
        slugs = [
            must_pick(catalogue, user_id=user_id, day=day, swaps=s).question.slug for s in range(4)
        ]
        assert len(set(slugs)) == 4


def test_pick_varies_with_user_and_day() -> None:
    catalogue = make_catalogue()
    by_day = {must_pick(catalogue, day=TODAY + timedelta(days=n)).question.slug for n in range(30)}
    by_user = {must_pick(catalogue, user_id=f"user-{n}").question.slug for n in range(30)}
    assert len(by_day) > 10
    assert len(by_user) > 10


def test_pick_is_a_question_from_the_catalogue_with_its_topic() -> None:
    catalogue = make_catalogue()
    result = must_pick(catalogue)
    assert result.question in catalogue
    assert result.topic in result.question.topics
    assert result.difficulty == result.question.difficulty
    assert result.relaxed == ()


def test_pick_follows_the_difficulty_band() -> None:
    catalogue = make_catalogue()
    picked = Counter(
        must_pick(catalogue, user_id=f"user-{n}", years=0.5).difficulty for n in range(300)
    )
    assert picked["hard"] == 0
    assert picked["easy"] > picked["medium"] > 0


def test_pick_favours_heavier_topics() -> None:
    catalogue = make_catalogue()
    picked = Counter(must_pick(catalogue, user_id=f"user-{n}").topic for n in range(600))
    assert picked["array"] > picked["linked-list"]
    assert picked["hash-table"] > picked["concurrency"]


def test_never_returns_a_question_done_in_the_last_180_days_when_an_alternative_exists() -> None:
    catalogue = make_catalogue()
    done_slugs = [q.slug for q in catalogue[:120]]
    history = [
        done(slug, catalogue[index].topics[0], 1 + index) for index, slug in enumerate(done_slugs)
    ]
    for number in range(60):
        result = must_pick(
            catalogue,
            user_id=f"user-{number}",
            day=TODAY + timedelta(days=number % 7),
            history=history,
        )
        assert result.question.slug not in done_slugs
        assert "history" not in result.relaxed


def test_the_180_day_window_edges(only_graph: None) -> None:
    catalogue = [question(1, "medium"), question(2, "medium")]
    # Done 180 days ago is still inside the window; 181 days ago has expired.
    inside = [done("q-001", "graph", 180)]
    assert {
        must_pick(catalogue, user_id=f"u{n}", history=inside).question.slug for n in range(20)
    } == {"q-002"}
    expired = [done("q-001", "graph", 181), done("q-002", "graph", 500)]
    seen = {must_pick(catalogue, user_id=f"u{n}", history=expired).question.slug for n in range(20)}
    assert seen == {"q-001", "q-002"}


def test_a_question_that_was_not_done_can_be_picked_again(only_graph: None) -> None:
    catalogue = [question(1, "medium")]
    history = [done("q-001", "graph", 3, difficulty="medium", is_done=False)]
    result = must_pick(catalogue, history=history)
    assert result.question.slug == "q-001"
    assert result.relaxed == ()


def test_the_pick_for_a_day_ignores_that_day_and_later(only_graph: None) -> None:
    catalogue = [question(n, "medium") for n in range(1, 6)]
    before = must_pick(catalogue)
    marked_done = [done(before.question.slug, "graph", 0, feel="hard")]
    later = [done("q-001", "graph", -3), done("q-002", "graph", -1)]
    assert must_pick(catalogue, history=marked_done) == before
    assert must_pick(catalogue, history=later) == before


def test_pool_relaxes_difficulty_then_topic(only_graph: None) -> None:
    # Band: 100% medium on graph. Only Hard graph questions exist: relax difficulty.
    hard_graph = [question(n, "hard", ("graph",)) for n in range(1, 4)]
    result = must_pick(hard_graph)
    assert result.question.difficulty == "hard"
    assert result.difficulty == "hard"
    assert result.topic == "graph"
    assert "difficulty" in result.relaxed
    assert result.relaxed == ("difficulty",)

    # No graph question at all: relax the topic too.
    only_trees = [question(n, "hard", ("tree",)) for n in range(1, 4)]
    result = must_pick(only_trees)
    assert result.topic == "tree"
    assert result.relaxed == ("difficulty", "topic")

    # A medium on another topic is a smaller step than a hard on the same topic: topic only.
    medium_tree = [question(1, "medium", ("tree",))]
    result = must_pick(medium_tree)
    assert result.relaxed == ("topic",)


def test_relaxation_prefers_the_difficulty_nearest_the_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(TRACK_TOPICS, "backend", {"graph": 1.0})
    monkeypatch.setattr(
        practice_pick, "difficulty_band", lambda years, feels: {"easy": 100, "medium": 0, "hard": 0}
    )
    catalogue = [question(1, "hard"), question(2, "medium")]
    assert must_pick(catalogue).question.slug == "q-002"


def test_an_exhausted_medium_falls_to_a_hard_before_an_easy_on_a_tie(only_graph: None) -> None:
    catalogue = [question(1, "easy"), question(2, "hard")]
    assert must_pick(catalogue).question.slug == "q-002"


def test_equidistant_difficulties_go_to_the_one_the_band_favours_then_the_harder() -> None:
    nearest_first = practice_pick._nearest_first
    assert nearest_first("medium", {"easy": 30, "medium": 60, "hard": 10}) == ["easy", "hard"]
    assert nearest_first("medium", {"easy": 0, "medium": 65, "hard": 35}) == ["hard", "easy"]
    assert nearest_first("medium", {"easy": 0, "medium": 100, "hard": 0}) == ["hard", "easy"]
    assert nearest_first("easy", {"easy": 60, "medium": 40, "hard": 0}) == ["medium", "hard"]
    assert nearest_first("hard", {"easy": 0, "medium": 65, "hard": 35}) == ["medium", "easy"]


def test_when_everything_is_done_the_history_rule_relaxes_last(only_graph: None) -> None:
    catalogue = [question(1, "medium"), question(2, "hard")]
    history = [done("q-001", "graph", 5), done("q-002", "graph", 9)]
    result = must_pick(catalogue, history=history)
    assert "history" in result.relaxed
    assert result.question.slug in {"q-001", "q-002"}
    assert result.relaxed[-1] == "history"


def test_a_swap_repeats_a_question_only_when_the_catalogue_has_nothing_else(
    only_graph: None,
) -> None:
    one = [question(1, "medium")]
    assert must_pick(one, swaps=0).question.slug == must_pick(one, swaps=1).question.slug == "q-001"
    # Repeating a swap is not a repeat of anything the user did, so nothing is reported relaxed.
    assert must_pick(one, swaps=1).relaxed == ()
    two = [question(1, "medium"), question(2, "medium")]
    assert must_pick(two, swaps=0).question.slug != must_pick(two, swaps=1).question.slug


def test_empty_catalogue_gives_no_pick() -> None:
    assert pick([]) is None


def test_questions_without_topics_are_never_picked() -> None:
    untagged = [question(1, "medium", ())]
    assert pick(untagged) is None
    tagged = [*untagged, question(2, "medium", ("graph",))]
    assert must_pick(tagged).question.slug == "q-002"


def test_negative_swaps_are_rejected() -> None:
    with pytest.raises(ValueError, match="swaps"):
        pick(make_catalogue(1), swaps=-1)


def test_every_track_can_be_picked_from_a_matching_catalogue() -> None:
    for track, table in TRACK_TOPICS.items():
        catalogue = [
            question(number, difficulty, (topic,))
            for number, (topic, difficulty) in enumerate(
                ((t, d) for t in sorted(table) for d in DIFFICULTIES), start=1
            )
        ]
        result = must_pick(catalogue, track=track)
        assert result.topic in table
        assert result.relaxed == ()


# --------------------------------------------------------------------------- the reason


def test_reason_names_the_topic_and_that_it_has_not_been_practised(only_graph: None) -> None:
    result = must_pick([question(n, "medium") for n in range(1, 4)])
    assert topic_label("graph") in result.reason
    assert "not practised" in result.reason or "days ago" in result.reason
    assert "backend" in result.reason


def test_reason_says_how_long_ago_a_neglected_topic_was_practised(only_graph: None) -> None:
    history = [done("q-009", "graph", 30)]
    result = must_pick([question(n, "medium") for n in range(1, 4)], history=history)
    assert "Graph" in result.reason
    assert "30 days ago" in result.reason


def test_reason_for_a_hard_topic(only_graph: None) -> None:
    history = [done("q-009", "graph", 10, feel="hard")]
    result = must_pick([question(n, "medium") for n in range(1, 4)], history=history)
    assert "Graph" in result.reason
    assert "hard" in result.reason
    assert "days ago" not in result.reason


def test_reason_for_a_topic_with_no_gap_rule() -> None:
    catalogue = make_catalogue()
    topic = must_pick(catalogue).topic
    history = [done("q-9999", topic, 14, feel="right")]
    result = must_pick(catalogue, history=history)
    assert topic_label(result.topic) in result.reason
    assert "days ago" not in result.reason
    assert "not practised" not in result.reason


def test_reason_for_the_general_track_does_not_claim_a_role() -> None:
    catalogue = [question(n, "medium", ("array",)) for n in range(1, 4)]
    result = must_pick(catalogue, track="general")
    assert "general interviews" not in result.reason
    assert topic_label(result.topic) in result.reason


def test_reason_explains_a_relaxed_difficulty(only_graph: None) -> None:
    result = must_pick([question(1, "hard")])
    assert "Graph" in result.reason
    assert "Medium" in result.reason
    assert "Hard" in result.reason


def test_reason_explains_a_relaxed_topic(only_graph: None) -> None:
    result = must_pick([question(1, "medium", ("tree",))])
    assert "Tree" in result.reason
    assert "Graph" in result.reason


def test_reason_explains_a_repeat(only_graph: None) -> None:
    result = must_pick([question(1, "medium")], history=[done("q-001", "graph", 5)])
    assert "180 days" in result.reason


def test_reason_is_one_line() -> None:
    catalogue = make_catalogue()
    for number in range(30):
        assert "\n" not in must_pick(catalogue, user_id=f"user-{number}").reason


# --------------------------------------------------------------------------- value types


def test_question_url_is_the_leetcode_problem_page() -> None:
    assert question(1, "easy", ("array",)).url == "https://leetcode.com/problems/q-001/"
    two_sum = Question(1, "two-sum", "Two Sum", "easy", ("array", "hash-table"))
    assert two_sum.url == "https://leetcode.com/problems/two-sum/"


def test_value_types_are_frozen() -> None:
    q = question(1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        q.slug = "other"  # type: ignore[misc]
    item = done("q-001", "graph", 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.done = False  # type: ignore[misc]
    result = must_pick(make_catalogue(1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.reason = "x"  # type: ignore[misc]


def test_topic_label() -> None:
    assert topic_label("graph") == "Graph"
    assert topic_label("hash-table") == "Hash Table"
    assert topic_label("heap-priority-queue") == "Heap Priority Queue"
    assert topic_label("dynamic-programming") == "Dynamic Programming"
