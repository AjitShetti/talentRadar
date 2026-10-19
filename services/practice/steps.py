"""
services/practice/steps.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
The scheduled work behind Practice, one call per step.

``POST /api/v1/ingest/practice?step=…`` (run every two hours by ``.github/workflows/practice.yml``;
there is no worker) calls :func:`run_step`. A step:

* ``challenges`` - ``ensure_editions`` (this week's and next week's edition), then
  ``advance_statuses`` (``submitted`` becomes ``queued`` and ``started`` becomes ``missed`` once
  the grace hour is over), then ``prune`` (the retention rules). The three are independent:
  one that fails does not stop the others, because a broken brief file must not leave
  submissions unqueued. Its result is exactly ``{editions_created, queued, missed,
  pruned_days, pruned_attempts}``; ``limit`` does not apply to it.
* ``judge`` - ``run_judge_step(limit)``: judge up to ``limit`` queued attempts, spaced for the
  model's rate limit, inside the day's budget. This is the only step that calls a model.

:func:`run_step` never raises. A part that fails is logged by its class name only (an exception's
text can carry a URL or a token) and comes back as ``{"error": <class name>}``; for
``challenges`` the counts of the parts that did run stay in the result next to ``error``. An
unknown step is ``{"error": "unknown step"}``. Every service commits per unit of work, so a
failure part-way leaves what it finished and the next run repeats the rest.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from services.practice import challenges, judge

logger = logging.getLogger(__name__)

STEPS = ("challenges", "judge")

_T = TypeVar("_T")


async def _attempt(
    name: str, call: Callable[[], Awaitable[_T]], fallback: _T, failures: list[str]
) -> _T:
    """Run one part; on any failure note its class name in ``failures`` and use ``fallback``."""
    try:
        return await call()
    except Exception as exc:
        logger.warning("practice: %s failed (%s)", name, type(exc).__name__)
        failures.append(type(exc).__name__)
        return fallback


async def _challenges_step() -> dict[str, Any]:
    failures: list[str] = []
    created = await _attempt("ensure_editions", challenges.ensure_editions, 0, failures)
    moved = await _attempt(
        "advance_statuses", challenges.advance_statuses, {"queued": 0, "missed": 0}, failures
    )
    pruned = await _attempt(
        "prune", challenges.prune, {"pruned_days": 0, "pruned_attempts": 0}, failures
    )
    result: dict[str, Any] = {
        "editions_created": created,
        "queued": moved["queued"],
        "missed": moved["missed"],
        "pruned_days": pruned["pruned_days"],
        "pruned_attempts": pruned["pruned_attempts"],
    }
    if failures:
        result["error"] = ",".join(dict.fromkeys(failures))
    return result


async def _judge_step(limit: int) -> dict[str, Any]:
    try:
        return dict(await judge.run_judge_step(limit))
    except Exception as exc:
        logger.warning("practice: the judge step failed (%s)", type(exc).__name__)
        return {"error": type(exc).__name__}


async def run_step(step: str, limit: int) -> dict[str, Any]:
    """Run one scheduled step and return its counts. Never raises."""
    if step == "challenges":
        return await _challenges_step()
    if step == "judge":
        return await _judge_step(limit)
    return {"error": "unknown step"}
