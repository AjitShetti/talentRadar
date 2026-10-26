"""Which rows scripts/clean_query_skills.py picks: search words, never a posting's own skills."""

from __future__ import annotations

from scripts.clean_query_skills import Row, pick

QUERY = ("What", "should", "focus", "today?")


def _row(id_: str, source: str, company: str, skills: tuple[str, ...], status: str = "raw") -> Row:
    return Row(id_, f"ext-{id_}", source, company, status, skills)


def test_a_raw_row_from_a_source_without_skills_is_certain() -> None:
    assert pick([_row("1", "greenhouse:stripe", "c1", QUERY)]) == {"1": "certain"}
    assert pick([_row("2", "linkedin", "c1", ("python",))]) == {"2": "certain"}


def test_one_list_across_companies_is_shared() -> None:
    rows = [
        _row("1", "naukri", "c1", QUERY),
        _row("2", "foundit", "c2", QUERY, status="enriched"),
    ]
    assert pick(rows) == {"1": "shared", "2": "shared"}


def test_a_board_or_parsed_list_of_one_company_is_kept() -> None:
    rows = [
        _row("1", "foundit", "c1", ("Java", "Spring")),
        _row("2", "foundit", "c1", ("Java", "Spring")),
        _row("3", "greenhouse:gitlab", "c2", ("Go", "Postgres"), status="enriched"),
    ]
    assert pick(rows) == {}


def test_batch_pipeline_and_seed_rows_are_never_touched() -> None:
    rows = [
        _row("1", "greenhouse", "c1", QUERY),
        _row("2", "tavily", "c2", QUERY),
        _row("3", "lever", "c3", QUERY),
    ]
    assert pick(rows) == {}
