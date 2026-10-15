"""
tests/test_practice_catalogue.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The practice question catalogue: the parser that turns saved leetcode.com
responses into free-question pointers, the script that writes the checked-in file, and
the loader the daily pick reads. Nothing here requests leetcode.com; every payload is
a hand-written fixture or built inline.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from domain.practice_pick import Question
from domain.practice_topics import TRACK_TOPICS
from scripts.build_practice_catalogue import main
from services.practice import catalogue
from services.practice.catalogue import (
    CATALOGUE_PATH,
    catalogue_by_slug,
    load_catalogue,
    parse_saved_responses,
)
from services.practice.errors import PracticeConflict, PracticeInvalid, PracticeNotFound
from tests.test_practice_fixtures import PRACTICE_FIXTURES, load_fixture

#: The five fields a catalogue row holds, and nothing else.
ROW_FIELDS = 5
MAX_CATALOGUE_BYTES = 600 * 1024
STATEMENT_MARKERS = ("content", "acRate", "hints", "SECRET-STATEMENT-TEXT", "SECRET-HINT-TEXT")


@pytest.fixture(autouse=True)
def fresh_catalogue_cache() -> Iterator[None]:
    load_catalogue.cache_clear()
    yield
    load_catalogue.cache_clear()


def node(slug: str, **overrides: Any) -> dict[str, Any]:
    """One free problem as leetcode.com's problem list states it."""
    base: dict[str, Any] = {
        "titleSlug": slug,
        "title": slug.replace("-", " ").title(),
        "difficulty": "Medium",
        "isPaidOnly": False,
        "questionFrontendId": "7",
        "topicTags": [{"slug": "array"}],
    }
    return {**base, **overrides}


def wrap(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"questionList": {"data": list(nodes)}}}


# ---------------------------------------------------------------------------
# parse_saved_responses
# ---------------------------------------------------------------------------


def test_question_list_fixture_keeps_free_questions_only() -> None:
    questions = parse_saved_responses([load_fixture("leetcode_question_list.json")])

    assert [q.id for q in questions] == [1, 2, 3]
    assert "paid-example" not in {q.slug for q in questions}
    two_sum = questions[0]
    assert two_sum == Question(
        id=1,
        slug="two-sum",
        title="Two Sum",
        difficulty="easy",
        topics=("array", "hash-table"),
    )
    assert two_sum.url == "https://leetcode.com/problems/two-sum/"


def test_v2_shape_parses_to_the_same_two_sum_question() -> None:
    from_v2 = parse_saved_responses([load_fixture("leetcode_problemset_v2.json")])
    from_list = parse_saved_responses([load_fixture("leetcode_question_list.json")])

    assert from_v2 == [from_list[0]]
    assert from_v2[0].difficulty == "easy"


def test_v2_shape_drops_a_paid_only_row() -> None:
    payload = {
        "data": {
            "problemsetQuestionListV2": {
                "questions": [
                    {
                        "titleSlug": "free-pair",
                        "title": "Free Pair",
                        "difficulty": "MEDIUM",
                        "paidOnly": False,
                        "questionFrontendId": "10",
                        "topicTags": [{"slug": "string"}],
                    },
                    {
                        "titleSlug": "members-only-pair",
                        "title": "Members Only Pair",
                        "difficulty": "HARD",
                        "paidOnly": True,
                        "questionFrontendId": "11",
                        "topicTags": [{"slug": "string"}],
                    },
                ]
            }
        }
    }

    questions = parse_saved_responses([payload])

    assert [q.slug for q in questions] == ["free-pair"]
    assert questions[0].difficulty == "medium"


def test_is_paid_only_row_is_dropped() -> None:
    questions = parse_saved_responses(
        [wrap(node("free-one"), node("paid-one", isPaidOnly=True, questionFrontendId="8"))]
    )

    assert [q.slug for q in questions] == ["free-one"]


def without_flag(slug: str) -> dict[str, Any]:
    flagless = node(slug)
    del flagless["isPaidOnly"]
    return flagless


