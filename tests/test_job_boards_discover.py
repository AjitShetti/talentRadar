"""
tests/test_job_boards_discover.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Finding boards for the roster from the open list and by name probe
(``services/job_boards/discover.py``).

Nothing here touches the network: every request is answered by ``httpx.MockTransport``, the
open list from ``tests/fixtures/boards/open_list_sample.csv`` and the ATSes by a small fake
that speaks the three response shapes the probe reads. The database is an in-memory SQLite
built from the real models, and time is a recording ``sleep``. Each test names a rule:

* the open list keeps the nine ATSes only, lower-cases every slug but Lever's and Ashby's,
  drops an unusable slug, and reads Personio's host from the row; a second run inserts nothing
  and never resets a board already on the roster
* the list is bounded: rows, bytes, redirects and a broken stream all end in ``OpenListError``,
  and the rows read before that stay
* the probe keeps a board only with an India role and, where the ATS states a name, the
  company's own name; it is paced, capped at 24 requests, and stops asking an ATS that says 429
* no database transaction is open while a request is in flight
* the company map's `sites` step files the boards a careers page links under that company, and
  a careers URL that is itself a board is used as a string, never requested
* the YC list keeps India only (a location or a region; Indianapolis is not India), reads each
  site once per 30 days through the company map's site reader, probes the ones whose site names
  no board once per 90 days, and never makes a `companies` row

Every company, board and role is fictional, apart from the two the brief names (Groww and Slice).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from domain.job_boards import PACE_SECONDS, PROBE_ATS, BoardRef
from services.cache_backend import CacheBackend
from services.company_map import refresh, website
from services.company_map.types import SiteFacts
from services.job_boards import discover
from services.job_boards.discover import (
    MAX_YC_BYTES,
    OpenListError,
    YcListError,
    discover_open_list,
    discover_probe,
    discover_yc,
    probe_company,
    probed_key,
    yc_site_key,
)
from storage.database import Base
from storage.models import Company, JobBoard

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "boards" / "open_list_sample.csv"
LIST_URL = "https://raw.githubusercontent.com/storesignalhq/ats-job-boards/main/boards.csv"
USER_AGENT = "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"

INDIA = ("Bengaluru", "India")
ABROAD = ("Skopje", "North Macedonia")

#: What the sample keeps: 16 of its 20 rows (not smartrecruiters, not taleo, not the dotted slug).
SAMPLE_KEPT = {
    ("greenhouse", "northwindlabs", None),
    ("greenhouse", "contoso-systems", None),
    ("greenhouse", "fabrikam", None),
    ("ashby", "AdventureWorks", None),
    ("ashby", "wingtip", None),
    ("ashby", "litware", None),
    ("lever", "Proseware", None),
    ("lever", "woodgrove", None),
    ("workable", "coho-winery", None),
    ("workable", "lucerne-publishing", None),
    ("recruitee", "fourthcoffee", None),
    ("recruitee", "margiestravel", None),
    ("keka", "bellows-college", None),
    ("personio", "alpineski", "com"),
    ("personio", "munichbakery", "de"),
    ("personio", "graphicdesign", "de"),
}


# -- the database, the cache and the clock ----------------------------------------------------


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Production's flags: nothing flushes on its own, and a commit expires nothing.
    yield async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    await engine.dispose()


@pytest_asyncio.fixture
async def session(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as opened:
        yield opened


@pytest.fixture(autouse=True)
async def memory_cache() -> AsyncIterator[None]:
    await CacheBackend.reset()
    CacheBackend._client = None
    CacheBackend._initialised = True
    yield
    await CacheBackend.reset()


class Sleeper:
    """A ``sleep`` that records what it was asked for and waits for nothing."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


async def boards_of(factory: async_sessionmaker[AsyncSession]) -> list[JobBoard]:
    async with factory() as reader:
        return list((await reader.execute(select(JobBoard))).scalars().all())


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# -- the open list ------------------------------------------------------------------------------


@dataclass
class ListServer:
    """Serves one body at the list's URL and remembers what was asked."""

    body: bytes = b""
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, content=self.body, headers=self.headers)


def sample_server() -> ListServer:
    return ListServer(body=SAMPLE.read_bytes())


def chunked(*parts: bytes, then_raise: Exception | None = None) -> Callable[..., httpx.Response]:
    """A handler whose body arrives as these chunks (so it has no Content-Length), then maybe fails."""

    async def body() -> AsyncIterator[bytes]:
        for part in parts:
            yield part
        if then_raise is not None:
            raise then_raise

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body())

    return handler


