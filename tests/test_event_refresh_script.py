"""
tests/test_event_refresh_script.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
``scripts/refresh_events.py``: the local runner of the same steps the workflow runs.
No database, no network: ``run_step`` is replaced.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import refresh_events
from services.events.refresh import MAX_LIMIT

REPO = Path(__file__).resolve().parent.parent


class Recorder:
    def __init__(self, failing: str | None = None) -> None:
        self.calls: list[tuple[str, int, str | None]] = []
        self.failing = failing

    async def __call__(
        self, step: str, limit: int, *, source: str | None = None
    ) -> dict[str, int | str]:
        self.calls.append((step, limit, source))
        if step == self.failing:
            return {"step": step, "error": "RuntimeError: boom"}
        return {"step": step, "seen": 1}


async def test_all_runs_every_routine_step_in_order_but_not_purge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(refresh_events, "run_step", recorder)

    status = await refresh_events.run("all", None, None)

    assert status == 0
    assert [call[0] for call in recorder.calls] == [
        "luma", "gdg", "eventbrite", "link", "relevance", "prune",
    ]  # fmt: skip
    assert [call[1] for call in recorder.calls] == [
        26,
        30,
        60,
        500,
        2000,
        1,
    ]  # each its own ceiling
    printed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["step"] for line in printed] == [call[0] for call in recorder.calls]


async def test_a_failing_step_does_not_stop_the_next_and_sets_the_exit_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorder = Recorder(failing="gdg")
    monkeypatch.setattr(refresh_events, "run_step", recorder)

    status = await refresh_events.run("all", 5, None)

    assert status == 1
    assert len(recorder.calls) == 6
    assert all(call[1] == 5 for call in recorder.calls)
    assert "RuntimeError: boom" in capsys.readouterr().out


async def test_one_step_with_a_limit_and_a_source(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(refresh_events, "run_step", recorder)

    assert await refresh_events.run("purge", None, "eventbrite") == 0
    assert recorder.calls == [("purge", MAX_LIMIT["purge"], "eventbrite")]


def test_the_parser_knows_every_step_and_all() -> None:
    parser = refresh_events.build_parser()
    for step in ("all", "luma", "gdg", "eventbrite", "link", "relevance", "prune", "purge"):
        assert parser.parse_args(["--step", step]).step == step
    args = parser.parse_args(["--step", "gdg", "--limit", "7", "--source", "eventbrite"])
    assert (args.step, args.limit, args.source) == ("gdg", 7, "eventbrite")
    assert parser.parse_args([]).step == "all" and parser.parse_args([]).limit is None
    with pytest.raises(SystemExit):
        parser.parse_args(["--step", "nope"])


def test_the_script_finds_this_checkout_from_any_working_directory(tmp_path: Path) -> None:
    # Run as a file, not as a module: sys.path[0] is scripts/, so only the script's own
    # sys.path line can make `services` importable, and it must be this checkout's.
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "refresh_events.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--step" in result.stdout
