"""
tests/test_practice_steps.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Practice's scheduled steps: ``services/practice/steps.py``, the cron endpoint
``POST /api/v1/ingest/practice`` and ``.github/workflows/practice.yml``.

* The step result contract is pinned: ``challenges`` is exactly ``{editions_created, queued,
  missed, pruned_days, pruned_attempts}``; a step that fails comes back as ``{"error": ...}``
  and never raises.
* The services run for real on SQLite in a temporary file with ``PRAGMA foreign_keys=ON`` and
  the module's ``AsyncSessionLocal`` pointed at it. The shared Postgres test database is never
  touched. The briefs are one synthetic, fictional file in a temporary directory.
* The endpoint follows ``tests/test_reverify_endpoint.py``: closed unless the caller is an admin
  or holds the cron token.
* The workflow is parsed with ``yaml.safe_load`` and checked for the budget it must allow the
  judge step and for never naming the question site.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_args
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import yaml
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.routers.ingest import PracticeStep
from config.settings import get_settings
from services.practice import challenges, steps
from storage.database import Base
from storage.models import PracticeAttempt, PracticeChallenge, User

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "practice.yml"
URL = "/api/v1/ingest/practice"
RUN_STEP = "services.practice.steps.run_step"
RUN_JUDGE = "services.practice.judge.run_judge_step"

CONTRACT = {"editions_created", "queued", "missed", "pruned_days", "pruned_attempts"}
JUDGE_COUNTS = {
    "processed": 2,
    "judged": 1,
    "rejected": 1,
    "held": 0,
    "retried": 0,
    "failed": 0,
    "errors": 0,
    "budget_exhausted": 0,
}


# --------------------------------------------------------------------------- the database


@dataclass
class Db:
    factory: async_sessionmaker[AsyncSession]


def make_brief(slug: str) -> dict[str, Any]:
    """A synthetic, fictional brief that satisfies the schema."""
    return {
        "slug": slug,
        "title": f"Sample build {slug}",
        "track": "backend",
        "hours": 48,
        "summary": f"One line about {slug}.",
        "context": ["A fictional team needs this."],
        "must_have": ["Does the thing."],
        "nice_to_have": [],
        "constraints": ["No paid APIs."],
        "deliverables": ["Public repo with a README."],
        "judge_focus": ["How the edge cases are handled."],
        "bengaluru_context": "Teams at fictional Examplepay-like companies own this work.",
    }


@pytest.fixture
def briefs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "briefs"
    directory.mkdir()
    (directory / "01-relay-sample.json").write_text(
        json.dumps(make_brief("relay-sample")), encoding="utf-8"
    )
    monkeypatch.setattr(challenges, "BRIEFS_DIR", directory)
    return directory


@pytest_asyncio.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Db]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'practice.db').as_posix()}")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys_on(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables = [
        Base.metadata.tables[name]
        for name in ("users", "practice_challenges", "practice_attempts", "practice_days")
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(challenges, "AsyncSessionLocal", factory)
    yield Db(factory=factory)
    await engine.dispose()


async def editions_of(db: Db) -> list[PracticeChallenge]:
    async with db.factory() as session:
        rows = await session.execute(select(PracticeChallenge).order_by(PracticeChallenge.opens_at))
        return list(rows.scalars().all())


async def statuses_of(db: Db) -> list[str]:
    async with db.factory() as session:
        found = (await session.execute(select(PracticeAttempt.status))).scalars().all()
    return sorted(found)


async def add_due_attempts(db: Db) -> None:
    """A submitted attempt and a started one whose deadline and grace hour are over."""
    now = datetime.now(UTC)
    async with db.factory() as session:
        edition = PracticeChallenge(
            slug="seed-edition",
            brief_slug="seed",
            title="Seed",
            track="backend",
            hours=24,
            brief=make_brief("seed"),
            opens_at=now - timedelta(days=3),
            closes_at=now + timedelta(days=4),
        )
        sana = User(email="sana@example.com", hashed_password="not-a-real-hash")
        ira = User(email="ira@example.com", hashed_password="not-a-real-hash")
        session.add_all([edition, sana, ira])
        await session.flush()
        for user, status, code in (
            (sana, "submitted", "SANA1PROOF"),
            (ira, "started", "IRA01PROOF"),
        ):
            session.add(
                PracticeAttempt(
                    user_id=user.id,
                    challenge_id=edition.id,
                    status=status,
                    started_at=now - timedelta(hours=30),
                    deadline_at=now - timedelta(hours=3),
                    proof_code=code,
                )
            )
        await session.commit()


# --------------------------------------------------------------------------- the steps


class TestChallengesStep:
    async def test_it_returns_exactly_the_contract_keys(self) -> None:
        with (
            patch.object(challenges, "ensure_editions", AsyncMock(return_value=2)),
            patch.object(
                challenges, "advance_statuses", AsyncMock(return_value={"queued": 3, "missed": 1})
            ),
            patch.object(
                challenges,
                "prune",
                AsyncMock(return_value={"pruned_days": 5, "pruned_attempts": 4}),
            ),
        ):
            result = await steps.run_step("challenges", 1)

        assert result == {
            "editions_created": 2,
            "queued": 3,
            "missed": 1,
            "pruned_days": 5,
            "pruned_attempts": 4,
        }

    async def test_the_limit_does_not_apply_to_it(self) -> None:
        ensure = AsyncMock(return_value=0)
        with (
            patch.object(challenges, "ensure_editions", ensure),
            patch.object(
                challenges, "advance_statuses", AsyncMock(return_value={"queued": 0, "missed": 0})
            ),
            patch.object(
                challenges,
                "prune",
                AsyncMock(return_value={"pruned_days": 0, "pruned_attempts": 0}),
            ),
        ):
            await steps.run_step("challenges", 10)

        ensure.assert_awaited_once_with()

    async def test_on_a_database_it_makes_this_weeks_and_next_weeks_edition(
        self, db: Db, briefs_dir: Path
    ) -> None:
        result = await steps.run_step("challenges", 1)

        assert result == {
            "editions_created": 2,
            "queued": 0,
            "missed": 0,
            "pruned_days": 0,
            "pruned_attempts": 0,
        }
        assert len(await editions_of(db)) == 2

    async def test_a_second_run_changes_nothing(self, db: Db, briefs_dir: Path) -> None:
        await steps.run_step("challenges", 1)

        again = await steps.run_step("challenges", 1)

        assert again["editions_created"] == 0
        assert len(await editions_of(db)) == 2

    async def test_two_runs_at_once_never_make_two_editions_for_one_opening(
        self, db: Db, briefs_dir: Path
    ) -> None:
        first, second = await asyncio.gather(
            steps.run_step("challenges", 1), steps.run_step("challenges", 1)
        )

        assert "error" not in first and "error" not in second
        assert first["editions_created"] + second["editions_created"] == 2
        openings = [row.opens_at for row in await editions_of(db)]
        assert len(openings) == 2
        assert len(set(openings)) == 2

    async def test_after_the_grace_hour_it_queues_and_marks_missed(
        self, db: Db, briefs_dir: Path
    ) -> None:
        await add_due_attempts(db)

        result = await steps.run_step("challenges", 1)

        assert result["queued"] == 1 and result["missed"] == 1
        assert set(result) == CONTRACT
        assert await statuses_of(db) == ["missed", "queued"]

    @pytest.mark.parametrize("failing", ["ensure_editions", "advance_statuses", "prune"])
    async def test_a_failing_part_is_an_error_and_the_others_still_run(self, failing: str) -> None:
        parts = {
            "ensure_editions": AsyncMock(return_value=2),
            "advance_statuses": AsyncMock(return_value={"queued": 3, "missed": 1}),
            "prune": AsyncMock(return_value={"pruned_days": 5, "pruned_attempts": 4}),
        }
        parts[failing] = AsyncMock(side_effect=RuntimeError("secret-token-123"))
        with (
            patch.object(challenges, "ensure_editions", parts["ensure_editions"]),
            patch.object(challenges, "advance_statuses", parts["advance_statuses"]),
            patch.object(challenges, "prune", parts["prune"]),
        ):
            result = await steps.run_step("challenges", 1)

        assert result["error"] == "RuntimeError"
        assert "secret-token-123" not in repr(result)  # the class name only
        assert set(result) == CONTRACT | {"error"}
        for part in parts.values():
            assert part.await_count == 1
        zeroed = {
            "ensure_editions": {"editions_created"},
            "advance_statuses": {"queued", "missed"},
            "prune": {"pruned_days", "pruned_attempts"},
        }[failing]
        assert all(result[key] == 0 for key in zeroed)
        assert all(result[key] != 0 for key in CONTRACT - zeroed)

    async def test_when_everything_fails_it_still_returns_instead_of_raising(self) -> None:
        boom = AsyncMock(side_effect=RuntimeError("down"))
        with (
            patch.object(challenges, "ensure_editions", boom),
            patch.object(challenges, "advance_statuses", boom),
            patch.object(challenges, "prune", boom),
        ):
            result = await steps.run_step("challenges", 1)

        assert result == {
            "editions_created": 0,
            "queued": 0,
            "missed": 0,
            "pruned_days": 0,
            "pruned_attempts": 0,
            "error": "RuntimeError",
        }

    async def test_a_broken_brief_file_does_not_stop_submissions_being_queued(
        self, db: Db, briefs_dir: Path
    ) -> None:
        (briefs_dir / "02-bad-sample.json").write_text(
            json.dumps({**make_brief("bad-sample"), "hours": 72}), encoding="utf-8"
        )
        await add_due_attempts(db)

        result = await steps.run_step("challenges", 1)

        assert result["error"] == "PracticeInvalid"
        assert result["editions_created"] == 0
        assert result["queued"] == 1 and result["missed"] == 1
        assert await statuses_of(db) == ["missed", "queued"]
        assert len(await editions_of(db)) == 1  # only the seed edition the attempts hang on


class TestJudgeStep:
    async def test_it_delegates_to_the_judge_with_the_limit(self) -> None:
        with patch(RUN_JUDGE, AsyncMock(return_value=JUDGE_COUNTS)) as run:
            result = await steps.run_step("judge", 4)

        run.assert_awaited_once_with(4)
        assert result == JUDGE_COUNTS

    async def test_a_service_that_raises_is_an_error(self) -> None:
        with patch(RUN_JUDGE, AsyncMock(side_effect=RuntimeError("secret-token-123"))):
            result = await steps.run_step("judge", 4)

        assert result == {"error": "RuntimeError"}

    async def test_the_judge_step_does_not_touch_the_challenge_services(self) -> None:
        ensure = AsyncMock(return_value=0)
        with (
            patch(RUN_JUDGE, AsyncMock(return_value=JUDGE_COUNTS)),
            patch.object(challenges, "ensure_editions", ensure),
        ):
            await steps.run_step("judge", 1)

        assert ensure.await_count == 0


class TestUnknownStep:
    @pytest.mark.parametrize("name", ["", "everything", "Judge", "challenges "])
    async def test_it_is_an_error_and_runs_nothing(self, name: str) -> None:
        ensure = AsyncMock(return_value=0)
        with (
            patch(RUN_JUDGE, AsyncMock(return_value=JUDGE_COUNTS)) as judge_run,
            patch.object(challenges, "ensure_editions", ensure),
        ):
            result = await steps.run_step(name, 4)

        assert result == {"error": "unknown step"}
        assert judge_run.await_count == 0
        assert ensure.await_count == 0


def test_the_endpoint_accepts_exactly_the_steps() -> None:
    assert steps.STEPS == ("challenges", "judge")
    assert set(get_args(PracticeStep)) == set(steps.STEPS)


# --------------------------------------------------------------------------- the endpoint


@pytest.fixture
def cron_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_settings(), "cron_token", "a-long-shared-secret")
    return "a-long-shared-secret"


async def test_anonymous_caller_is_refused(api_client: AsyncClient, cron_token: str) -> None:
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await api_client.post(f"{URL}?step=judge")
    assert response.status_code == 403
    assert run.await_count == 0


async def test_wrong_token_is_refused(api_client: AsyncClient, cron_token: str) -> None:
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await api_client.post(f"{URL}?step=judge", headers={"X-Cron-Token": "guess"})
    assert response.status_code == 403
    assert run.await_count == 0


async def test_no_token_configured_means_no_token_is_accepted(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "cron_token", None)
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await api_client.post(f"{URL}?step=judge", headers={"X-Cron-Token": ""})
    assert response.status_code == 403
    assert run.await_count == 0


async def test_right_token_runs_the_step(api_client: AsyncClient, cron_token: str) -> None:
    counts = {
        "editions_created": 2,
        "queued": 0,
        "missed": 0,
        "pruned_days": 0,
        "pruned_attempts": 0,
    }
    with patch(RUN_STEP, AsyncMock(return_value=counts)) as run:
        response = await api_client.post(
            f"{URL}?step=challenges&limit=1", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 200
    assert response.json() == counts
    assert run.await_args is not None
    assert run.await_args.args == ("challenges", 1)


async def test_the_judge_limit_is_forwarded(api_client: AsyncClient, cron_token: str) -> None:
    with patch(RUN_STEP, AsyncMock(return_value=JUDGE_COUNTS)) as run:
        response = await api_client.post(
            f"{URL}?step=judge&limit=4", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 200
    assert response.json() == JUDGE_COUNTS
    assert run.await_args is not None
    assert run.await_args.args == ("judge", 4)


async def test_a_step_that_failed_is_still_a_200_with_the_error(
    api_client: AsyncClient, cron_token: str
) -> None:
    # The step never raises, so the workflow reads the body instead of a stack trace.
    with patch(RUN_JUDGE, AsyncMock(side_effect=RuntimeError("down"))):
        response = await api_client.post(
            f"{URL}?step=judge&limit=4", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 200
    assert response.json() == {"error": "RuntimeError"}


@pytest.mark.parametrize("query", ["step=everything", "step=", "", "step=Judge"])
async def test_an_unknown_step_is_rejected(
    api_client: AsyncClient, cron_token: str, query: str
) -> None:
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await api_client.post(f"{URL}?{query}", headers={"X-Cron-Token": cron_token})
    assert response.status_code == 422
    assert run.await_count == 0


@pytest.mark.parametrize("limit", [0, 11, -1, "many"])
async def test_the_limit_is_one_to_ten(
    api_client: AsyncClient, cron_token: str, limit: int | str
) -> None:
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await api_client.post(
            f"{URL}?step=judge&limit={limit}", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 422
    assert run.await_count == 0


@pytest.mark.parametrize("limit", [1, 10])
async def test_the_limit_bounds_are_accepted(
    api_client: AsyncClient, cron_token: str, limit: int
) -> None:
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await api_client.post(
            f"{URL}?step=judge&limit={limit}", headers={"X-Cron-Token": cron_token}
        )
    assert response.status_code == 200
    assert run.await_args is not None
    assert run.await_args.args == ("judge", limit)


async def test_admin_may_run_it_without_the_token(admin_client: AsyncClient) -> None:
    with patch(RUN_STEP, AsyncMock(return_value={"error": "x"})):
        response = await admin_client.post(f"{URL}?step=judge")
    assert response.status_code == 200


async def test_ordinary_user_may_not(auth_client: AsyncClient) -> None:
    with patch(RUN_STEP, AsyncMock(return_value={})) as run:
        response = await auth_client.post(f"{URL}?step=judge")
    assert response.status_code == 403
    assert run.await_count == 0


# --------------------------------------------------------------------------- the workflow


def _workflow() -> dict[Any, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # PyYAML reads the bare key ``on`` as the boolean True (YAML 1.1).
    triggers: dict[str, Any] = workflow[True] if True in workflow else workflow["on"]
    return triggers


def _job() -> dict[str, Any]:
    job: dict[str, Any] = _workflow()["jobs"]["practice"]
    return job


def _step(name: str) -> dict[str, Any]:
    (found,) = [step for step in _job()["steps"] if step.get("name") == name]
    return dict(found)


def _run_script() -> str:
    return str(_step("Run Practice steps")["run"])


def test_workflow_never_calls_leetcode() -> None:
    assert "leetcode" not in WORKFLOW.read_text(encoding="utf-8").lower()


def test_workflow_runs_every_two_hours_and_by_hand() -> None:
    workflow = _workflow()
    assert [entry["cron"] for entry in _triggers(workflow)["schedule"]] == ["7 */2 * * *"]
    assert "workflow_dispatch" in _triggers(workflow)
    assert workflow["concurrency"] == {"group": "practice", "cancel-in-progress": False}


def test_workflow_calls_the_two_steps_in_order_with_the_cron_token() -> None:
    script = _run_script()
    assert "/api/v1/ingest/practice?step=$1&limit=$2" in script
    assert "X-Cron-Token: $CRON_TOKEN" in script
    calls = re.findall(r"^\s*practice (\w+) (\d+)\s*$", script, flags=re.MULTILINE)
    assert calls == [("challenges", "1"), ("judge", "4")]
    assert script.count('|| echo "::warning::') == 1  # in the one helper both calls go through


def test_workflow_gates_on_the_token_and_wakes_the_api_before_any_call() -> None:
    names = [step.get("name") or step.get("uses") for step in _job()["steps"]]
    assert names == [
        "Skip when no token is configured",
        "actions/checkout@v4",
        "Wake the API",
        "Run Practice steps",
    ]
    assert _job()["env"]["CRON_TOKEN"] == "${{ secrets.CRON_TOKEN }}"
    assert "CRON_TOKEN" in str(_step("Skip when no token is configured")["run"])
    checkout = next(step for step in _job()["steps"] if step.get("uses") == "actions/checkout@v4")
    assert checkout["with"]["sparse-checkout"] == "scripts/keepalive.sh"
    assert _step("Wake the API")["run"] == "bash scripts/keepalive.sh"
    for later in ("actions/checkout@v4", "Wake the API", "Run Practice steps"):
        step = next(s for s in _job()["steps"] if later in (s.get("name"), s.get("uses")))
        assert step["if"] == "steps.gate.outputs.run == 'true'"


def test_each_call_is_allowed_long_enough_for_the_judge_and_the_job_for_all_of_it() -> None:
    # The judge step spaces up to four model calls 65 seconds apart: about 4 x 65 s.
    max_times = [int(seconds) for seconds in re.findall(r"--max-time (\d+)", _run_script())]
    assert max_times and all(seconds >= 600 for seconds in max_times)
    wake = int(_step("Wake the API")["env"]["DEADLINE_SECONDS"])
    calls = len(re.findall(r"^\s*practice \w+ \d+\s*$", _run_script(), flags=re.MULTILINE))
    assert _job()["timeout-minutes"] * 60 >= wake + calls * max(max_times)
    assert _job()["timeout-minutes"] * 60 > max(max_times)