@pytest.mark.parametrize(
    "unknown",
    [
        pytest.param(without_flag("unflagged"), id="no-flag"),
        pytest.param(node("unflagged", isPaidOnly=None), id="null"),
        pytest.param(node("unflagged", isPaidOnly=0), id="zero"),
        pytest.param(node("unflagged", isPaidOnly="false"), id="string"),
        pytest.param(node("unflagged", isPaidOnly=False, paidOnly=True), id="flags-disagree"),
        pytest.param(node("unflagged", isPaidOnly=False, paidOnly=None), id="second-flag-null"),
    ],
)
def test_row_not_known_to_be_free_is_dropped(unknown: dict[str, Any]) -> None:
    questions = parse_saved_responses([wrap(node("free-one", questionFrontendId="1"), unknown)])

    assert [q.slug for q in questions] == ["free-one"]


def test_row_flagged_free_by_either_name_is_kept() -> None:
    by_is_paid_only = node("by-is-paid-only", questionFrontendId="1")
    by_paid_only = without_flag("by-paid-only") | {"paidOnly": False, "questionFrontendId": "2"}

    questions = parse_saved_responses([wrap(by_is_paid_only, by_paid_only)])

    assert [q.slug for q in questions] == ["by-is-paid-only", "by-paid-only"]


def test_daily_fixture_parses_to_one_question() -> None:
    questions = parse_saved_responses([load_fixture("leetcode_daily.json")])

    assert len(questions) == 1
    daily = questions[0]
    assert daily.id == 2913
    assert daily.slug == "subarrays-distinct-element-sum-of-squares-i"
    assert daily.difficulty == "easy"
    assert daily.topics == ("array", "hash-table", "binary-indexed-tree", "segment-tree")


def test_duplicates_across_payloads_collapse_to_one() -> None:
    questions = parse_saved_responses(
        [
            load_fixture("leetcode_question_list.json"),
            load_fixture("leetcode_problemset_v2.json"),
            load_fixture("leetcode_question_list.json"),
        ]
    )

    assert [q.slug for q in questions] == [
        "two-sum",
        "add-two-numbers",
        "longest-substring-without-repeating-characters",
    ]


def test_result_is_sorted_by_id_whatever_the_payload_order() -> None:
    questions = parse_saved_responses(
        [
            wrap(node("later", questionFrontendId="30")),
            wrap(node("sooner", questionFrontendId="4")),
        ]
    )

    assert [q.id for q in questions] == [4, 30]


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"titleSlug": ""}, id="empty-slug"),
        pytest.param({"titleSlug": "   "}, id="blank-slug"),
        pytest.param({"difficulty": "Impossible"}, id="unknown-difficulty"),
        pytest.param({"difficulty": None}, id="no-difficulty"),
        pytest.param({"title": ""}, id="no-title"),
        pytest.param({"questionFrontendId": "LCP 01"}, id="non-numeric-id"),
        pytest.param({"questionFrontendId": None}, id="no-id"),
    ],
)
def test_unusable_row_is_skipped(bad: dict[str, Any]) -> None:
    questions = parse_saved_responses(
        [wrap(node("keeper", questionFrontendId="1"), node("x", **bad))]
    )

    assert [q.slug for q in questions] == ["keeper"]


def test_object_without_a_title_slug_is_not_a_question() -> None:
    payload = {"data": {"matchedUser": {"username": "someone", "difficulty": "Easy"}}}

    assert parse_saved_responses([payload]) == []


def test_topics_ignore_tags_without_a_slug_and_repeats() -> None:
    questions = parse_saved_responses(
        [
            wrap(
                node(
                    "tagged",
                    topicTags=[{"slug": "array"}, {"name": "No Slug"}, {"slug": "array"}, "bare"],
                )
            )
        ]
    )

    assert questions[0].topics == ("array",)


def test_a_question_without_topic_tags_has_no_topics() -> None:
    node_without_tags = node("untagged")
    del node_without_tags["topicTags"]

    assert parse_saved_responses([wrap(node_without_tags)])[0].topics == ()


