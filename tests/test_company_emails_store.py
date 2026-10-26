"""Storing published addresses: only on the company's own domain, never overwriting,
with the page they came from. Fictional companies and people only."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.email_patterns import email_domain_ok
from services.company_map import website
from services.company_map.people import upsert_people
from services.company_map.store import due, upsert_site_emails
from services.company_map.types import PersonCandidate, SiteEmail
from storage.models import CompanyContact
from tests.outreach_support import make_company, make_contact, outreach_db  # noqa: F401

TEAM_PAGE = "https://fictional.test/team"


def _person(**kw: object) -> PersonCandidate:
    values: dict[str, object] = {
        "name": "Asha Example",
        "kind": "founder",
        "source_url": TEAM_PAGE,
        "source_kind": "site",
    }
    values.update(kw)
    return PersonCandidate(**values)  # type: ignore[arg-type]


async def _contacts(session: AsyncSession) -> list[CompanyContact]:
    return list((await session.execute(select(CompanyContact))).scalars())


def test_email_domain_ok() -> None:
    assert email_domain_ok("asha@fictional.test", "fictional.test")
    assert email_domain_ok("asha@in.fictional.test", "https://www.fictional.test/")
    assert not email_domain_ok("asha@gmail.com", "fictional.test")
    assert not email_domain_ok("asha@notfictional.test", "fictional.test")
    assert not email_domain_ok("asha@fictional.test", None)


async def test_person_email_stored_with_provenance(
    outreach_db: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    async with outreach_db() as session:
        company = await make_company(session, "Fictional Labs", domain="fictional.test")
        await upsert_people(
            session,
            company.id,
            [_person(email="asha@fictional.test", email_source_url=TEAM_PAGE)],
            company_domain=company.domain,
        )
        await session.commit()
        (row,) = await _contacts(session)
    assert row.email == "asha@fictional.test"
    assert row.email_source_url == TEAM_PAGE


async def test_foreign_domain_email_not_stored(
    outreach_db: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    async with outreach_db() as session:
        company = await make_company(session, "Fictional Labs", domain="fictional.test")
        await upsert_people(
            session,
            company.id,
            [_person(email="asha.personal@gmail.com", email_source_url="https://github.com/asha")],
            company_domain=company.domain,
        )
        await session.commit()
        (row,) = await _contacts(session)
    assert row.email is None
    assert row.email_source_url is None


async def test_email_not_stored_without_a_company_domain(
    outreach_db: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    async with outreach_db() as session:
        company = await make_company(session, "Fictional Labs")
        await upsert_people(
            session,
            company.id,
            [_person(email="asha@fictional.test", email_source_url=TEAM_PAGE)],
        )
        await session.commit()
        (row,) = await _contacts(session)
    assert row.email is None


async def test_existing_email_never_overwritten(
    outreach_db: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    async with outreach_db() as session:
        company = await make_company(session, "Fictional Labs", domain="fictional.test")
        await make_contact(
            session,
            company,
            "founder",
            "Asha Example",
            email="asha.e@fictional.test",
            email_source_url="https://fictional.test/about",
        )
        await upsert_people(
            session,
            company.id,
            [_person(email="asha@fictional.test", email_source_url=TEAM_PAGE)],
            company_domain=company.domain,
        )
        await session.commit()
        (row,) = await _contacts(session)
    assert row.email == "asha.e@fictional.test"
    assert row.email_source_url == "https://fictional.test/about"


async def test_existing_person_gains_a_missing_email(
    outreach_db: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    async with outreach_db() as session:
        company = await make_company(session, "Fictional Labs", domain="fictional.test")
        await make_contact(session, company, "founder", "Asha Example")
        await upsert_people(
            session,
            company.id,
            [_person(email="asha@fictional.test", email_source_url=TEAM_PAGE)],
            company_domain=company.domain,
        )
        await session.commit()
        (row,) = await _contacts(session)
    assert row.email == "asha@fictional.test"
    assert row.email_source_url == TEAM_PAGE


async def test_hiring_inbox_becomes_careers_inbox_contact_once(
    outreach_db: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    inbox = SiteEmail(address="careers@fictional.test", source_url=TEAM_PAGE)
    foreign = SiteEmail(address="jobs@agency.test", source_url=TEAM_PAGE)
    async with outreach_db() as session:
        company = await make_company(session, "Fictional Labs", domain="fictional.test")
        assert await upsert_site_emails(session, company, [inbox, foreign]) == 1
        assert await upsert_site_emails(session, company, [inbox]) == 0
        await session.commit()
        rows = await _contacts(session)
    assert len(rows) == 1
    row = rows[0]
    assert (row.kind, row.name, row.user_id) == ("careers_inbox", None, None)
    assert row.email == "careers@fictional.test"
    assert row.source_url == TEAM_PAGE
    assert row.email_source_url == TEAM_PAGE


async def test_sites_due_prefers_people_without_email(
    outreach_db: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    async with outreach_db() as session:
        plain = await make_company(session, "Aaa Plain", website_url="https://aaa.test/")
        with_people = await make_company(session, "Zzz People", website_url="https://zzz.test/")
        await make_contact(session, with_people, "founder", "Asha Example")
        reached = await make_company(session, "Mmm Reached", website_url="https://mmm.test/")
        await make_contact(
            session,
            reached,
            "founder",
            "Ravi Sample",
            email="ravi@mmm.test",
            email_source_url="https://mmm.test/team",
        )
        rows = await due(session, "sites", 10)
    assert [c.name for c in rows] == [with_people.name, plain.name, reached.name]


# --- the extra pages read_site visits ----------------------------------------------

_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return [_PUBLIC_IP]

    monkeypatch.setattr(website, "_resolve", resolve, raising=False)


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=body)


HOME = (
    "<html><head><meta name='description' content='Home.'></head><body>"
    "<address>1 MG Road, Bengaluru 560001</address>"
    "<a href='/careers'>Careers</a><a href='/jobs'>Jobs</a>"
    "<a href='/team'>Team</a><a href='/about'>About</a></body></html>"
)
CAREERS = "<html><body><a href='mailto:careers@fictional.test'>Apply</a></body></html>"
TEAM = (
    "<html><body><div><h3>Asha Example</h3><p>Founder</p>"
    "<a href='https://www.linkedin.com/in/asha-example'>LinkedIn</a>"
    "<a href='mailto:asha@fictional.test'>mail</a></div></body></html>"
)


async def test_read_site_visits_careers_and_team_pages() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        pages = {"/": HOME, "/careers": CAREERS, "/team": TEAM}
        body = pages.get(request.url.path)
        return _html(body) if body is not None else httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        facts = await website.read_site(client, "https://fictional.test/")

    assert facts is not None
    assert seen == ["/robots.txt", "/", "/careers", "/team"]
    assert [e.address for e in facts.emails] == ["careers@fictional.test"]
    assert [(p.name, p.email) for p in facts.people] == [("Asha Example", "asha@fictional.test")]
    assert facts.people[0].email_source_url == "https://fictional.test/team"


async def test_read_site_extra_page_failure_keeps_the_homepage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/":
            return _html(HOME)
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        facts = await website.read_site(client, "https://fictional.test/")

    assert facts is not None
    assert facts.description == "Home."
