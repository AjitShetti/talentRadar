"""
tests/test_event_workflow.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Pins the shape of ``.github/workflows/events.yml``, the schedule that keeps the Events list
fresh. Every step calls the Events refresh endpoint with the cron token, the per-step limits
are the ceilings the API applies, and prune runs only in the 23:xx UTC hour.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from services.events.refresh import MAX_LIMIT

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "events.yml"
STEPS = ("luma", "gdg", "eventbrite", "link", "relevance", "prune")


def _workflow() -> dict[Any, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # PyYAML reads the bare key ``on`` as the boolean True (YAML 1.1).
    triggers: dict[str, Any] = workflow[True] if True in workflow else workflow["on"]
    return triggers


def _run_script() -> str:
    steps = _workflow()["jobs"]["refresh"]["steps"]
    (step,) = [s for s in steps if s.get("name") == "Run refresh steps"]
    return str(step["run"])


def test_events_workflow_shape() -> None:
    crons = {entry["cron"] for entry in _triggers(_workflow())["schedule"]}
    assert {"20 23 * * *", "20 11 * * *"} <= crons

    script = _run_script()
    assert "/api/v1/ingest/events/refresh?step=" in script
    assert "X-Cron-Token" in script
    for step in STEPS:
        assert step in script


def test_dispatch_defaults_to_all_and_runs_queue_in_one_group() -> None:
    workflow = _workflow()
    assert _triggers(workflow)["workflow_dispatch"]["inputs"]["step"]["default"] == "all"
    assert workflow["concurrency"] == {"group": "events", "cancel-in-progress": False}


def test_per_step_limits_are_the_api_ceilings() -> None:
    calls = {step: int(limit) for step, limit in re.findall(r"refresh (\w+) (\d+)", _run_script())}
    assert calls == {step: MAX_LIMIT[step] for step in STEPS}


def test_prune_runs_only_in_the_23_utc_hour() -> None:
    script = _run_script()
    assert script.count("run_one prune") == 1
    assert 'if [ "$(date -u +%H)" = "23" ]; then run_one prune; fi' in script
