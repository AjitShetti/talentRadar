"""
tests/test_posting_urls_boards.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The rest of the app learns the nine ATS names: posting URLs on the six newer
ATSes are recognised exactly (and never fetched), a stored row on any of the
nine is asked about by the id in its own URL, and every list that used to name
Greenhouse, Lever and Ashby is derived from ``domain.job_boards.ATS``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.merge_rank import SOURCE_TRUST
from domain.job_boards import ATS
from domain.posting_urls import PostingRef, parse_posting_url, posting_ref
from services import liveness, public_roles
from storage.database import Base
from storage.models import Company, Job, JobStatus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

RIPPLING_ID = "0b6c1f3e-1a2b-4c5d-8e9f-001122334455"

NEW_ATS_URLS = [
    ("https://apply.workable.com/apna/j/1A2B3C4D5E/", "workable", "apna"),
    ("https://lenskartcareers.recruitee.com/o/store-manager-bengaluru", "recruitee", "lenskartcareers"),
    ("https://spyneai.keka.com/careers/jobdetails/12345", "keka", "spyneai"),
    (f"https://ats.rippling.com/rippling/jobs/{RIPPLING_ID}", "rippling", "rippling"),
    ("https://acme.breezy.hr/p/0123abcd4567-backend-engineer", "breezy", "acme"),
    ("https://acme.jobs.personio.de/job/998877", "personio", "acme"),
]


# -- recognising a posting URL --------------------------------------------------


@pytest.mark.parametrize(("url", "ats", "slug"), NEW_ATS_URLS)
def test_new_ats_posting_urls_parse(url: str, ats: str, slug: str) -> None:
    ref = parse_posting_url(url)
    assert ref is not None
    assert (ref.ats, ref.company_slug) == (ats, slug)
    assert ref.source == f"{ats}:{slug}"


@pytest.mark.parametrize(
    ("url", "external_id"),
    [
        # Workable's shortcode is upper case and keeps its case: the id is
        # looked for in the stored URL and handed back to Workable as written.
        ("https://apply.workable.com/apna/j/1A2B3C4D5E/", "1A2B3C4D5E"),
        ("https://lenskartcareers.recruitee.com/o/store-manager-bengaluru", "store-manager-bengaluru"),
        ("https://spyneai.keka.com/careers/jobdetails/12345", "12345"),
        (f"https://ats.rippling.com/rippling/jobs/{RIPPLING_ID.upper()}", RIPPLING_ID),
        ("https://acme.breezy.hr/p/0123abcd4567-backend-engineer", "0123abcd4567-backend-engineer"),
        ("https://acme.jobs.personio.com/job/998877?language=en", "998877"),
    ],
)
def test_new_ats_posting_ids(url: str, external_id: str) -> None:
    ref = parse_posting_url(url)
    assert ref is not None
    assert ref.external_id == external_id


def test_personio_posting_on_either_region_host_parses() -> None:
    de = parse_posting_url("https://acme.jobs.personio.de/job/1")
    com = parse_posting_url("https://acme.jobs.personio.com/job/1")
    assert de == com == PostingRef("personio", "acme", "1")


def test_slug_is_lower_cased_like_the_other_atses() -> None:
    ref = parse_posting_url("https://Apply.Workable.com/Apna/j/1A2B3C4D5E")
    assert ref == PostingRef("workable", "apna", "1A2B3C4D5E")


@pytest.mark.parametrize(
    "url",
    [
        # The ATS's own site, not a customer's board.
        "https://www.recruitee.com/o/store-manager",
        "https://app.keka.com/careers/jobdetails/1",
        "https://help.breezy.hr/p/0123abcd4567",
        "https://jobs.personio.de/job/1",
        "https://apply.workable.com/j/1A2B3C4D5E",
        "https://apply.workable.com/api/j/1A2B3C4D5E",
        # A board, not a posting.
        "https://apply.workable.com/apna/",
        "https://apply.workable.com/apna/j/",
        "https://lenskartcareers.recruitee.com/",
        "https://lenskartcareers.recruitee.com/o/",
        "https://spyneai.keka.com/careers",
        "https://spyneai.keka.com/careers/jobdetails/",
        "https://ats.rippling.com/rippling/jobs",
        "https://acme.breezy.hr/",
        "https://acme.jobs.personio.de/",
        # Keka is read only under /careers.
        "https://spyneai.keka.com/jobdetails/12345",
        # The id is not the shape the ATS uses.
        "https://apply.workable.com/apna/j/abc",
        "https://spyneai.keka.com/careers/jobdetails/abc",
        "https://acme.jobs.personio.de/job/abc",
        "https://ats.rippling.com/rippling/jobs/not-a-uuid",
        "https://acme.breezy.hr/p/NOT-HEX",
        "https://lenskartcareers.recruitee.com/o/Store_Manager",
        # Look-alike hosts, extra labels, a login, a port, another scheme.
        "https://apply.workable.com.evil.example/apna/j/1A2B3C4D5E",
        "https://recruitee.com.evil.example/o/store-manager",
        "https://evil.example/?u=https://apply.workable.com/apna/j/1A2B3C4D5E",
        "https://a.b.recruitee.com/o/store-manager",
        "https://acme.x.jobs.personio.de/job/1",
        "https://user:pass@acme.breezy.hr/p/0123abcd4567",
        "https://acme.breezy.hr:8443/p/0123abcd4567",
        "ftp://acme.breezy.hr/p/0123abcd4567",
        "https://notbreezy.hr/p/0123abcd4567",
        "https://acme.jobs.personio.org/job/1",
        # Traversal and encoding tricks in the path.
        "https://apply.workable.com/ap%2fna/j/1A2B3C4D5E",
        "https://apply.workable.com/../j/1A2B3C4D5E",
    ],
)
def test_other_urls_on_those_hosts_are_not_postings(url: str) -> None:
    assert parse_posting_url(url) is None


def test_greenhouse_lever_and_ashby_still_parse_as_before() -> None:
    lever = "0a1b2c3d-1111-2222-3333-444455556666"
    assert parse_posting_url("https://boards.greenhouse.io/Stripe/jobs/6123456") == PostingRef(
        "greenhouse", "stripe", "6123456"
    )
    assert parse_posting_url(f"https://jobs.lever.co/spotify/{lever}/apply") == PostingRef(
        "lever", "spotify", lever
    )
    assert parse_posting_url(f"https://jobs.ashbyhq.com/linear/{lever}") == PostingRef(
        "ashby", "linear", lever
    )


# -- a stored row ---------------------------------------------------------------

URL_HASH = "d41d8cd98f00b204e9800998ecf8427e"


@pytest.mark.parametrize(("url", "ats", "slug"), NEW_ATS_URLS)
def test_stored_row_on_a_new_ats_is_asked_about_by_the_id_in_its_url(
    url: str, ats: str, slug: str
) -> None:
    # The sweep stores an MD5 of the URL as external_id; the real id is in the URL.
    ref = posting_ref(f"{ats}:{slug}", url, URL_HASH)
    assert ref is not None
    assert (ref.ats, ref.company_slug) == (ats, slug)
    assert ref.external_id != URL_HASH


@pytest.mark.parametrize(("url", "ats", "slug"), NEW_ATS_URLS)
def test_a_url_on_another_ats_proves_nothing_about_the_row(url: str, ats: str, slug: str) -> None:
    other = next(a for a in ATS if a != ats)
    assert posting_ref(f"{other}:{slug}", url, URL_HASH) is None


@pytest.mark.parametrize("ats", ATS)
def test_a_row_with_no_real_id_cannot_be_asked_about(ats: str) -> None:
    assert posting_ref(f"{ats}:acme", "https://example.com/careers/1", URL_HASH) is None
    assert posting_ref(f"{ats}:acme", None, None) is None
    assert posting_ref(f"{ats}:", "https://acme.breezy.hr/p/0123abcd4567", None) is None


def test_a_source_outside_the_nine_is_not_asked_about() -> None:
    assert posting_ref("workday:acme", "https://acme.breezy.hr/p/0123abcd4567", None) is None
    assert posting_ref("linkedin", None, "123") is None


# -- derived lists --------------------------------------------------------------


@pytest.mark.parametrize("ats", ATS)
def test_every_ats_has_full_trust(ats: str) -> None:
    assert SOURCE_TRUST[ats] == 1.0


@pytest.mark.parametrize("ats", ATS)
def test_every_ats_is_publishable_source(ats: str) -> None:
    assert public_roles.is_publishable_source(f"{ats}:acme")


def test_unsupported_message_names_all_nine() -> None:
    message = public_roles._UNSUPPORTED_MESSAGE.lower()
    assert all(name in message for name in ATS)


# -- the public reads -----------------------------------------------------------


@pytest_asyncio.fixture
async def factory(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("storage.database.AsyncSessionLocal", sessions)
    yield sessions
    await engine.dispose()


async def _seed(sessions: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    company_id = uuid.uuid4()
    async with sessions() as session:
        session.add(Company(id=company_id, domain="acme.talentradar.internal", name="Acme"))
        await session.commit()
        session.add_all(
            Job(
                id=uuid.uuid4(),
                external_id=f"ext-{ats}",
                source=f"{ats}:acme",
                source_url=f"https://example.com/{ats}/1",
                title=f"Engineer {ats}",
                company_id=company_id,
                location_raw="Bengaluru, India",
                status=JobStatus.ACTIVE,
                last_seen_at=datetime.now(tz=UTC),
            )
            for ats in ATS
        )
        # A job board row is never published, whatever its location says.
        session.add(
            Job(
                id=uuid.uuid4(),
                external_id="ext-linkedin",
                source="linkedin",
                source_url="https://www.linkedin.com/jobs/view/1",
                title="Engineer linkedin",
                company_id=company_id,
                location_raw="Bengaluru, India",
                status=JobStatus.ACTIVE,
                last_seen_at=datetime.now(tz=UTC),
            )
        )
        await session.commit()
    return company_id


@pytest.mark.asyncio
async def test_public_list_reads_every_ats(factory: async_sessionmaker[AsyncSession]) -> None:
    await _seed(factory)
    roles = await public_roles.list_roles(limit=100)
    assert {role["title"] for role in roles} == {f"Engineer {ats}" for ats in ATS}


@pytest.mark.asyncio
async def test_company_page_reads_every_ats(factory: async_sessionmaker[AsyncSession]) -> None:
    company_id = await _seed(factory)
    page = await public_roles.company_roles(f"acme-{company_id}")
    assert page is not None
    assert {role["title"] for role in page["roles"]} == {f"Engineer {ats}" for ats in ATS}


@pytest.mark.asyncio
async def test_reverification_candidates_include_every_ats(factory: async_sessionmaker[AsyncSession]) -> None:
    await _seed(factory)
    candidates = await liveness._load_candidates(100)
    assert {c.source for c in candidates} == {f"{ats}:acme" for ats in ATS}