def test_related_questions_inside_a_question_are_not_collected() -> None:
    parent = node("parent", similarQuestions=[node("relative", isPaidOnly=None)])

    assert [q.slug for q in parse_saved_responses([wrap(parent)])] == ["parent"]


def test_non_json_containers_are_ignored() -> None:
    assert parse_saved_responses(["text", 3, None, [], {}]) == []


# ---------------------------------------------------------------------------
# the file the builder writes
# ---------------------------------------------------------------------------


def write_payload(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_build_script_writes_positional_rows(tmp_path: Path) -> None:
    out = tmp_path / "leetcode_free.json"

    code = main(
        [
            str(PRACTICE_FIXTURES / "leetcode_question_list.json"),
            str(PRACTICE_FIXTURES / "leetcode_problemset_v2.json"),
            str(PRACTICE_FIXTURES / "leetcode_daily.json"),
            "--out",
            str(out),
        ]
    )

    assert code == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert set(document) == {"generated_at", "source", "questions"}
    assert document["source"] == "leetcode.com/problemset (saved by hand)"
    assert document["generated_at"]
    assert document["questions"] == [
        [1, "two-sum", "Two Sum", "easy", ["array", "hash-table"]],
        [2, "add-two-numbers", "Add Two Numbers", "medium", ["linked-list", "math", "recursion"]],
        [
            3,
            "longest-substring-without-repeating-characters",
            "Longest Substring Without Repeating Characters",
            "medium",
            ["hash-table", "string", "sliding-window"],
        ],
        [
            2913,
            "subarrays-distinct-element-sum-of-squares-i",
            "Subarrays Distinct Element Sum of Squares I",
            "easy",
            ["array", "hash-table", "binary-indexed-tree", "segment-tree"],
        ],
    ]


def test_catalogue_never_keeps_statement_fields(tmp_path: Path) -> None:
    source = write_payload(
        tmp_path / "saved.json",
        wrap(
            node(
                "fictional-pair-sum",
                title="Fictional Pair Sum",
                content="<p>SECRET-STATEMENT-TEXT</p>",
                acRate=55.5,
                hints=["SECRET-HINT-TEXT"],
                likes=1234,
                exampleTestcases="1\n2",
            )
        ),
    )
    out = tmp_path / "out.json"

    assert main([str(source), "--out", str(out)]) == 0

    written = out.read_text(encoding="utf-8")
    for marker in (*STATEMENT_MARKERS, "likes", "exampleTestcases", "1234", "55.5"):
        assert marker not in written
    rows = json.loads(written)["questions"]
    assert rows == [[7, "fictional-pair-sum", "Fictional Pair Sum", "medium", ["array"]]]


def test_built_file_loads_back_as_the_same_questions(tmp_path: Path) -> None:
    out = tmp_path / "leetcode_free.json"
    saved = [load_fixture("leetcode_question_list.json"), load_fixture("leetcode_daily.json")]
    sources = [write_payload(tmp_path / f"saved-{i}.json", p) for i, p in enumerate(saved)]

    assert main([*map(str, sources), "--out", str(out)]) == 0

    assert load_catalogue(out) == tuple(parse_saved_responses(saved))


def test_build_script_creates_the_output_folder(tmp_path: Path) -> None:
    out = tmp_path / "data" / "practice" / "leetcode_free.json"

    code = main([str(PRACTICE_FIXTURES / "leetcode_question_list.json"), "--out", str(out)])

    assert code == 0
    assert out.exists()


def test_build_script_keeps_the_existing_file_when_nothing_free_was_found(tmp_path: Path) -> None:
    out = tmp_path / "leetcode_free.json"
    out.write_text("previous catalogue", encoding="utf-8")
    only_paid = write_payload(tmp_path / "paid.json", wrap(node("paid-one", isPaidOnly=True)))

    assert main([str(only_paid), "--out", str(out)]) == 1

    assert out.read_text(encoding="utf-8") == "previous catalogue"


def test_build_script_says_why_flagless_questions_were_not_kept(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "leetcode_free.json"
    unflagged = write_payload(tmp_path / "unflagged.json", wrap(without_flag("unflagged-one")))

    assert main([str(unflagged), "--out", str(out)]) == 1

    assert "isPaidOnly" in capsys.readouterr().err
    assert not out.exists()


def test_build_script_rejects_an_unreadable_saved_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "leetcode_free.json"
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")

    assert main([str(broken), "--out", str(out)]) == 1

    assert "broken.json" in capsys.readouterr().err
    assert not out.exists()


def test_build_script_rejects_a_missing_saved_file(tmp_path: Path) -> None:
    out = tmp_path / "leetcode_free.json"

    assert main([str(tmp_path / "nope.json"), "--out", str(out)]) == 1
    assert not out.exists()


# ---------------------------------------------------------------------------
# load_catalogue
# ---------------------------------------------------------------------------


def test_missing_file_loads_as_empty(tmp_path: Path) -> None:
    assert load_catalogue(tmp_path / "missing.json") == ()


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("{ not json", id="invalid-json"),
        pytest.param("[]", id="list-at-top"),
        pytest.param('{"questions": "many"}', id="questions-not-a-list"),
        pytest.param('{"generated_at": "x"}', id="no-questions"),
    ],
)
def test_malformed_file_loads_as_empty(tmp_path: Path, body: str) -> None:
    path = tmp_path / "leetcode_free.json"
    path.write_text(body, encoding="utf-8")

    assert load_catalogue(path) == ()