async def test_open_list_keeps_supported_ats_only(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    server = sample_server()
    async with client_for(server) as client:
        result = await discover_open_list(session, client, limit=1000)

    assert result == {"rows": 20, "kept": 16, "inserted": 16}
    stored = await boards_of(factory)
    assert {(b.ats, b.board, b.region) for b in stored} == SAMPLE_KEPT
    assert {b.status for b in stored} == {"candidate"}
    assert {b.discovered_via for b in stored} == {"open_list"}
    assert {b.company_id for b in stored} == {None}
    names = {(b.ats, b.board): b.company_name for b in stored}
    assert names[("greenhouse", "contoso-systems")] == "Contoso Systems, Inc."
    assert names[("ashby", "AdventureWorks")] is None  # the list names no company for it
    assert names[("recruitee", "margiestravel")] == "Margie's Travel"

    (request,) = server.requests
    assert str(request.url) == LIST_URL
    assert request.headers["user-agent"] == USER_AGENT


async def test_open_list_reupsert_is_noop(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with client_for(sample_server()) as client:
        await discover_open_list(session, client, limit=1000)
        stored = await boards_of(factory)
        blocked = next(b for b in stored if b.board == "wingtip")
        async with factory() as other:
            row = await other.get(JobBoard, blocked.id)
            assert row is not None
            row.status = "blocked"
            await other.commit()

        second = await discover_open_list(session, client, limit=1000)

    assert second == {"rows": 20, "kept": 16, "inserted": 0}
    again = await boards_of(factory)
    assert len(again) == 16
    # Being found again never revives a board the sweep gave up on.
    assert next(b for b in again if b.board == "wingtip").status == "blocked"


async def test_open_list_limit_caps_the_rows_read(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with client_for(sample_server()) as client:
        result = await discover_open_list(session, client, limit=5)

    assert result == {"rows": 5, "kept": 4, "inserted": 4}  # the fourth row has a dotted slug
    assert len(await boards_of(factory)) == 4


async def test_open_list_reads_the_url_from_settings(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        discover,
        "get_settings",
        lambda: SimpleNamespace(job_boards_open_list_url="https://mirror.example/boards.csv"),
    )
    server = sample_server()
    async with client_for(server) as client:
        await discover_open_list(session, client, limit=1)

    assert [str(r.url) for r in server.requests] == ["https://mirror.example/boards.csv"]


async def test_open_list_reads_crlf_bom_and_a_quoted_name_with_a_line_break(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    text = (
        "﻿ats,slug,company_name,open_jobs\r\n"
        'greenhouse,northwindlabs,"Northwind\nLabs  ""NW""",4\r\n'
        "ashby,wingtip,Wingtip,2\r\n"
        "\r\n"
        "lever,woodgrove,Woodgrove,1"  # no final newline
    )
    async with client_for(ListServer(body=text.encode())) as client:
        result = await discover_open_list(session, client, limit=100)

    assert result == {"rows": 3, "kept": 3, "inserted": 3}
    names = {b.board: b.company_name for b in await boards_of(factory)}
    assert names == {
        "northwindlabs": 'Northwind Labs "NW"',  # one line, whitespace collapsed
        "wingtip": "Wingtip",
        "woodgrove": "Woodgrove",
    }


async def test_open_list_bounds_what_a_row_can_put_in_the_table(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    long_name = "N" * 500
    text = (
        "ats,slug,company_name\n"
        f"greenhouse,northwindlabs,{long_name}\n"
        "greenhouse,x/../y,Slash\n"
        "greenhouse,,Empty\n"
        f"greenhouse,{'a' * 81},TooLong\n"
        "greenhouse,ok,\x07Control\x1b\n"
    )
    async with client_for(ListServer(body=text.encode())) as client:
        result = await discover_open_list(session, client, limit=100)

    assert result == {"rows": 5, "kept": 2, "inserted": 2}
    names = {b.board: b.company_name for b in await boards_of(factory)}
    assert names == {"northwindlabs": "N" * 200, "ok": "Control"}


@pytest.mark.parametrize("status", [301, 302, 404, 429, 500])
async def test_open_list_refuses_a_redirect_or_an_error_status(
    session: AsyncSession, status: int
) -> None:
    server = ListServer(body=b"x", status=status, headers={"location": "https://evil.example/x"})
    # Even a client set to follow redirects must not follow one here.
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(server), follow_redirects=True
    ) as client:
        with pytest.raises(OpenListError):
            await discover_open_list(session, client, limit=100)

    assert len(server.requests) == 1  # the redirect was not followed


async def test_open_list_refuses_an_unknown_layout(session: AsyncSession) -> None:
    async with client_for(ListServer(body=b"name,url\nNorthwind,https://x.example\n")) as client:
        with pytest.raises(OpenListError):
            await discover_open_list(session, client, limit=100)
    async with client_for(ListServer(body=b"")) as client:
        with pytest.raises(OpenListError):
            await discover_open_list(session, client, limit=100)


async def test_open_list_over_the_cap_is_refused_declared_or_streamed(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discover, "MAX_LIST_BYTES", 400)
    body = SAMPLE.read_bytes()

    async with client_for(ListServer(body=body)) as client:  # Content-Length says so
        with pytest.raises(OpenListError):
            await discover_open_list(session, client, limit=1000)
    assert await boards_of(factory) == []

    lines = body.splitlines(keepends=True)
    async with client_for(chunked(*lines)) as client:  # no Content-Length: counted as it arrives
        with pytest.raises(OpenListError):
            await discover_open_list(session, client, limit=1000)
    # Rows read before the cap stay: each is independent, and the next run adds the rest.
    stored = await boards_of(factory)
    assert 0 < len(stored) < 16


async def test_open_list_a_broken_stream_keeps_the_rows_read(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    lines = SAMPLE.read_bytes().splitlines(keepends=True)
    handler = chunked(*lines[:4], then_raise=httpx.ReadError("connection reset"))
    async with client_for(handler) as client:
        with pytest.raises(OpenListError):
            await discover_open_list(session, client, limit=1000)

    assert {b.board for b in await boards_of(factory)} == {
        "northwindlabs",
        "contoso-systems",
        "fabrikam",
    }


async def test_open_list_a_line_that_is_not_a_row_stops_the_read(session: AsyncSession) -> None:
    handler = chunked(b"ats,slug\n", b"greenhouse," + b"x" * (70 * 1024))
    async with client_for(handler) as client:
        with pytest.raises(OpenListError):
            await discover_open_list(session, client, limit=100)


async def test_open_list_commits_in_batches_and_holds_no_transaction_between_them(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discover, "BATCH_SIZE", 2)
    in_transaction: list[bool] = []
    committed: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        yield b"ats,slug,company_name\ngreenhouse,one,One\ngreenhouse,two,Two\n"
        # The two rows above filled a batch: it is committed before the next chunk is read.
        in_transaction.append(session.in_transaction())
        committed.append(len(await boards_of(factory)))
        yield b"greenhouse,three,Three\ngreenhouse,four,Four\ngreenhouse,five,Five\n"
        in_transaction.append(session.in_transaction())
        committed.append(len(await boards_of(factory)))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body())

    async with client_for(handler) as client:
        result = await discover_open_list(session, client, limit=100)

    assert result == {"rows": 5, "kept": 5, "inserted": 5}
    assert in_transaction == [False, False]
    assert committed == [2, 4]  # the fifth waits for the final flush
    assert len(await boards_of(factory)) == 5
    assert not session.in_transaction()


async def test_open_list_ends_the_callers_transaction_before_it_downloads(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    # The caller has read something, so its session holds a transaction (and a connection).
    await session.execute(select(JobBoard))
    assert session.in_transaction()
    during: list[bool] = []

    async def body() -> AsyncIterator[bytes]:
        during.append(session.in_transaction())  # the response has started
        yield b"ats,slug,company_name\n"
        during.append(session.in_transaction())  # mid-download, before any batch is written
        yield b"greenhouse,one,One\n"

    def handler(_request: httpx.Request) -> httpx.Response:
        during.append(session.in_transaction())  # the request is being answered
        return httpx.Response(200, content=body())

    async with client_for(handler) as client:
        result = await discover_open_list(session, client, limit=100)

    assert during == [False, False, False]
    assert result == {"rows": 1, "kept": 1, "inserted": 1}
    assert len(await boards_of(factory)) == 1


# -- the name probe: a fake web -----------------------------------------------------------------


def _place(city_country: tuple[str, str]) -> str:
    return f"{city_country[0]}, {city_country[1]}"


@dataclass
class FakeWeb:
    """
    Just enough of five ATSes for the probe: Greenhouse, Ashby, Workable, Recruitee and Keka by board id.

    ``greenhouse["slice"] = ("Slice", [INDIA])`` is a board named "Slice" with one Bengaluru role
    (a ``None`` name answers 404 on the name endpoint; on Recruitee it is an offer that states no
    ``company_name``). The board id ``"*"`` answers for any id. Anything not listed is a 404, as
    on the real ATSes. ``status`` overrides the answer of a host.
    """

    greenhouse: dict[str, tuple[str | None, list[tuple[str, str]]]] = field(default_factory=dict)
    ashby: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    workable: dict[str, tuple[str | None, list[tuple[str, str]]]] = field(default_factory=dict)
    recruitee: dict[str, tuple[str | None, list[tuple[str, str]]]] = field(default_factory=dict)
    keka: dict[str, tuple[str | None, list[tuple[str, str]]]] = field(default_factory=dict)
    status: dict[str, int] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    on_request: Callable[[httpx.Request], None] | None = None

    @staticmethod
    def _find(table: dict[str, Any], board: str) -> Any:
        return table.get(board, table.get("*"))

    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.on_request is not None:
            self.on_request(request)
        host, path = request.url.host, request.url.path
        if host in self.status:
            return httpx.Response(self.status[host], text="slow down")

        if host == "boards-api.greenhouse.io" and path.startswith("/v1/boards/"):
            parts = path.removeprefix("/v1/boards/").split("/")
            board = self._find(self.greenhouse, parts[0])
            if board is None:
                return httpx.Response(404)
            name, roles = board
            if parts[1:] == ["jobs"]:
                return httpx.Response(
                    200,
                    json={
                        "jobs": [
                            {
                                "id": 100 + n,
                                "title": f"Engineer {n}",
                                "absolute_url": f"https://boards.greenhouse.io/{parts[0]}/jobs/{100 + n}",
                                "location": {"name": _place(place)},
                            }
                            for n, place in enumerate(roles)
                        ]
                    },
                )
            if len(parts) == 1:
                return httpx.Response(200, json={"name": name}) if name else httpx.Response(404)

        if host == "api.ashbyhq.com" and path.startswith("/posting-api/job-board/"):
            roles = self._find(self.ashby, path.rsplit("/", 1)[1])
            if roles is None:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": f"a-{n}",
                            "title": f"Engineer {n}",
                            "jobUrl": f"https://jobs.ashbyhq.com/x/a-{n}",
                            "location": _place(place),
                        }
                        for n, place in enumerate(roles)
                    ]
                },
            )

        if host == "apply.workable.com" and path.startswith("/api/v1/widget/accounts/"):
            board = self._find(self.workable, path.rsplit("/", 1)[1])
            if board is None:
                return httpx.Response(404)
            name, roles = board
            return httpx.Response(
                200,
                json={
                    "name": name,
                    "jobs": [
                        {
                            "title": f"Engineer {n}",
                            "shortcode": f"ABCDEF{n:04d}",
                            "locations": [{"city": place[0], "country": place[1]}],
                        }
                        for n, place in enumerate(roles)
                    ],
                },
            )

        if host.endswith(".recruitee.com") and path == "/api/offers/":
            board = self._find(self.recruitee, host.removesuffix(".recruitee.com"))
            if board is None:
                return httpx.Response(404)
            name, roles = board
            return httpx.Response(
                200,
                json={
                    "offers": [
                        {
                            "id": 500 + n,
                            "title": f"Engineer {n}",
                            "slug": f"engineer-{n}",
                            "locations": [{"city": place[0], "country": place[1]}],
                            **({"company_name": name} if name else {}),
                        }
                        for n, place in enumerate(roles)
                    ]
                },
            )

        if host.endswith(".keka.com") and path.startswith("/careers/api/"):
            board = self._find(self.keka, host.removesuffix(".keka.com"))
            if board is None:
                return httpx.Response(404)
            name, roles = board
            if path.endswith("/jobs/default/active"):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": 10 + n,
                            "title": f"Engineer {n}",
                            "jobLocations": [{"city": place[0], "countryName": place[1]}],
                        }
                        for n, place in enumerate(roles)
                    ],
                )
            if path.endswith("/careerportalinfo"):
                return httpx.Response(200, json={"name": name}) if name else httpx.Response(404)

        return httpx.Response(404)


