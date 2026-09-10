"""
tests/test_job_persistence.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for writing live-scraped jobs without an LLM call per posting.

The database write itself is covered by the integration suite; what is pinned
here is everything that decides *what* gets written, because those are the
rules that keep a 0.5 GB Neon instance and a free Groq tier alive.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from domain.entities import Job
from domain.enums import EmploymentType, JobStatus, SeniorityLevel
from services.cache_backend import CacheBackend
from services.job_persistence import (
    MAX_DESCRIPTION_CHARS,
    _stable_external_id,
    build_job_kwargs,
    embedding_text,
    persist_live_jobs,
)


def make_job(
    *,
    title: str = "Python Developer",
    company: str = "Acme Corp",
    url: str = "https://www.foundit.in/job/12345",
    skills: list[str] | None = None,
    description: str = "",
) -> Job:
    return Job(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        external_id=None,
        source="foundit",
        source_url=url,
        title=title,
        status=JobStatus.ACTIVE,
        employment_type=EmploymentType.FULL_TIME,
        seniority=SeniorityLevel.MID,
        location_raw="Bengaluru, India",
        country="India",
        city="Bengaluru",
        is_remote=False,
        skills=skills if skills is not None else ["Python", "Django"],
        tags=["foundit"],
        description_clean=description,
        posted_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        extra_metadata={"company_name": company},
    )


@pytest.fixture(autouse=True)
async def memory_cache():
    await CacheBackend.reset()
    CacheBackend._client = None
    CacheBackend._initialised = True
    yield
    await CacheBackend.reset()


# ── external id stability ────────────────────────────────────────────────────

def test_external_id_is_stable_for_the_same_posting():
    """
    An unstable id re-inserts the same posting on every scrape. On a 0.5 GB
    database that is a storage leak, not a cosmetic bug.
    """
    assert _stable_external_id(make_job()) == _stable_external_id(make_job())


def test_external_id_differs_between_postings():
    a = _stable_external_id(make_job(url="https://www.foundit.in/job/1"))
    b = _stable_external_id(make_job(url="https://www.foundit.in/job/2"))
    assert a != b


def test_external_id_falls_back_to_title_and_company_without_a_url():
    job = make_job(url="")
    assert _stable_external_id(job) == _stable_external_id(make_job(url=""))


# ── structural mapping ───────────────────────────────────────────────────────

def test_build_job_kwargs_marks_rows_raw():
    """The whole point: written now, LLM-parsed later, under budget."""
    assert build_job_kwargs(make_job())["enrichment_status"] == "raw"


def test_build_job_kwargs_carries_scraped_fields():
    kwargs = build_job_kwargs(make_job())
    assert kwargs["title"] == "Python Developer"
    assert kwargs["city"] == "Bengaluru"
    assert kwargs["country"] == "India"
    assert kwargs["skills"] == ["Python", "Django"]


def test_descriptions_are_truncated():
    """Neon's free tier is 0.5 GB and scraped descriptions are mostly boilerplate."""
    kwargs = build_job_kwargs(make_job(description="x" * 20_000))
    assert len(kwargs["description_clean"]) == MAX_DESCRIPTION_CHARS


# ── embedding text ───────────────────────────────────────────────────────────

def test_embedding_text_works_without_a_description():
    """
    A scraped posting usually has no description. Embedding that alone gives a
    near-empty vector, and the row never surfaces in a semantic search.
    """
    text = embedding_text(make_job(description=""))
    assert "Python Developer" in text
    assert "Acme Corp" in text
    assert "Python" in text
    assert text.strip()


def test_embedding_text_skips_empty_parts():
    text = embedding_text(make_job(skills=[], description=""))
    assert "| |" not in text


# ── filtering ────────────────────────────────────────────────────────────────

async def test_listing_pages_are_rejected_before_the_database():
    summary = await persist_live_jobs(
        [make_job(url="https://www.foundit.in/srp/results?query=python")]
    )
    assert summary.rejected_url == 1
    assert summary.inserted == 0


async def test_already_seen_urls_are_skipped():
    from services.job_persistence import SEEN_URL_TTL_SECONDS, _seen_key

    job = make_job()
    await CacheBackend.set(_seen_key(job.source_url or ""), "1", SEEN_URL_TTL_SECONDS)

    summary = await persist_live_jobs([job])
    assert summary.duplicate == 1
    assert summary.inserted == 0


async def test_empty_input_is_a_no_op():
    summary = await persist_live_jobs([])
    assert summary.as_dict() == {
        "received": 0,
        "rejected_url": 0,
        "duplicate": 0,
        "inserted": 0,
        "updated": 0,
        "failed": 0,
    }


async def test_persistence_never_raises_when_the_database_is_unreachable(monkeypatch):
    """
    This runs as a background task behind a user's search. A database failure
    must not surface to a user who already has their results.
    """
    import storage.database as db

    def explode(*args, **kwargs):
        raise RuntimeError("no database configured")

    monkeypatch.setattr(db, "AsyncSessionLocal", explode)

    summary = await persist_live_jobs([make_job()])
    assert summary.inserted == 0
    assert summary.received == 1


async def test_urls_are_not_marked_seen_when_the_write_fails(monkeypatch):
    """Otherwise a failed run permanently blacklists the jobs it failed on."""
    import storage.database as db
    from services.job_persistence import _seen_key

    def explode(*args, **kwargs):
        raise RuntimeError("no database configured")

    monkeypatch.setattr(db, "AsyncSessionLocal", explode)

    job = make_job()
    await persist_live_jobs([job])
    assert await CacheBackend.exists(_seen_key(job.source_url or "")) is False