GOOD_ROW_BEFORE: list[Any] = [1, "before", "Before", "easy", ["array"]]
GOOD_ROW_AFTER: list[Any] = [3, "after", "After", "hard", ["tree"]]
QUESTIONS_AROUND_A_BAD_ROW = (
    Question(1, "before", "Before", "easy", ("array",)),
    Question(3, "after", "After", "hard", ("tree",)),
)


def write_rows(path: Path, rows: list[Any]) -> Path:
    path.write_text(json.dumps({"questions": rows}), encoding="utf-8")
    return path


def catalogue_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == catalogue.logger.name]


@pytest.mark.parametrize(
    "bad_row",
    [
        pytest.param([2, "two-sum", "Two Sum", "easy"], id="short-row"),
        pytest.param([2, "b", "B", "easy", ["array"], 9], id="long-row"),
        pytest.param([2, "b", "B", "trivial", ["array"]], id="bad-difficulty"),
        pytest.param(["2", "b", "B", "easy", ["array"]], id="string-id"),
        pytest.param([True, "b", "B", "easy", ["array"]], id="boolean-id"),
        pytest.param([2, "", "B", "easy", ["array"]], id="empty-slug"),
        pytest.param([2, "b", 7, "easy", ["array"]], id="title-not-a-string"),
        pytest.param([2, "b", "B", "easy", "array"], id="topics-not-a-list"),
        pytest.param([2, "b", "B", "easy", [3]], id="topic-not-a-string"),
        pytest.param("row", id="row-not-a-list"),
    ],
)
def test_bad_row_is_skipped_and_its_neighbours_load(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, bad_row: Any
) -> None:
    path = write_rows(tmp_path / "leetcode_free.json", [GOOD_ROW_BEFORE, bad_row, GOOD_ROW_AFTER])

    with caplog.at_level(logging.WARNING, logger=catalogue.logger.name):
        loaded = load_catalogue(path)

    assert loaded == QUESTIONS_AROUND_A_BAD_ROW
    assert len(catalogue_warnings(caplog)) == 1


def test_skipped_rows_are_counted_in_one_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rows = [GOOD_ROW_BEFORE, [2, "b", "B", "nope", []], "row", GOOD_ROW_AFTER]
    path = write_rows(tmp_path / "leetcode_free.json", rows)

    with caplog.at_level(logging.WARNING, logger=catalogue.logger.name):
        loaded = load_catalogue(path)
        load_catalogue(path)

    assert loaded == QUESTIONS_AROUND_A_BAD_ROW
    warnings = catalogue_warnings(caplog)
    assert len(warnings) == 1
    assert "skipped 2 malformed row(s) of 4" in warnings[0]