# -- the name probe ---------------------------------------------------------------------------


async def test_probe_accepts_groww() -> None:
    web = FakeWeb(greenhouse={"groww": ("Groww", [INDIA, INDIA, ABROAD])})
    sleeper = Sleeper()
    async with client_for(web) as client:
        found = await probe_company(client, name="Groww", domain="groww.in", sleep=sleeper)

    assert found == BoardRef("greenhouse", "groww")
    # The board, then its name: two requests to one host, so one wait. Nothing else was asked.
    assert web.hosts() == ["boards-api.greenhouse.io"] * 2
    assert sleeper.calls == [PACE_SECONDS["greenhouse"]]


async def test_probe_accepts_an_unnamed_ats_board_that_is_the_domains_label() -> None:
    web = FakeWeb(ashby={"cred": [INDIA]})
    async with client_for(web) as client:
        found = await probe_company(client, name="CRED", domain="cred.club", sleep=Sleeper())

    assert found == BoardRef("ashby", "cred")


async def test_probe_unnamed_ats_needs_domain_label() -> None:
    # Ashby states no company name, so an India role alone does not make a board the company's.
    # Observe.AI's site is observe.ai: "observe" is the id its own domain names, "observeai" is
    # only a guess from its name.
    guessed = FakeWeb(ashby={"observeai": [INDIA]})
    async with client_for(guessed) as client:
        assert (
            await probe_company(client, name="Observe.AI", domain="observe.ai", sleep=Sleeper())
            is None
        )
    # The "observeai" board was read, and then not trusted.
    assert any(r.url.path.endswith("/observeai") for r in guessed.requests)

    own_label = FakeWeb(ashby={"observe": [INDIA]})
    async with client_for(own_label) as client:
        found = await probe_company(
            client, name="Observe.AI", domain="https://www.observe.ai/careers", sleep=Sleeper()
        )
    assert found == BoardRef("ashby", "observe")


async def test_probe_unnamed_ats_without_a_domain_is_a_miss() -> None:
    # Another company's "slice" board with India roles must not be filed under Slice.
    web = FakeWeb(ashby={"slice": [INDIA]}, recruitee={"slice": (None, [INDIA])})
    async with client_for(web) as client:
        assert await probe_company(client, name="Slice", domain=None, sleep=Sleeper()) is None
        assert (
            await probe_company(client, name="Slice", domain="slicepay.in", sleep=Sleeper()) is None
        )
        found = await probe_company(client, name="Slice", domain="slice.in", sleep=Sleeper())

    assert found == BoardRef("ashby", "slice")  # the same board, once the company's site names it


async def test_probe_recruitee_accepts_stated_name() -> None:
    # Recruitee's offers state the company: a matching name is accepted whatever the guess.
    web = FakeWeb(recruitee={"acmerobotics": ("Acme Robotics Pvt Ltd", [INDIA])})
    async with client_for(web) as client:
        found = await probe_company(client, name="Acme Robotics", domain="acme.in", sleep=Sleeper())

    assert found == BoardRef("recruitee", "acmerobotics")


async def test_probe_recruitee_rejects_another_stated_name() -> None:
    web = FakeWeb(recruitee={"acmerobotics": ("Acme Foods", [INDIA])})
    async with client_for(web) as client:
        found = await probe_company(client, name="Acme Robotics", domain="acme.in", sleep=Sleeper())

    assert found is None


async def test_probe_recruitee_default_name_is_not_evidence() -> None:
    # An offer that states no company is filed under "Acme", the board id capitalised. That is the
    # guess read back, not a name the board gave, so it cannot confirm the guess.
    web = FakeWeb(recruitee={"acme": (None, [INDIA])})
    async with client_for(web) as client:
        found = await probe_company(client, name="Acme", domain="acmeinc.com", sleep=Sleeper())

    assert found is None


async def test_probe_rejects_board_without_india_roles() -> None:
    web = FakeWeb(greenhouse={"slice": ("Slice", [ABROAD, ABROAD])})
    async with client_for(web) as client:
        found = await probe_company(client, name="Slice", domain=None, sleep=Sleeper())

    assert found is None
    # No India role, so the board's name was not even asked for: the one Greenhouse request is
    # the board itself.
    assert [r.url.path for r in web.requests if r.url.host == "boards-api.greenhouse.io"] == [
        "/v1/boards/slice/jobs"
    ]


