"""
tests/test_company_map_github_orgs.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Bengaluru GitHub organisations as a company source: the search walk (paging,
the 1,000-result cap, the cursor, rate limits) and the filter that keeps only
real companies. Fixtures are trimmed recordings of the real API
(``github_org_search.json``, ``github_orgs.json``).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from services.company_map import github_orgs
from services.company_map.github_orgs import Cursor, RateLimited

FIXTURES = Path(__file__).parent / "fixtures" / "company_map"


def _load(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def _org(login: str) -> dict[str, Any]:
    return copy.deepcopy(_load("github_orgs.json")[login])


def _good(**overrides: Any) -> dict[str, Any]:
    org = _org("SigNoz")
    org.update(overrides)
    return org


class TestParseSearchPage:
    def test_reads_logins_and_the_total(self) -> None:
        logins, total = github_orgs.parse_search_page(_load("github_org_search.json"))
        assert total == 639
        assert len(logins) == 14
        assert logins[:3] == ["scaleracademy", "SigNoz", "swiggy-private"]

    def test_only_organisations_and_valid_logins(self) -> None:
        page = {
            "total_count": 4,
            "items": [
                {"login": "acme", "type": "Organization"},
                {"login": "a-person", "type": "User"},
                {"login": "bad login!", "type": "Organization"},
                {"login": "-leading-dash", "type": "Organization"},
                {"type": "Organization"},
                "junk",
            ],
        }
        assert github_orgs.parse_search_page(page) == (["acme"], 4)

    @pytest.mark.parametrize("junk", [{}, {"items": "x"}, [], None, {"total_count": "n"}])
    def test_junk_is_an_empty_page(self, junk: Any) -> None:
        assert github_orgs.parse_search_page(junk) == ([], 0)


class TestParseOrg:
    def test_a_real_company_maps_exactly(self) -> None:
        company = github_orgs.parse_org(_org("SigNoz"))
        assert company is not None
        assert company.name == "SigNoz"
        assert company.domain == "signoz.io"
        assert company.website_url == "https://signoz.io/"
        assert company.github_org == "SigNoz"
        assert company.description == "Open source Observability Platform"
        assert company.sector == "devtools"
        assert company.stage is None and company.employee_count is None
        assert company.lat is None and company.people == ()
        assert company.source_metadata == {"github_org_followers": 719, "github_org_repos": 158}

    def test_a_blog_without_a_scheme_gets_one(self) -> None:
        company = github_orgs.parse_org(_org("Auquan"))
        assert company is not None
        assert company.website_url == "https://www.auquan.com"
        assert company.domain == "auquan.com"

    def test_a_multi_city_location_that_includes_bengaluru_counts(self) -> None:
        company = github_orgs.parse_org(_org("hotstar"))
        assert company is not None
        assert company.domain == "hotstar.com"
        assert company.name == "Jiostar"
        assert company.description is None  # the org has none; the sector stays unknown
        assert company.sector is None

    def test_no_blog_means_no_company(self) -> None:
        assert github_orgs.parse_org(_org("swiggy-private")) is None  # no blog and no repos
        assert github_orgs.parse_org(_good(blog=None)) is None
        assert github_orgs.parse_org(_good(blog="")) is None
        assert github_orgs.parse_org(_good(blog="   ")) is None

    @pytest.mark.parametrize(
        "blog",
        [
            "https://acme.github.io/",
            "acme.github.io",
            "https://acme.vercel.app",
            "https://www.linkedin.com/company/acme",
            "https://twitter.com/acme",
            "https://linktr.ee/acme",
            "https://github.com/acme",
            "https://discord.gg/abcdef",
            "https://bit.ly/3xyz",
            "https://www.pes.edu",
            "https://www.iisc.ac.in",
            "https://www.mit.edu.in",
            "https://karnataka.gov.in",
            "not a url at all",
            "javascript:alert(1)",
            "ftp://files.acme.com",
        ],
    )
    def test_a_blog_that_is_not_a_company_site_is_refused(self, blog: str) -> None:
        assert github_orgs.parse_org(_good(blog=blog)) is None

    def test_an_org_with_no_public_repo_is_refused(self) -> None:
        assert github_orgs.parse_org(_good(public_repos=0)) is None
        assert github_orgs.parse_org(_good(public_repos=None)) is None
        assert github_orgs.parse_org(_good(public_repos="3")) is None

    def test_needs_a_name_or_a_description(self) -> None:
        assert github_orgs.parse_org(_good(name=None, description=None)) is None
        assert github_orgs.parse_org(_good(name="", description="  ")) is None
        named = github_orgs.parse_org(_good(name="SigNoz Inc", description=None))
        assert named is not None and named.name == "SigNoz Inc"

    def test_a_description_alone_is_enough_and_the_login_is_the_name(self) -> None:
        company = github_orgs.parse_org(_good(name=None))
        assert company is not None
        assert company.name == "SigNoz"  # the login

    @pytest.mark.parametrize(
        ("login", "name", "description"),
        [
            ("dscpesu", "GDSC PESU", "Google Developer Student Clubs PES University"),
            ("glugmvit", "GLUG MVIT", None),
            ("acme-club", "Acme Developers Club", "We meet on Saturdays"),
            ("acme-nitk", "Acme", "The NITK chapter of the Acme community"),
            ("campus-dev", "Campus Dev", "Open source at the college"),
            ("dsc-bms", None, "DSC BMSCE"),
        ],
    )
    def test_student_clubs_and_college_chapters_are_not_employers(
        self, login: str, name: str | None, description: str | None
    ) -> None:
        assert github_orgs.parse_org(_good(login=login, name=name, description=description)) is None

    @pytest.mark.parametrize(
        ("login", "name", "description"),
        [
            ("unacademy", "Unacademy", "India's largest learning platform"),
            ("clubhouse-in", "Clubhouse Software", "Workflow tools"),
            ("campfire", "Campfire", "Team chat"),
            ("dscout", "dscout", "Remote research platform"),
        ],
    )
    def test_the_club_filter_matches_words_not_substrings(
        self, login: str, name: str, description: str
    ) -> None:
        assert github_orgs.parse_org(_good(login=login, name=name, description=description))

    def test_only_organisations(self) -> None:
        assert github_orgs.parse_org(_good(type="User")) is None
        assert github_orgs.parse_org(_good(type=None)) is None

    def test_the_location_must_still_say_bengaluru(self) -> None:
        # The search matches a word anywhere in the profile's location; the
        # org record is the one to trust.
        assert github_orgs.parse_org(_good(location="London, UK")) is None
        assert github_orgs.parse_org(_good(location=None)) is None
        assert github_orgs.parse_org(_good(location="Bangalore")) is not None

    def test_a_missing_login_is_refused(self) -> None:
        assert github_orgs.parse_org(_good(login="")) is None
        assert github_orgs.parse_org(_good(login="has space")) is None

    def test_the_email_is_never_read(self) -> None:
        company = github_orgs.parse_org(_good(email="someone@signoz.io"))
        assert company is not None
        assert "someone@signoz.io" not in repr(company)

    @pytest.mark.parametrize("junk", [None, [], "x", 3, {}])
    def test_junk_is_none(self, junk: Any) -> None:
        assert github_orgs.parse_org(junk) is None


def _search_transport(
    totals: dict[str, int], calls: list[tuple[str, int]], per_page: int = 100
) -> httpx.MockTransport:
    """A search API holding ``totals[location]`` orgs per query, named ``<loc>-<n>``."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        location = query.split("location:")[1]
        page = int(request.url.params["page"])
        calls.append((location, page))
        assert request.url.params["per_page"] == "100"
        assert "type:org" in query
        assert request.url.params["sort"] == "followers"
        total = totals[location]
        if page > 10:
            return httpx.Response(422, json={"message": "Only the first 1000 search results"})
        first = (page - 1) * per_page
        items = [
            {"login": f"{location}-{n}", "type": "Organization"}
            for n in range(first, min(first + per_page, total, 1000))
        ]
        return httpx.Response(200, json={"total_count": total, "items": items})

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the search throttle instead of sleeping through it."""
    pauses: list[float] = []

    async def pause(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(github_orgs, "_sleep", pause)
    return pauses


class TestWalk:
    async def test_first_run_takes_the_head_of_the_first_query(self) -> None:
        calls: list[tuple[str, int]] = []
        async with httpx.AsyncClient(
            transport=_search_transport({"Bengaluru": 639, "Bangalore": 300}, calls)
        ) as client:
            found = await github_orgs.fetch_candidates(client, Cursor(), 60)
        assert [c.login for c in found.candidates] == [f"Bengaluru-{n}" for n in range(60)]
        assert found.candidates[-1].next == Cursor(0, 60)
        assert found.cursor == Cursor(0, 60)
        assert calls == [("Bengaluru", 1)]  # one request is all 60 need

    async def test_the_next_run_resumes_mid_page_and_crosses_into_the_next(self) -> None:
        calls: list[tuple[str, int]] = []
        async with httpx.AsyncClient(
            transport=_search_transport({"Bengaluru": 639, "Bangalore": 300}, calls)
        ) as client:
            found = await github_orgs.fetch_candidates(client, Cursor(0, 60), 60)
        assert [c.login for c in found.candidates] == [f"Bengaluru-{n}" for n in range(60, 120)]
        assert found.cursor == Cursor(0, 120)
        assert calls == [("Bengaluru", 1), ("Bengaluru", 2)]

    async def test_a_page_is_not_fetched_for_a_cursor_that_is_past_it(self) -> None:
        calls: list[tuple[str, int]] = []
        async with httpx.AsyncClient(
            transport=_search_transport({"Bengaluru": 639, "Bangalore": 300}, calls)
        ) as client:
            await github_orgs.fetch_candidates(client, Cursor(0, 300), 10)
        assert calls == [("Bengaluru", 4)]

    async def test_the_end_of_a_query_moves_to_the_next_one(self) -> None:
        calls: list[tuple[str, int]] = []
        async with httpx.AsyncClient(
            transport=_search_transport({"Bengaluru": 639, "Bangalore": 300}, calls)
        ) as client:
            found = await github_orgs.fetch_candidates(client, Cursor(0, 620), 60)
        logins = [c.login for c in found.candidates]
        assert logins[:19] == [f"Bengaluru-{n}" for n in range(620, 639)]
        assert logins[19:] == [f"Bangalore-{n}" for n in range(41)]
        # The last Bengaluru org already points at the start of the next query.
        assert found.candidates[18].next == Cursor(1, 0)
        assert found.cursor == Cursor(1, 41)

    async def test_the_last_query_ending_wraps_to_a_fresh_cycle(self) -> None:
        calls: list[tuple[str, int]] = []
        async with httpx.AsyncClient(
            transport=_search_transport({"Bengaluru": 639, "Bangalore": 300}, calls)
        ) as client:
            found = await github_orgs.fetch_candidates(client, Cursor(1, 290), 60)
        assert [c.login for c in found.candidates] == [f"Bangalore-{n}" for n in range(290, 300)]
        assert found.cursor == Cursor(0, 0)
        assert found.wrapped

    async def test_stops_at_the_1000_result_cap(self) -> None:
        calls: list[tuple[str, int]] = []
        async with httpx.AsyncClient(
            transport=_search_transport({"Bengaluru": 4000, "Bangalore": 5}, calls)
        ) as client:
            found = await github_orgs.fetch_candidates(client, Cursor(0, 980), 60)
        logins = [c.login for c in found.candidates]
        assert logins[:20] == [f"Bengaluru-{n}" for n in range(980, 1000)]
        assert logins[20:25] == [f"Bangalore-{n}" for n in range(5)]
        assert ("Bengaluru", 11) not in calls
        assert found.candidates[19].next == Cursor(1, 0)

    async def test_an_empty_query_is_passed_over(self) -> None:
        calls: list[tuple[str, int]] = []
        async with httpx.AsyncClient(
            transport=_search_transport({"Bengaluru": 0, "Bangalore": 3}, calls)
        ) as client:
            found = await github_orgs.fetch_candidates(client, Cursor(), 60)
        assert [c.login for c in found.candidates] == ["Bangalore-0", "Bangalore-1", "Bangalore-2"]

    async def test_the_limit_is_a_ceiling_on_candidates(self) -> None:
        async with httpx.AsyncClient(
            transport=_search_transport({"Bengaluru": 639, "Bangalore": 300}, [])
        ) as client:
            found = await github_orgs.fetch_candidates(client, Cursor(), 7)
        assert len(found.candidates) == 7

    async def test_one_org_listed_under_both_spellings_is_one_candidate(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            items = [
                {"login": "Acme", "type": "Organization"},
                {"login": "Other", "type": "Organization"},
            ]
            return httpx.Response(200, json={"total_count": 2, "items": items})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            found = await github_orgs.fetch_candidates(client, Cursor(), 60)
        assert [c.login for c in found.candidates] == ["Acme", "Other"]

    async def test_search_requests_are_throttled(self, _no_pause: list[float]) -> None:
        async with httpx.AsyncClient(
            transport=_search_transport({"Bengaluru": 639, "Bangalore": 300}, [])
        ) as client:
            await github_orgs.fetch_candidates(client, Cursor(0, 60), 60)
        # Two search requests: one pause between them, none before the first.
        assert len(_no_pause) == 1
        assert _no_pause[0] >= 2.0

    async def test_the_unauthenticated_pace_stays_inside_ten_a_minute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(github_orgs, "_has_token", lambda: False)
        assert github_orgs._search_gap() >= 6.0
        monkeypatch.setattr(github_orgs, "_has_token", lambda: True)
        assert github_orgs._search_gap() >= 2.0

    async def test_rate_limited_search_returns_what_it_has(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["page"])
            calls.append(page)
            if page == 2:
                return httpx.Response(
                    403,
                    headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1792043168"},
                    json={"message": "API rate limit exceeded"},
                )
            items = [{"login": f"o{n}", "type": "Organization"} for n in range(100)]
            return httpx.Response(200, json={"total_count": 639, "items": items})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            found = await github_orgs.fetch_candidates(client, Cursor(), 150)
        assert found.rate_limited
        assert len(found.candidates) == 100
        assert found.cursor == Cursor(0, 100)

    async def test_other_search_failures_raise(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await github_orgs.fetch_candidates(client, Cursor(), 10)


class TestCursor:
    def test_round_trips(self) -> None:
        assert Cursor.decode(Cursor(1, 340).encode()) == Cursor(1, 340)

    @pytest.mark.parametrize(
        "junk", [None, "", "x", "1", "a:b", "-1:0", "0:-5", "9:0", {"a": 1}, 3]
    )
    def test_junk_starts_a_fresh_walk(self, junk: Any) -> None:
        assert Cursor.decode(junk) == Cursor()


class TestFetchOrg:
    async def test_returns_the_org(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/orgs/SigNoz"
            assert request.headers["accept"] == "application/vnd.github+json"
            return httpx.Response(200, json=_org("SigNoz"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            org = await github_orgs.fetch_org(client, "SigNoz")
        assert org is not None and org["login"] == "SigNoz"

    async def test_a_missing_org_is_none(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await github_orgs.fetch_org(client, "gone") is None

    async def test_a_bad_login_is_never_requested(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not be requested")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await github_orgs.fetch_org(client, "../users/x") is None

    @pytest.mark.parametrize("status", [403, 429])
    async def test_a_rate_limit_raises_so_the_step_can_stop(self, status: int) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(status, headers={"x-ratelimit-remaining": "0"})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(RateLimited):
                await github_orgs.fetch_org(client, "SigNoz")

    async def test_a_server_error_raises(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(502))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await github_orgs.fetch_org(client, "SigNoz")

    async def test_a_body_that_is_not_an_object_is_none(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[1, 2]))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await github_orgs.fetch_org(client, "SigNoz") is None