def test_file_of_only_bad_rows_loads_as_empty(tmp_path: Path) -> None:
    path = write_rows(tmp_path / "leetcode_free.json", ["row", [1, "a"]])

    assert load_catalogue(path) == ()


def test_clean_file_logs_nothing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = write_rows(tmp_path / "leetcode_free.json", [GOOD_ROW_BEFORE, GOOD_ROW_AFTER])

    with caplog.at_level(logging.DEBUG, logger=catalogue.logger.name):
        loaded = load_catalogue(path)

    assert loaded == QUESTIONS_AROUND_A_BAD_ROW
    assert catalogue_warnings(caplog) == []


def test_problem_with_the_file_is_logged_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "leetcode_free.json"
    path.write_text("{ not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=catalogue.logger.name):
        assert load_catalogue(path) == ()
        assert load_catalogue(path) == ()

    assert len([r for r in caplog.records if r.name == catalogue.logger.name]) == 1


def test_missing_file_is_logged_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "missing.json"

    with caplog.at_level(logging.WARNING, logger=catalogue.logger.name):
        load_catalogue(path)
        load_catalogue(path)

    assert len([r for r in caplog.records if r.name == catalogue.logger.name]) == 1


def test_loaded_catalogue_is_a_tuple_of_questions_in_file_order(tmp_path: Path) -> None:
    path = tmp_path / "leetcode_free.json"
    rows = [[1, "a", "A", "easy", ["array"]], [2, "b", "B", "hard", []]]
    path.write_text(json.dumps({"questions": rows}), encoding="utf-8")

    loaded = load_catalogue(path)

    assert loaded == (
        Question(1, "a", "A", "easy", ("array",)),
        Question(2, "b", "B", "hard", ()),
    )
    assert load_catalogue(path) is loaded


def test_catalogue_by_slug_indexes_the_default_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    questions = (Question(1, "a", "A", "easy", ("array",)), Question(2, "b", "B", "hard", ()))
    monkeypatch.setattr(catalogue, "load_catalogue", lambda: questions)

    indexed = catalogue_by_slug()

    assert indexed == {"a": questions[0], "b": questions[1]}
    assert isinstance(indexed, dict)


def test_catalogue_by_slug_is_empty_without_a_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalogue, "load_catalogue", lambda: ())

    assert catalogue_by_slug() == {}


def test_default_catalogue_path_is_the_checked_in_file() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert repo_root / "data" / "practice" / "leetcode_free.json" == CATALOGUE_PATH


# ---------------------------------------------------------------------------
# the checked-in file, once someone has built it
# ---------------------------------------------------------------------------


def test_shipped_catalogue_is_free_only_and_bounded() -> None:
    if not CATALOGUE_PATH.exists():
        pytest.skip("data/practice/leetcode_free.json has not been built yet")

    assert CATALOGUE_PATH.stat().st_size < MAX_CATALOGUE_BYTES
    document = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    assert document["questions"], "the shipped catalogue is empty"
    for row in document["questions"]:
        assert len(row) == ROW_FIELDS, row
        assert row[3] in ("easy", "medium", "hard"), row
    assert len(load_catalogue()) == len(document["questions"])


def test_track_topics_exist_in_catalogue() -> None:
    if not CATALOGUE_PATH.exists():
        pytest.skip("data/practice/leetcode_free.json has not been built yet")

    known = {topic for question in load_catalogue() for topic in question.topics}
    wanted = {topic for weights in TRACK_TOPICS.values() for topic in weights}

    assert wanted - known == set()


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


def test_practice_errors_are_distinct_exceptions() -> None:
    errors = (PracticeConflict, PracticeInvalid, PracticeNotFound)

    for error in errors:
        assert issubclass(error, Exception)
        assert str(error("already done today")) == "already done today"
    for first in errors:
        for second in errors:
            assert first is second or not issubclass(first, second)