async def test_probe_rejects_name_mismatch() -> None:
    web = FakeWeb(
        greenhouse={"slice": ("Slice Pizza Holdings", [INDIA])},
        workable={"slice": ("Slice Studio", [INDIA])},
        keka={"slice": ("Slice Dental", [INDIA])},
    )
    async with client_for(web) as client:
        found = await probe_company(client, name="Slice", domain="slice.in", sleep=Sleeper())

    assert found is None


async def test_probe_rejects_a_named_ats_whose_name_cannot_be_read() -> None:
    # Greenhouse's name endpoint and Keka's portal answer 404, Workable's account has no name:
    # an India role alone does not show these are this company's.
    web = FakeWeb(
        greenhouse={"slice": (None, [INDIA])},
        workable={"slice": (None, [INDIA])},
        keka={"slice": (None, [INDIA])},
    )
    async with client_for(web) as client:
        found = await probe_company(client, name="Slice", domain=None, sleep=Sleeper())

    assert found is None


async def test_probe_compares_names_the_way_the_company_map_does() -> None:
    web = FakeWeb(workable={"slice": ("Slice Technologies Private Limited", [INDIA])})
    async with client_for(web) as client:
        found = await probe_company(
            client, name="Slice Technologies Pvt. Ltd.", domain="slice.in", sleep=Sleeper()
        )

    assert found == BoardRef("workable", "slice")


async def test_probe_goes_on_to_the_next_guess_after_a_rejected_board() -> None:
    web = FakeWeb(
        greenhouse={
            "acme": ("Acme Foods", [INDIA]),
            "acmerobotics": ("Acme Robotics", [INDIA]),
        }
    )
    async with client_for(web) as client:
        found = await probe_company(client, name="Acme Robotics", domain="acme.in", sleep=Sleeper())

    assert found == BoardRef("greenhouse", "acmerobotics")


async def test_probe_tries_the_likeliest_guess_on_every_ats_first() -> None:
    web = FakeWeb(
        greenhouse={"acmerobotics": ("Acme Robotics", [INDIA])},
        ashby={"acme": [INDIA]},
    )
    async with client_for(web) as client:
        found = await probe_company(client, name="Acme Robotics", domain="acme.in", sleep=Sleeper())

    assert found == BoardRef("ashby", "acme")  # guess 1 on Ashby beats guess 2 on Greenhouse


async def test_probe_paces_every_request_to_a_host_it_has_already_asked() -> None:
    web = FakeWeb()  # everything is a 404
    sleeper = Sleeper()
    async with client_for(web) as client:
        found = await probe_company(client, name="Acme Robotics", domain="acme.in", sleep=sleeper)

    assert found is None
    # Three guesses on six ATSes: the first request to each host is free, the other two wait.
    assert len(web.requests) == 18
    assert sorted(sleeper.calls) == sorted(PACE_SECONDS[ats] for ats in PROBE_ATS for _ in range(2))


async def test_probe_request_budget() -> None:
    # The worst case: every guess finds a Greenhouse board with an India role and another
    # company's name (two requests) and a Keka one with the same (two requests), on top of one
    # request each to the other four ATSes. That is 8 a guess, 24 in all, and not one more.
    web = FakeWeb(
        greenhouse={"*": ("Acme Foods", [INDIA])},
        keka={"*": ("Acme Dental", [INDIA])},
    )
    async with client_for(web) as client:
        found = await probe_company(client, name="Acme Robotics", domain="acme.in", sleep=Sleeper())

    assert found is None
    assert len(web.requests) == 24


async def test_probe_stops_at_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discover, "MAX_PROBE_REQUESTS", 5)
    web = FakeWeb(greenhouse={"*": ("Acme Foods", [INDIA])})
    async with client_for(web) as client:
        found = await probe_company(client, name="Acme Robotics", domain="acme.in", sleep=Sleeper())

    assert found is None
    assert len(web.requests) == 5


async def test_probe_budget_covers_the_second_request_of_a_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One request left: the board's own read is made, its name check is not. A board whose name
    # was not confirmed is not kept, so the budget cannot be exceeded by "just one more".
    monkeypatch.setattr(discover, "MAX_PROBE_REQUESTS", 1)
    web = FakeWeb(greenhouse={"groww": ("Groww", [INDIA])})
    async with client_for(web) as client:
        found = await probe_company(client, name="Groww", domain="groww.in", sleep=Sleeper())

    assert found is None
    assert [r.url.path for r in web.requests] == ["/v1/boards/groww/jobs"]


async def test_probe_does_not_ask_an_ats_again_after_a_429() -> None:
    web = FakeWeb(
        status={"boards-api.greenhouse.io": 429},
        keka={"acmerobotics": ("Acme Robotics", [INDIA])},
    )
    async with client_for(web) as client:
        found = await probe_company(client, name="Acme Robotics", domain="acme.in", sleep=Sleeper())

    assert found == BoardRef("keka", "acmerobotics")  # the second guess
    assert web.hosts().count("boards-api.greenhouse.io") == 1  # one guess, then left alone


async def test_probe_an_ats_error_page_is_a_miss_not_a_board() -> None:
    # A 200 whose body is not JSON (a rate-limit page, a captive portal) for a board that would
    # otherwise be a hit.
    web = FakeWeb(workable={"slice": ("Slice", [INDIA])}, status={"apply.workable.com": 200})
    async with client_for(web) as client:
        found = await probe_company(client, name="Slice", domain=None, sleep=Sleeper())

    assert found is None


# -- discover_probe ---------------------------------------------------------------------------


async def add_company(
    factory: async_sessionmaker[AsyncSession],
    name: str,
    domain: str | None,
    *,
    is_tech: bool | None = True,
) -> uuid.UUID:
    async with factory() as seeding:
        company = Company(id=uuid.uuid4(), name=name, domain=domain, is_tech=is_tech)
        seeding.add(company)
        await seeding.commit()
        return company.id


