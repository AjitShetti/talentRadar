"""
tests/test_public_surface.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The part of TalentRadar a stranger can reach without an account: public role
pages and the "is this posting still open?" check.

Two things matter more here than anywhere else. The check takes a URL from
the internet, so it must never fetch that URL - only a known ATS endpoint
built from validated pieces of it. And nothing on this surface may carry a
user's data or a row scraped from a job board we have no right to republish.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from domain.posting_urls import PostingRef, parse_posting_url, role_slug, slug_job_id
from ingestion.scrapers.ats_scraper import PostingCheck
from services import public_roles

# ── recognising a posting URL ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/stripe/jobs/6123456", PostingRef("greenhouse", "stripe", "6123456")),
        ("https://job-boards.greenhouse.io/stripe/jobs/6123456?gh_src=abc", PostingRef("greenhouse", "stripe", "6123456")),
        ("http://boards.greenhouse.io/Stripe/jobs/6123456/", PostingRef("greenhouse", "stripe", "6123456")),
        ("https://jobs.lever.co/spotify/0a1b2c3d-1111-2222-3333-444455556666", PostingRef("lever", "spotify", "0a1b2c3d-1111-2222-3333-444455556666")),
        ("https://jobs.lever.co/spotify/0a1b2c3d-1111-2222-3333-444455556666/apply", PostingRef("lever", "spotify", "0a1b2c3d-1111-2222-3333-444455556666")),
        ("https://jobs.ashbyhq.com/linear/9f8e7d6c-1111-2222-3333-444455556666", PostingRef("ashby", "linear", "9f8e7d6c-1111-2222-3333-444455556666")),
        ("  https://jobs.ashbyhq.com/linear/9f8e7d6c-1111-2222-3333-444455556666/application  ", PostingRef("ashby", "linear", "9f8e7d6c-1111-2222-3333-444455556666")),
    ],
)
def test_ats_posting_urls_are_recognised(url: str, expected: PostingRef):
    assert parse_posting_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "https://www.linkedin.com/jobs/view/123456",
        "https://www.naukri.com/job-listings-backend-engineer-123",
        "https://boards.greenhouse.io/stripe",
        "https://boards.greenhouse.io/stripe/jobs/",
        "https://boards.greenhouse.io/stripe/jobs/abc",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://boards.greenhouse.io.evil.example/stripe/jobs/123",
        "https://evil.example/?u=https://boards.greenhouse.io/stripe/jobs/123",
        "https://user:pass@boards.greenhouse.io/stripe/jobs/123",
        "https://jobs.lever.co/spo%2e%2e/../x/0a1b2c3d-1111-2222-3333-444455556666",
        "https://jobs.lever.co/spotify/..%2f..%2fadmin",
        "https://boards.greenhouse.io/" + "a" * 200 + "/jobs/123",
    ],
)
def test_everything_else_is_not_a_posting_we_can_verify(url: str):
    assert parse_posting_url(url) is None


def test_ref_gives_the_source_key_the_index_uses():
    assert PostingRef("greenhouse", "stripe", "1").source == "greenhouse:stripe"


# ── public slugs ─────────────────────────────────────────────────────────────


def test_slug_is_readable_and_ends_in_the_id():
    job_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    slug = role_slug("Senior Backend Engineer (Payments)", "Stripe, Inc.", job_id)
    assert slug == "senior-backend-engineer-payments-stripe-inc-11111111-2222-3333-4444-555555555555"
    assert slug_job_id(slug) == job_id


def test_slug_survives_an_unusable_title():
    job_id = uuid.uuid4()
    assert slug_job_id(role_slug("???", None, job_id)) == job_id


@pytest.mark.parametrize("slug", ["", "backend-engineer", "x-11111111-2222-3333-4444-55555555555", "../../etc"])
def test_a_slug_without_a_valid_id_resolves_to_nothing(slug: str):
    assert slug_job_id(slug) is None


def test_a_bare_id_is_also_a_valid_slug():
    job_id = uuid.uuid4()
    assert slug_job_id(str(job_id)) == job_id


# ── the public check ─────────────────────────────────────────────────────────


@pytest.fixture
def no_index(monkeypatch: pytest.MonkeyPatch) -> None:
    async def nothing(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(public_roles, "_indexed_role_for_ref", nothing)
    monkeypatch.setattr(public_roles, "_indexed_role_for_url", nothing)


async def test_unsupported_source_is_reported_as_such_without_any_request(no_index):
    with patch("ingestion.scrapers.ats_scraper.ATSScraper.check_posting", AsyncMock()) as check:
        result = await public_roles.check_url("https://www.linkedin.com/jobs/view/123456")
    assert result["status"] == "unsupported"
    assert result["liveness"] is None
    assert check.await_count == 0


async def test_a_role_already_in_the_index_returns_its_verdict(monkeypatch):
    indexed = {"slug": "backend-engineer-stripe-x", "title": "Backend Engineer", "company": "Stripe",
               "liveness": {"state": "ageing", "headline": "Open for 60 days", "evidence": [], "open_days": 60}}

    async def found(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return indexed

    monkeypatch.setattr(public_roles, "_indexed_role_for_ref", found)
    result = await public_roles.check_url("https://boards.greenhouse.io/stripe/jobs/101")
    assert result["status"] == "indexed"
    assert result["liveness"]["state"] == "ageing"
    assert result["role"]["slug"] == "backend-engineer-stripe-x"


@pytest.mark.parametrize(
    ("outcome", "status"),
    [(PostingCheck.OPEN, "open_now"), (PostingCheck.CLOSED, "closed_now"), (PostingCheck.ERROR, "unreachable")],
)
async def test_an_unknown_ats_posting_is_checked_live(no_index, outcome, status):
    with patch(
        "ingestion.scrapers.ats_scraper.ATSScraper.check_posting", AsyncMock(return_value=outcome)
    ) as check:
        result = await public_roles.check_url("https://boards.greenhouse.io/stripe/jobs/101")
    assert result["status"] == status
    assert check.await_args.args == ("greenhouse:stripe", "101")
    # We have never seen it before, so we claim nothing about its history.
    assert result["liveness"] is None


async def test_the_check_never_raises(monkeypatch):
    async def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database is down")

    monkeypatch.setattr(public_roles, "_indexed_role_for_ref", broken)
    monkeypatch.setattr(public_roles, "_indexed_role_for_url", broken)
    with patch(
        "ingestion.scrapers.ats_scraper.ATSScraper.check_posting",
        AsyncMock(return_value=PostingCheck.OPEN),
    ):
        result = await public_roles.check_url("https://boards.greenhouse.io/stripe/jobs/101")
    assert result["status"] == "open_now"


# ── what a public role may contain ───────────────────────────────────────────


def test_public_payload_is_built_only_from_republishable_fields():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    job = SimpleNamespace(
        id=uuid.uuid4(), title="Backend Engineer", company=SimpleNamespace(name="Stripe"),
        company_id=uuid.uuid4(), location_raw="Bengaluru", city="Bengaluru", is_remote=False,
        skills=["python"], source="greenhouse:stripe", source_url="https://boards.greenhouse.io/stripe/jobs/1",
        employment_type=None, seniority=None, salary_raw=None,
        source_posted_at=datetime(2026, 9, 1, tzinfo=UTC), first_seen_at=None, last_seen_at=None,
        last_verified_at=None, closed_at=None, view_count=41, apply_count=7,
        description_clean="internal notes", extra_metadata={"secret": 1},
    )
    payload = public_roles.public_role(job, None, now=datetime(2026, 10, 5, tzinfo=UTC))
    assert payload["title"] == "Backend Engineer"
    assert payload["company"] == "Stripe"
    assert payload["liveness"]["state"]
    assert set(payload) == {
        "id", "slug", "title", "company", "company_slug", "location", "is_remote", "skills",
        "employment_type", "seniority", "source_url", "posted_at", "closed_at", "liveness",
    }


def test_board_rows_are_not_publishable():
    assert public_roles.is_publishable_source("greenhouse:stripe") is True
    assert public_roles.is_publishable_source("lever:spotify") is True
    assert public_roles.is_publishable_source("linkedin") is False
    assert public_roles.is_publishable_source("foundit") is False
    assert public_roles.is_publishable_source("live_search") is False


# ── HTTP ─────────────────────────────────────────────────────────────────────


async def test_public_role_needs_no_account(api_client):
    role = {"id": "x", "slug": "s", "title": "Backend Engineer"}
    with patch("services.public_roles.get_role", AsyncMock(return_value=role)):
        response = await api_client.get("/api/v1/public/roles/backend-engineer-stripe-" + str(uuid.uuid4()))
    assert response.status_code == 200
    assert response.json() == role


async def test_unknown_public_role_is_a_404(api_client):
    with patch("services.public_roles.get_role", AsyncMock(return_value=None)):
        response = await api_client.get("/api/v1/public/roles/nope")
    assert response.status_code == 404


async def test_public_check_needs_no_account(api_client):
    answer = {"status": "unsupported", "liveness": None, "role": None, "message": "m"}
    with patch("services.public_roles.check_url", AsyncMock(return_value=answer)) as check:
        response = await api_client.post("/api/v1/public/check", json={"url": "https://x.example/job"})
    assert response.status_code == 200
    assert check.await_args.args == ("https://x.example/job",)


async def test_public_check_rejects_an_oversized_url(api_client):
    response = await api_client.post("/api/v1/public/check", json={"url": "https://x.example/" + "a" * 3000})
    assert response.status_code == 422


def test_the_public_check_is_on_the_strict_rate_limit():
    from api.rate_limit import STRICT_PATHS

    assert "/api/v1/public/check" in STRICT_PATHS
    assert "/api/auth/login" in STRICT_PATHS