async def test_discover_probe_adds_a_hit_under_its_company(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    groww = await add_company(factory, "Groww", "groww.in")
    nowhere = await add_company(factory, "Nowhere Systems", "nowhere.example")
    not_tech = await add_company(factory, "Bakery", "bakery.example", is_tech=False)
    unclassified = await add_company(factory, "Unclassified", "unclassified.example", is_tech=None)
    has_board = await add_company(factory, "Fabrikam", "fabrikam.in")
    async with factory() as seeding:
        seeding.add(
            JobBoard(ats="ashby", board="fabrikam", company_id=has_board, discovered_via="manual")
        )
        await seeding.commit()

    web = FakeWeb(greenhouse={"groww": ("Groww", [INDIA])}, ashby={"fabrikam": [INDIA]})
    async with client_for(web) as client:
        result = await discover_probe(session, client, limit=10, sleep=Sleeper())

    assert result == {"probed": 2, "found": 1, "inserted": 1}
    added = [b for b in await boards_of(factory) if b.discovered_via == "name_probe"]
    assert len(added) == 1
    assert (added[0].ats, added[0].board, added[0].company_id) == ("greenhouse", "groww", groww)
    assert (added[0].status, added[0].company_name) == ("candidate", "Groww")
    # Hit or miss, a probed company is not probed again for 90 days; the others never were.
    assert await CacheBackend.get(probed_key(str(groww))) is not None
    assert await CacheBackend.get(probed_key(str(nowhere))) is not None
    for skipped in (not_tech, unclassified, has_board):
        assert await CacheBackend.get(probed_key(str(skipped))) is None
    # A company that has a board, is not tech, or is not classified is never asked about.
    urls = " ".join(str(r.url).lower() for r in web.requests)
    assert not any(word in urls for word in ("fabrikam", "bakery", "unclassified"))


async def test_probe_skips_recently_probed_company(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    recent = await add_company(factory, "Groww", "groww.in")
    await CacheBackend.set(probed_key(str(recent)), "miss", discover.PROBED_TTL_SECONDS)

    web = FakeWeb(greenhouse={"groww": ("Groww", [INDIA])})
    async with client_for(web) as client:
        result = await discover_probe(session, client, limit=10, sleep=Sleeper())

    assert result == {"probed": 0, "found": 0, "inserted": 0}
    assert web.requests == []
    assert await boards_of(factory) == []


async def test_a_probed_company_is_not_probed_by_the_next_run(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    await add_company(factory, "Nowhere Systems", "nowhere.example")
    web = FakeWeb()
    async with client_for(web) as client:
        first = await discover_probe(session, client, limit=10, sleep=Sleeper())
        asked = len(web.requests)
        second = await discover_probe(session, client, limit=10, sleep=Sleeper())

    assert first["probed"] == 1
    assert second == {"probed": 0, "found": 0, "inserted": 0}
    assert len(web.requests) == asked


async def test_probed_key_and_ttl() -> None:
    assert probed_key("abc") == "tr:boards:probed:abc"
    assert discover.PROBED_TTL_SECONDS == 90 * 24 * 60 * 60


async def test_discover_probe_limit_caps_the_companies(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    for n in range(4):
        await add_company(factory, f"Company {n}", f"company{n}.example")

    async with client_for(FakeWeb()) as client:
        first = await discover_probe(session, client, limit=3, sleep=Sleeper())
        rest = await discover_probe(session, client, limit=3, sleep=Sleeper())

    assert first["probed"] == 3
    assert rest["probed"] == 1  # the run before it left one that was never probed

    await add_company(factory, "Company 5", "company5.example")
    web = FakeWeb()
    async with client_for(web) as client:
        none = await discover_probe(session, client, limit=0, sleep=Sleeper())
    assert none == {"probed": 0, "found": 0, "inserted": 0}
    assert web.requests == []


async def test_discover_probe_holds_no_transaction_during_a_request(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    await add_company(factory, "Groww", "groww.in")
    await add_company(factory, "Nowhere Systems", "nowhere.example")
    during: list[bool] = []
    web = FakeWeb(
        greenhouse={"groww": ("Groww", [INDIA])},
        on_request=lambda _request: during.append(session.in_transaction()),
    )
    async with client_for(web) as client:
        await discover_probe(session, client, limit=10, sleep=Sleeper())

    assert during and not any(during)
    assert not session.in_transaction()


async def test_discover_probe_gives_a_board_found_earlier_its_company(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    groww = await add_company(factory, "Groww", "groww.in")
    async with factory() as seeding:  # the open list got there first
        seeding.add(JobBoard(ats="greenhouse", board="groww", discovered_via="open_list"))
        await seeding.commit()

    async with client_for(FakeWeb(greenhouse={"groww": ("Groww", [INDIA])})) as client:
        result = await discover_probe(session, client, limit=10, sleep=Sleeper())

    assert result == {"probed": 1, "found": 1, "inserted": 0}
    (board,) = await boards_of(factory)
    assert (board.company_id, board.discovered_via) == (groww, "open_list")


async def test_discover_probe_paces_across_companies(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    await add_company(factory, "Alpha", "alpha.example")
    await add_company(factory, "Beta", "beta.example")
    sleeper = Sleeper()

    async with client_for(FakeWeb()) as client:
        result = await discover_probe(session, client, limit=10, sleep=sleeper)

    assert result["probed"] == 2
    # One guess each on six ATSes. The first company's request to each host is free; the
    # second company's has to wait that host's pace, even though it is another company.
    assert sorted(sleeper.calls) == sorted(PACE_SECONDS[ats] for ats in PROBE_ATS)


async def test_discover_probe_starts_no_company_after_its_budget(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    for n in range(3):
        await add_company(factory, f"Company {n}", f"company{n}.example")
    readings = iter([0.0, 10.0, 481.0])  # started, first company, second company

    async with client_for(FakeWeb()) as client:
        result = await discover_probe(
            session, client, limit=10, sleep=Sleeper(), clock=lambda: next(readings)
        )

    assert result["probed"] == 1
    async with client_for(FakeWeb()) as client:  # the two it left are taken by the next run
        assert (await discover_probe(session, client, limit=10, sleep=Sleeper()))["probed"] == 2


# -- discovery from the careers-page links: the company map's `sites` step ----------------------


def site_with(*boards: BoardRef) -> SiteFacts:
    return SiteFacts(
        title=None, description=None, text="", address=None, pincode=None, boards=boards
    )


async def add_site_company(
    factory: async_sessionmaker[AsyncSession],
    name: str,
    *,
    website: str | None = None,
    careers_url: str | None = None,
) -> uuid.UUID:
    async with factory() as seeding:
        company = Company(
            id=uuid.uuid4(),
            name=name,
            domain=f"{name.lower()}.example",
            website_url=website or f"https://{name.lower()}.example",
            careers_url=careers_url,
            is_tech=True,
        )
        seeding.add(company)
        await seeding.commit()
        return company.id


async def test_sites_step_upserts_boards_with_company_id(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred = await add_site_company(factory, "Cred")
    plain = await add_site_company(factory, "Plain")
    seen = site_with(BoardRef("lever", "Cred"), BoardRef("greenhouse", "credclub"))

    async def read(_client: httpx.AsyncClient, url: str) -> SiteFacts | None:
        return seen if "cred" in url else site_with()

    monkeypatch.setattr(refresh, "read_site", read)
    result = await refresh._step_sites(session, 10)

    assert result["due"] == 2 and result["read"] == 2 and result["boards"] == 2
    rows = {(b.ats, b.board): b for b in await boards_of(factory)}
    assert set(rows) == {("lever", "Cred"), ("greenhouse", "credclub")}
    for row in rows.values():
        assert (row.company_id, row.company_name) == (cred, "Cred")
        assert (row.discovered_via, row.status) == ("careers_link", "candidate")
    assert plain not in {b.company_id for b in rows.values()}


async def test_sites_step_gives_a_board_found_earlier_its_company_and_counts_no_insert(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cred = await add_site_company(factory, "Cred")
    async with factory() as seeding:  # the open list got there first
        seeding.add(
            JobBoard(ats="lever", board="Cred", discovered_via="open_list", status="active")
        )
        await seeding.commit()

    async def read(_client: httpx.AsyncClient, _url: str) -> SiteFacts | None:
        return site_with(BoardRef("lever", "Cred"))

    monkeypatch.setattr(refresh, "read_site", read)
    result = await refresh._step_sites(session, 10)

    assert result["boards"] == 0
    (board,) = await boards_of(factory)
    assert (board.company_id, board.discovered_via, board.status) == (cred, "open_list", "active")


async def test_careers_url_that_is_a_board_is_used_unfetched(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acme = await add_site_company(
        factory, "Acme", careers_url="https://job-boards.greenhouse.io/acme"
    )
    await add_site_company(factory, "Stays", careers_url="https://stays.example/careers")
    asked: list[str] = []

    async def read(_client: httpx.AsyncClient, url: str) -> SiteFacts | None:
        asked.append(url)
        return None  # robots.txt said no, or the site was down: the careers URL still counts

    monkeypatch.setattr(refresh, "read_site", read)
    result = await refresh._step_sites(session, 10)

    assert result["read"] == 0 and result["boards"] == 1
    # Only the two homepages were asked for: the careers URL is read as a string.
    assert sorted(asked) == ["https://acme.example", "https://stays.example"]
    (board,) = await boards_of(factory)
    assert (board.ats, board.board, board.company_id) == ("greenhouse", "acme", acme)
    assert (board.discovered_via, board.company_name) == ("careers_link", "Acme")


async def test_careers_url_and_site_name_the_same_board_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_site_company(factory, "Acme", careers_url="https://boards.greenhouse.io/acme/")

    async def read(_client: httpx.AsyncClient, _url: str) -> SiteFacts | None:
        return site_with(BoardRef("greenhouse", "acme"), BoardRef("ashby", "acme"))

    monkeypatch.setattr(refresh, "read_site", read)
    result = await refresh._step_sites(session, 10)

    assert result["boards"] == 2
    assert {(b.ats, b.board) for b in await boards_of(factory)} == {
        ("greenhouse", "acme"),
        ("ashby", "acme"),
    }


def many_boards(count: int) -> tuple[BoardRef, ...]:
    return tuple(BoardRef("greenhouse", f"portfolio{n}") for n in range(count))


async def test_site_with_many_boards_is_a_directory(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_site_company(factory, "Fund")
    assert refresh.MAX_OWN_BOARDS_PER_SITE == 3

    async def read(_client: httpx.AsyncClient, _url: str) -> SiteFacts | None:
        return site_with(*many_boards(5))

    monkeypatch.setattr(refresh, "read_site", read)
    result = await refresh._step_sites(session, 10)

    # Still discovered, but nobody's own: their roles go to placeholder companies.
    assert result["boards"] == 5
    rows = await boards_of(factory)
    assert len(rows) == 5
    for row in rows:
        assert (row.company_id, row.company_name) == (None, None)
        assert (row.discovered_via, row.status) == ("careers_link", "candidate")


async def test_site_with_three_boards_is_still_linked(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acme = await add_site_company(factory, "Acme")

    async def read(_client: httpx.AsyncClient, _url: str) -> SiteFacts | None:
        return site_with(*many_boards(3))

    monkeypatch.setattr(refresh, "read_site", read)
    result = await refresh._step_sites(session, 10)

    assert result["boards"] == 3
    rows = await boards_of(factory)
    assert {(row.company_id, row.company_name) for row in rows} == {(acme, "Acme")}


async def test_careers_url_board_counts_toward_the_directory_limit(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_site_company(factory, "Acme", careers_url="https://boards.greenhouse.io/acme")

    async def read(_client: httpx.AsyncClient, _url: str) -> SiteFacts | None:
        return site_with(*many_boards(3))

    monkeypatch.setattr(refresh, "read_site", read)
    result = await refresh._step_sites(session, 10)

    # Three site boards and a fourth, distinct one from the careers URL: a directory.
    assert result["boards"] == 4
    rows = {row.board: row for row in await boards_of(factory)}
    assert set(rows) == {"acme", "portfolio0", "portfolio1", "portfolio2"}
    assert {(row.company_id, row.company_name) for row in rows.values()} == {(None, None)}


async def test_careers_url_naming_a_board_the_site_names_is_not_counted_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beta = await add_site_company(
        factory, "Beta", careers_url="https://boards.greenhouse.io/portfolio0"
    )

    async def read(_client: httpx.AsyncClient, _url: str) -> SiteFacts | None:
        return site_with(*many_boards(3))

    monkeypatch.setattr(refresh, "read_site", read)
    result = await refresh._step_sites(session, 10)

    assert result["boards"] == 3
    assert {row.company_id for row in await boards_of(factory)} == {beta}


# -- discovery from YC's hiring list ------------------------------------------------------------

YC_SAMPLE = Path(__file__).resolve().parent / "fixtures" / "boards" / "yc_hiring_sample.json"
YC_URL = "https://yc-oss.github.io/api/companies/hiring.json"

#: The sample's India companies (a location or a region names India), in the list's order.
YC_INDIA_SITES = [
    "https://razorpay.com",
    "https://groww.in",
    "https://www.leena.ai",
    "https://cityfurnish.com/",
    "http://www.meesho.com",
]


def yc_entry(**fields: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "Acme",
        "website": "https://acme.example",
        "all_locations": "",
        "regions": [],
    }
    entry.update(fields)
    return entry


def yc_list(*entries: Any) -> bytes:
    return json.dumps(list(entries)).encode()


@dataclass
class YcWeb:
    """
    The hiring list at its URL, and every other host answered by ``ats`` (a ``FakeWeb``).

    ``requests`` is every request in order; ``on_request`` runs for each, before it is answered.
    """

    body: bytes = field(default_factory=YC_SAMPLE.read_bytes)
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    ats: FakeWeb = field(default_factory=FakeWeb)
    requests: list[httpx.Request] = field(default_factory=list)
    on_request: Callable[[httpx.Request], None] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.on_request is not None:
            self.on_request(request)
        if request.url.host == "yc-oss.github.io":
            return httpx.Response(self.status, content=self.body, headers=self.headers)
        return self.ats(request)

    def yc_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "yc-oss.github.io"]


class Sites:
    """
    A stand-in for ``read_site``: by site URL, the boards that site shows (a URL it does not
    know is a site that could not be read, which is ``None``).
    """

    def __init__(self) -> None:
        self.boards: dict[str, tuple[BoardRef, ...]] = {}
        self.asked: list[str] = []
        self.on_ask: Callable[[str], None] | None = None

    async def __call__(self, _client: httpx.AsyncClient, url: str) -> SiteFacts | None:
        self.asked.append(url)
        if self.on_ask is not None:
            self.on_ask(url)
        found = self.boards.get(url)
        return None if found is None else site_with(*found)


@pytest.fixture
def sites(monkeypatch: pytest.MonkeyPatch) -> Sites:
    stand_in = Sites()
    monkeypatch.setattr(discover, "read_site", stand_in)
    return stand_in


async def test_yc_keeps_india_only_and_creates_no_company(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession], sites: Sites
) -> None:
    razorpay = BoardRef("greenhouse", "razorpaysoftwareprivatelimited")
    sites.boards = {"https://razorpay.com": (razorpay,)}
    web = YcWeb(ats=FakeWeb(greenhouse={"groww": ("Groww", [INDIA])}))

    async with client_for(web) as client:
        result = await discover_yc(session, client, limit=50, sleep=Sleeper())

    # Five of the sample's ten are in India: Razorpay and Groww by both fields, Leena AI by one of
    # two places, Cityfurnish and Meesho. Aravolta is in Indianapolis, which is not India.
    assert sites.asked == YC_INDIA_SITES
    # Razorpay's site named a board. The other four have none, so each was probed, and Groww's
    # probe found a Greenhouse board that states the name "Groww".
    assert result == {"india": 5, "read": 1, "boards": 2, "probed": 4}
    rows = {(b.ats, b.board): b for b in await boards_of(factory)}
    assert set(rows) == {("greenhouse", "razorpaysoftwareprivatelimited"), ("greenhouse", "groww")}
    assert {(b.company_id, b.company_name, b.discovered_via) for b in rows.values()} == {
        (None, "Razorpay", "yc"),
        (None, "Groww", "yc"),
    }
    # The map stays Bengaluru: a YC company is never a companies row.
    assert (await session.execute(select(Company))).scalars().all() == []
    assert await CacheBackend.get(yc_site_key("razorpay.com")) is not None
    assert await CacheBackend.get(yc_site_key("gusto.com")) is None
    assert not any("razorpay" in str(r.url) for r in web.ats.requests)  # a site board: no probe


async def test_yc_reads_the_list_the_way_the_other_lists_are_read(
    session: AsyncSession, sites: Sites
) -> None:
    web = YcWeb(body=yc_list())
    async with client_for(web) as client:
        result = await discover_yc(session, client, limit=50, sleep=Sleeper())

    assert result == {"india": 0, "read": 0, "boards": 0, "probed": 0}
    (request,) = web.yc_requests()
    assert request.method == "GET" and str(request.url) == YC_URL
    assert request.headers["user-agent"] == USER_AGENT
    assert sites.asked == []


async def test_yc_skips_recently_read_site(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession], sites: Sites
) -> None:
    await CacheBackend.set(yc_site_key("razorpay.com"), "read", discover.YC_SITE_TTL_SECONDS)
    await CacheBackend.set(probed_key("groww.in"), "miss", discover.PROBED_TTL_SECONDS)
    web = YcWeb(ats=FakeWeb(greenhouse={"groww": ("Groww", [INDIA])}))

    async with client_for(web) as client:
        first = await discover_yc(session, client, limit=50, sleep=Sleeper())
        asked = list(sites.asked)
        again = await discover_yc(session, client, limit=50, sleep=Sleeper())

    # Razorpay was read lately, so it is skipped altogether. Groww's site is read, but it was
    # probed lately, so it is not probed again (and so its board is not found here).
    assert asked == YC_INDIA_SITES[1:]
    assert first == {"india": 5, "read": 0, "boards": 0, "probed": 3}
    assert not any("groww" in str(r.url) for r in web.ats.requests)
    assert await boards_of(factory) == []
    # Every company was attempted, so the next run has nothing to do.
    assert again == {"india": 5, "read": 0, "boards": 0, "probed": 0}
    assert sites.asked == asked


def test_yc_key_names_and_ttls() -> None:
    assert yc_site_key("razorpay.com") == "tr:boards:yc-site:razorpay.com"
    assert discover.YC_SITE_TTL_SECONDS == 30 * 24 * 60 * 60
    assert discover.PROBED_TTL_SECONDS == 90 * 24 * 60 * 60


async def test_yc_keys_a_company_by_its_domain_and_marks_both(
    session: AsyncSession, sites: Sites
) -> None:
    web = YcWeb(
        body=yc_list(
            yc_entry(name="Nowhere", website="https://www.nowhere.example/jobs", regions=["India"])
        )
    )
    async with client_for(web) as client:
        result = await discover_yc(session, client, limit=50, sleep=Sleeper())

    assert result == {"india": 1, "read": 0, "boards": 0, "probed": 1}
    assert sites.asked == ["https://www.nowhere.example/jobs"]
    assert await CacheBackend.get(yc_site_key("nowhere.example")) is not None
    assert await CacheBackend.get(probed_key("nowhere.example")) is not None


async def test_yc_a_company_whose_site_names_a_board_is_not_probed(
    session: AsyncSession, sites: Sites
) -> None:
    sites.boards = {"https://razorpay.com": (BoardRef("ashby", "razorpay"),)}
    web = YcWeb()
    async with client_for(web) as client:
        await discover_yc(session, client, limit=1, sleep=Sleeper())

    assert web.ats.requests == []
    assert await CacheBackend.get(probed_key("razorpay.com")) is None


async def test_yc_picks_india_by_location_or_region_and_skips_what_it_cannot_read(
    session: AsyncSession, sites: Sites
) -> None:
    entries: list[Any] = [
        yc_entry(name="Location Only", website="https://locationonly.example")
        | {"all_locations": "Pune, MH, India"},
        yc_entry(
            name="Region Only", website="https://regiononly.example", regions=["Remote", "India"]
        ),
        yc_entry(name="Indianapolis Co", website="https://indy.example")
        | {"all_locations": "Indianapolis, IN, USA"},
        yc_entry(name="No Site", website=None, regions=["India"]),
        yc_entry(
            name="Profile Site", website="https://www.linkedin.com/company/x", regions=["India"]
        ),
        yc_entry(name="", website="https://nameless.example", regions=["India"]),
        yc_entry(name=None, website="https://nonamer.example", regions=["India"]),
        yc_entry(
            name="Odd Fields", website="https://oddfields.example", all_locations=7, regions="India"
        ),
        "not a company",
        None,
    ]
    web = YcWeb(body=yc_list(*entries))
    async with client_for(web) as client:
        result = await discover_yc(session, client, limit=50, sleep=Sleeper())

    assert sites.asked == ["https://locationonly.example", "https://regiononly.example"]
    assert result["india"] == 2


@pytest.mark.parametrize("name", ["x" * 500, "  Spaced \n Name  "])
async def test_yc_company_name_is_one_line_and_bounded(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    sites: Sites,
    name: str,
) -> None:
    sites.boards = {"https://acme.example": (BoardRef("lever", "acme"),)}
    web = YcWeb(body=yc_list(yc_entry(name=name, regions=["India"])))
    async with client_for(web) as client:
        await discover_yc(session, client, limit=50, sleep=Sleeper())

    (board,) = await boards_of(factory)
    assert board.company_name is not None
    assert "\n" not in board.company_name and len(board.company_name) <= 200
    assert board.company_name == board.company_name.strip()


async def test_yc_limit_counts_the_companies_attempted_not_the_ones_skipped(
    session: AsyncSession, sites: Sites
) -> None:
    await CacheBackend.set(yc_site_key("razorpay.com"), "read", discover.YC_SITE_TTL_SECONDS)
    web = YcWeb()
    async with client_for(web) as client:
        first = await discover_yc(session, client, limit=2, sleep=Sleeper())
        second = await discover_yc(session, client, limit=2, sleep=Sleeper())
        none = await discover_yc(session, client, limit=0, sleep=Sleeper())

    assert sites.asked == YC_INDIA_SITES[1:]  # two, then the next two
    assert first["probed"] == 2 and second["probed"] == 2
    assert none == {"india": 5, "read": 0, "boards": 0, "probed": 0}


async def test_yc_a_board_already_on_the_roster_is_left_as_it_is(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession], sites: Sites
) -> None:
    company = await add_company(factory, "Razorpay Map Row", "razorpay.com")
    async with factory() as seeding:
        seeding.add(
            JobBoard(
                ats="greenhouse",
                board="razorpaysoftwareprivatelimited",
                company_id=company,
                discovered_via="careers_link",
                status="active",
            )
        )
        await seeding.commit()
    sites.boards = {
        "https://razorpay.com": (BoardRef("greenhouse", "razorpaysoftwareprivatelimited"),)
    }

    async with client_for(YcWeb()) as client:
        result = await discover_yc(session, client, limit=1, sleep=Sleeper())

    assert result["boards"] == 0
    (board,) = await boards_of(factory)
    assert (board.company_id, board.discovered_via, board.status) == (
        company,
        "careers_link",
        "active",
    )


@pytest.mark.parametrize("site_names_a_board", [True, False], ids=["site-board", "probe-hit"])
async def test_yc_a_failed_write_sets_neither_key(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    sites: Sites,
    monkeypatch: pytest.MonkeyPatch,
    site_names_a_board: bool,
) -> None:
    if site_names_a_board:
        sites.boards = {"https://razorpay.com": (BoardRef("lever", "razorpay"),)}
    real_commit = session.commit
    calls = 0

    async def commit() -> None:
        nonlocal calls
        calls += 1
        if calls > 1:  # the first one only ends the caller's transaction, before the list
            raise RuntimeError("the database went away")
        await real_commit()

    monkeypatch.setattr(session, "commit", commit)
    web = YcWeb(ats=FakeWeb(greenhouse={"razorpay": ("Razorpay", [INDIA])}))

    async with client_for(web) as client:
        with pytest.raises(RuntimeError, match="went away"):
            await discover_yc(session, client, limit=1, sleep=Sleeper())

    # The company was read (and probed when its site named nothing), but its boards were never
    # committed, so neither key may say it was done: the next run takes it again.
    assert sites.asked == ["https://razorpay.com"]
    assert any("razorpay" in str(r.url) for r in web.ats.requests) is not site_names_a_board
    assert await CacheBackend.get(yc_site_key("razorpay.com")) is None
    assert await CacheBackend.get(probed_key("razorpay.com")) is None


async def test_yc_holds_no_transaction_during_a_request(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession], sites: Sites
) -> None:
    sites.boards = {
        "https://razorpay.com": (BoardRef("lever", "razorpay"),),
        "https://groww.in": (BoardRef("ashby", "groww"),),
    }
    # The caller has read something, so its session holds a transaction and a connection.
    await session.execute(select(JobBoard))
    assert session.in_transaction()
    during: list[bool] = []
    web = YcWeb(on_request=lambda _request: during.append(session.in_transaction()))
    sites.on_ask = lambda _url: during.append(session.in_transaction())

    async with client_for(web) as client:
        result = await discover_yc(session, client, limit=50, sleep=Sleeper())

    assert result["boards"] == 2
    assert len(during) > 6 and not any(during)
    assert not session.in_transaction()
    assert len(await boards_of(factory)) == 2


async def test_yc_paces_the_probe_across_companies(session: AsyncSession, sites: Sites) -> None:
    sleeper = Sleeper()
    web = YcWeb(
        body=yc_list(
            yc_entry(name="Alpha", website="https://alpha.example", regions=["India"]),
            yc_entry(name="Beta", website="https://beta.example", regions=["India"]),
        )
    )
    async with client_for(web) as client:
        result = await discover_yc(session, client, limit=50, sleep=sleeper)

    assert result["probed"] == 2
    # The second company's first request to each ATS host waits that host's pace.
    assert sorted(sleeper.calls) == sorted(PACE_SECONDS[ats] for ats in PROBE_ATS)


async def test_yc_starts_no_company_after_its_budget(session: AsyncSession, sites: Sites) -> None:
    readings = iter([0.0, 10.0, 481.0])  # started, first company, second company
    async with client_for(YcWeb()) as client:
        result = await discover_yc(
            session, client, limit=50, sleep=Sleeper(), clock=lambda: next(readings)
        )

    assert result["probed"] == 1 and sites.asked == YC_INDIA_SITES[:1]
    async with client_for(YcWeb()) as client:  # the four it left are taken by the next run
        again = await discover_yc(session, client, limit=50, sleep=Sleeper())
    assert again["probed"] == 4


@pytest.mark.parametrize(
    ("web", "message"),
    [
        (YcWeb(status=503), "503"),
        (YcWeb(status=301, headers={"location": "https://elsewhere.example/h.json"}), "301"),
        (YcWeb(body=b"<html>captive portal</html>"), "not JSON"),
        (YcWeb(body=b"[" * 200_000), "not JSON"),  # nested past what the parser will follow
        (YcWeb(body=b'{"companies": []}'), "not a list"),
        (YcWeb(headers={"content-length": str(MAX_YC_BYTES + 1)}), "size cap"),
    ],
    ids=["status", "redirect", "not-json", "too-deep", "not-a-list", "declared-too-big"],
)
async def test_yc_a_list_that_cannot_be_read_is_an_error_and_nothing_is_written(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    sites: Sites,
    web: YcWeb,
    message: str,
) -> None:
    async with client_for(web) as client:
        with pytest.raises(YcListError, match=message):
            await discover_yc(session, client, limit=50, sleep=Sleeper())

    assert len(web.requests) == 1  # a redirect is not followed
    assert sites.asked == [] and await boards_of(factory) == []


async def test_yc_a_list_over_the_cap_is_refused_as_it_streams(
    session: AsyncSession, sites: Sites, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(discover, "MAX_YC_BYTES", 64)
    async with client_for(chunked(b"[" + b" " * 40, b" " * 40 + b"]")) as client:
        with pytest.raises(YcListError, match="size cap"):
            await discover_yc(session, client, limit=50, sleep=Sleeper())


async def test_yc_a_broken_stream_is_an_error_not_a_crash(
    session: AsyncSession, sites: Sites
) -> None:
    handler = chunked(b"[", then_raise=httpx.ReadError("connection reset"))
    async with client_for(handler) as client:
        with pytest.raises(YcListError, match="ReadError"):
            await discover_yc(session, client, limit=50, sleep=Sleeper())


async def test_yc_reads_each_site_through_read_site_so_robots_txt_applies(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def public(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(website, "_resolve", public, raising=False)
    ats = FakeWeb()
    paths: dict[str, list[str]] = {"razorpay.com": [], "groww.in": []}
    home = "<html><body><a href='https://boards.greenhouse.io/groww'>Careers</a></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "yc-oss.github.io":
            return httpx.Response(200, content=YC_SAMPLE.read_bytes())
        if host in paths:
            paths[host].append(request.url.path)
            if host == "razorpay.com":
                return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(
                200, headers={"content-type": "text/html; charset=utf-8"}, text=home
            )
        return ats(request)

    async with client_for(handler) as client:
        result = await discover_yc(session, client, limit=2, sleep=Sleeper())

    assert paths == {"razorpay.com": ["/robots.txt"], "groww.in": ["/robots.txt", "/"]}
    assert result["read"] == 1  # Razorpay's robots.txt said no
    (board,) = await boards_of(factory)
    assert (board.ats, board.board, board.company_name) == ("greenhouse", "groww", "Groww")
    assert board.company_id is None
