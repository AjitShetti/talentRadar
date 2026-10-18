"""
services/practice/judge.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Judging a take-home: read the candidate's repo through GitHub, ask the model once, store the
verdict, and keep the whole thing honest when anything goes wrong.

The rules (the rubric, the prompt, the fences, reading a reply) are in ``domain/practice_judge``
and ``domain/practice_repo``; this module is the order things happen in and what is written.
Nothing is executed and nothing is sent anywhere: the judge's output is advisory, stored on the
attempt row for its owner and an admin to read.

What ``judge_attempt`` does with a ``queued`` attempt, in this order:

1. **Read the repo** (at most 12 GitHub requests, never cloned): the repo itself, the push
   history, the file tree at the snapshot, the proof file, the commits, the README and up to
   four key files. The snapshot is the last push at or before ``deadline_at``; later pushes,
   force pushes included, are counted and ignored. The tree is read before the proof file so
   a commit GitHub no longer has (``snapshot_gone``) is told from a missing file
   (``proof_missing``). The repo's creation time is checked again here, from GitHub, because
   the save-time check may have been skipped when GitHub was down.
2. **Reject without a model call** a private repo, a fork, a repo made before Start, a repo
   with no push in the window, a missing or wrong ``.talentradar``, a repo that is gone, a
   commit that is gone, or a repo too big to read. The reason is stored and shown to the owner.
3. **Hold without a model call** when the candidate's own text reads as aimed at a grader
   (``input_markers`` over the original strings, never the cleaned prompt copies). A held
   attempt stays ``queued`` with ``held`` set and is not taken again until an admin clears it
   (``admin_clear``); text that merely discusses prompts, as in a repo built for an AI brief,
   can be held falsely, and the clear is how that is put right. A clear is for the input that
   was held (its hash): the same input goes ahead, changed text is checked again. Every tree
   path is scanned, though the prompt is built from the first 2,000.
4. **Spend one unit of the day's budget**, then ask the model once and read its reply strictly.
5. **Store** the scores, total, notes, the run (model, prompt version, input hash) and the
   snapshot facts in one conditional UPDATE.

A model error, a timeout, an unreadable reply, a spent budget or a GitHub error never produces a
score: the attempt stays ``queued`` with ``judge_attempts`` up and ``next_judge_at`` backed off
(2, 4, 8, 16, 24 hours), and becomes ``judge_failed`` on the sixth failure. A spent budget is not
a failure and does not count. There is never a fallback score.

An appeal (``request_appeal``) stores the owner's reason for an admin, which never reaches the
model, and marks the attempt for a second blind reading of the same input. That reading is made
by the scheduled step, like every other model call; the two readings are averaged. The second
reading is checked like a first one (text aimed at the grader holds the attempt) and is made
only of the input the first reading saw; a result a person has reviewed cannot be appealed.

Logs carry attempt ids, flag names and exception class names. Candidate text, exception messages
(which can echo it) and appeal reasons are never logged.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.engine import CursorResult, Result
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from domain.practice_judge import (
    CAP_FILE,
    CAP_README,
    CRITERIA,
    JUDGE_PROMPT_VERSION,
    WEIGHTS,
    JudgeInput,
    Verdict,
    VerdictError,
    average_runs,
    build_judge_prompt,
    input_markers,
    input_sha256,
    parse_verdict,
    total,
)
from domain.practice_repo import (
    PROOF_FILE,
    REPO_CREATION_GRACE,
    CommitLine,
    Push,
    RepoRef,
    TreeEntry,
    parse_timestamp,
    pick_key_files,
    proof_matches,
    snapshot,
)
from domain.practice_signals import WRITEUP_TEXT_FIELDS, compute_signals
from services.base import parse_uuid
from services.cache_backend import CacheBackend
from services.llm import _chat
from services.practice.errors import PracticeConflict, PracticeInvalid, PracticeNotFound
from services.practice.github_reader import (
    GitHubUnavailable,
    ReplyTooLarge,
    RepoGone,
    fetch_commits,
    fetch_pushes,
    fetch_repo,
    fetch_text,
    fetch_tree,
    github_client,
)
from storage.database import AsyncSessionLocal
from storage.models import PracticeAttempt, PracticeChallenge

logger = logging.getLogger(__name__)

#: Model calls per day across the whole deployment (about 100k tokens), counted like
#: ``services/job_enrichment.py::_consume_budget``.
DAILY_JUDGE_BUDGET = 12
#: Attempts one scheduled run takes; two without a GitHub token (60 requests an hour, shared IP).
JUDGE_PER_RUN_MAX = 4
JUDGE_PER_RUN_MAX_NO_TOKEN = 2
#: Seconds between two model calls in one run (the per-minute token limit of the free tier).
JUDGE_SPACING_SECONDS = 65
#: Wait after the first, second, ... fifth failure; the sixth makes the attempt ``judge_failed``.
BACKOFF_HOURS = (2, 4, 8, 16, 24)
MAX_JUDGE_ATTEMPTS = 6

#: Reasoning tokens are billed against ``max_tokens``; 900 truncated the verdict.
JUDGE_MAX_TOKENS = 3_000
JUDGE_REASONING_EFFORT = "medium"

APPEAL_WINDOW = timedelta(days=7)
APPEAL_REASON_CHARS = 500
OVERRIDE_NOTE_CHARS = 500

INJECTION_FLAG = "injection_text"
#: Added by ``admin_clear`` as ``hold_cleared:<input hash>``: an admin has read the input that
#: was held, so exactly that input is not held again. Any other input is checked afresh.
HOLD_CLEARED_FLAG = "hold_cleared"
#: Added when an appeal's second reading would have been of a different input from the first:
#: the two are never averaged, so the appeal is left for an admin and the first score stands.
APPEAL_INPUT_CHANGED_FLAG = "appeal_input_changed"

_BUDGET_KEY_PREFIX = "tr:budget:judge:"
_BUDGET_TTL_SECONDS = 48 * 3600

#: How much of each file is fetched, in bytes: enough for ``CAP_*`` characters in any encoding.
_PROOF_BYTES = 256
_README_BYTES = CAP_README * 4
_FILE_BYTES = CAP_FILE * 4
#: Tree paths handed to the prompt builder; it shows 250, and the rest are only hashed and scanned.
_INPUT_TREE_PATHS = 2_000

_QUEUED = "queued"
_JUDGED = "judged"
#: Where an admin override makes sense: the attempt has reached the judge, or been through it.
_OVERRIDABLE = (_QUEUED, _JUDGED, "judge_failed", "rejected")

#: What the owner reads for each ``reject_reason`` code.
REJECT_MESSAGES: dict[str, str] = {
    "no_submission": "No repo was saved with this build, so there is nothing to judge.",
    "repo_gone": "The repo is gone or private.",
    "repo_private": "The repo is gone or private.",
    "repo_fork": "The repo is a fork. Create a new repo after you press Start.",
    "created_before_start": "The repo was created before you pressed Start.",
    "no_push_in_window": "Nothing was pushed to the default branch before the deadline.",
    "snapshot_gone": "The judged commit is no longer on GitHub.",
    "proof_missing": (
        "The .talentradar file with your proof code was not in the repo at the judged commit."
    ),
    "repo_too_large": "The repo is too large to read.",
}

_NO_ATTEMPT = "We could not find your attempt at that build."
_NO_SUCH_ATTEMPT = "We could not find that attempt."
_NO_ACCOUNT = "We could not find your account."
_NOTHING_TO_APPEAL = "You can appeal once your build has been judged."
_APPEALED_ALREADY = "You have already used your appeal for this build."
_APPEAL_CLOSED = "The appeal window for this result has closed."
_REVIEWED_ALREADY = "A person has already reviewed this result."
_NOT_READY_TO_OVERRIDE = "This build has not reached the judge yet, so it cannot be scored by hand."
_NEEDS_REASON = "Say, in a sentence or two, what you think the judge got wrong."
_REASON_TOO_LONG = f"Keep your reason to {APPEAL_REASON_CHARS} characters or fewer."
_NEEDS_NOTE = "An override needs a note saying why."
_NOTE_TOO_LONG = f"Keep the note to {OVERRIDE_NOTE_CHARS} characters or fewer."


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _moment(now: datetime | None = None) -> datetime:
    """``now`` (or the clock) as an aware UTC instant. A naive ``now`` is a bug in the caller."""
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return moment.astimezone(UTC)


def _aware(moment: datetime) -> datetime:
    """SQLite hands timezone-aware columns back naive; every stored instant is UTC."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _iso(moment: datetime) -> str:
    return _aware(moment).isoformat()


def _matched(result: Result[Any]) -> bool:
    """Whether an UPDATE changed a row. A conditional update that matched none lost a race."""
    return cast("CursorResult[Any]", result).rowcount > 0


def _user(user_id: str) -> uuid.UUID:
    user = parse_uuid(user_id)
    if user is None:
        raise PracticeNotFound(_NO_ACCOUNT)
    return user


def budget_key(moment: datetime) -> str:
    """``tr:budget:judge:<YYYY-MM-DD>``, the UTC day of ``moment``."""
    return f"{_BUDGET_KEY_PREFIX}{_aware(moment).date().isoformat()}"


async def _budget_spent(moment: datetime) -> bool:
    """Whether today's budget is gone. A cache that cannot answer says no (fail open)."""
    try:
        used = int(await CacheBackend.get(budget_key(moment)) or 0)
    except Exception as exc:
        logger.debug("practice: could not read the judge budget (%s)", type(exc).__name__)
        return False
    return used >= DAILY_JUDGE_BUDGET


async def _consume_budget(moment: datetime) -> bool:
    """Claim one unit of today's budget, only when the model is about to be called.

    Increments first and compares after, so two concurrent runs cannot both see the last unit
    as free. A cache failure returns True: the budget is a cost guard, and losing it should not
    stop judging outright.
    """
    try:
        used = await CacheBackend.incr(budget_key(moment), _BUDGET_TTL_SECONDS)
    except Exception as exc:
        logger.debug("practice: could not count the judge budget (%s)", type(exc).__name__)
        return True
    if used > DAILY_JUDGE_BUDGET:
        logger.info("practice: the daily judge budget (%d) is spent", DAILY_JUDGE_BUDGET)
        return False
    return True


# ---------------------------------------------------------------------------
# What one judging pass works from
# ---------------------------------------------------------------------------


class _Reject(Exception):  # noqa: N818 - names the outcome, as RepoGone does
    """The attempt cannot be judged and will not be able to: carries the ``reject_reason`` code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _Work:
    """The plain values of one attempt and its edition, read once so no session stays open
    across a GitHub request or a model call."""

    id: uuid.UUID
    status: str
    started_at: datetime
    deadline_at: datetime
    proof_code: str
    repo_owner: str | None
    repo_name: str | None
    fields: dict[str, str]
    video_url: str | None
    snapshot_sha: str | None
    signals: dict[str, Any] | None
    flags: list[str]
    held: bool
    runs: list[dict[str, Any]]
    appealed: bool
    override_note: str | None
    brief: dict[str, Any]


@dataclass(frozen=True)
class _Material:
    """What is read at the snapshot: the commits, the files and the text the judge is shown."""

    commits: list[CommitLine]
    tree: list[TreeEntry]
    readme: str
    files: list[tuple[str, str]]


@dataclass(frozen=True)
class _FirstRead:
    material: _Material
    created_at: datetime
    pushed: Push
    pushes: list[Push]
    late: int


@dataclass(frozen=True)
class _Outcome:
    """What a pass did. ``status`` is the attempt's status afterwards; ``kind`` is what the step
    counts: judged, rejected, held, retried, failed, budget or idle (nothing to do / lost a race);
    ``llm`` is whether the model was called."""

    status: str
    kind: str
    llm: bool = False


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _brief_for_judge(challenge: PracticeChallenge) -> dict[str, Any]:
    """The brief as stored, with the columns that are authoritative laid over it."""
    brief: dict[str, Any] = dict(challenge.brief) if isinstance(challenge.brief, dict) else {}
    brief["hours"] = challenge.hours
    brief.setdefault("title", challenge.title)
    brief.setdefault("track", challenge.track)
    return brief


def _work_of(attempt: PracticeAttempt, challenge: PracticeChallenge) -> _Work:
    stored = attempt.writeup if isinstance(attempt.writeup, dict) else {}
    return _Work(
        id=attempt.id,
        status=attempt.status,
        started_at=_aware(attempt.started_at),
        deadline_at=_aware(attempt.deadline_at),
        proof_code=str(attempt.proof_code or "").strip(),
        repo_owner=attempt.repo_owner,
        repo_name=attempt.repo_name,
        fields={name: str(stored.get(name) or "") for name in WRITEUP_TEXT_FIELDS},
        video_url=attempt.video_url,
        snapshot_sha=attempt.snapshot_sha,
        signals=dict(attempt.signals) if isinstance(attempt.signals, dict) else None,
        flags=[str(flag) for flag in (attempt.flags or [])],
        runs=_list_of_dicts(attempt.judge_runs),
        held=bool(attempt.held),
        appealed=attempt.appealed_at is not None,
        override_note=attempt.override_note or None,
        brief=_brief_for_judge(challenge),
    )


async def _load(attempt_id: uuid.UUID) -> _Work:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(PracticeAttempt, PracticeChallenge)
                .join(PracticeChallenge, PracticeChallenge.id == PracticeAttempt.challenge_id)
                .where(PracticeAttempt.id == attempt_id)
            )
        ).one_or_none()
    if row is None:
        raise PracticeNotFound(_NO_SUCH_ATTEMPT)
    return _work_of(row[0], row[1])


async def _status_of(attempt_id: uuid.UUID) -> str:
    async with AsyncSessionLocal() as session:
        status = (
            await session.execute(
                select(PracticeAttempt.status).where(PracticeAttempt.id == attempt_id)
            )
        ).scalar_one_or_none()
    return status or "missing"


async def _apply(
    attempt_id: uuid.UUID,
    expected: Mapping[str, Any],
    values: Mapping[str, Any],
    done: _Outcome,
) -> _Outcome:
    """One conditional UPDATE: ``values`` are written only while the row still matches ``expected``
    (column -> value). If someone else moved the row first, nothing is written and the pass is
    ``idle`` with whatever status the row has now."""
    conditions = [getattr(PracticeAttempt, column) == value for column, value in expected.items()]
    async with AsyncSessionLocal() as session:
        changed = await session.execute(
            update(PracticeAttempt)
            .where(PracticeAttempt.id == attempt_id, *conditions)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    if _matched(changed):
        return done
    return _Outcome(await _status_of(attempt_id), "idle")


# ---------------------------------------------------------------------------
# Reading the repo
# ---------------------------------------------------------------------------


async def _read_tree(client: httpx.AsyncClient, ref: RepoRef, sha: str) -> list[TreeEntry]:
    try:
        return await fetch_tree(client, ref, sha)
    except ReplyTooLarge as exc:
        raise _Reject("repo_too_large") from exc
    except RepoGone as exc:
        raise _Reject("snapshot_gone") from exc


async def _read_material(
    client: httpx.AsyncClient, ref: RepoRef, sha: str, tree: list[TreeEntry]
) -> _Material:
    """The commits, README and key files at ``sha``. A file GitHub does not have is skipped."""
    try:
        commits = await fetch_commits(client, ref, sha)
        readme = await fetch_text(client, ref, None, sha, limit=_README_BYTES) or ""
        files: list[tuple[str, str]] = []
        for path in pick_key_files(tree):
            text = await fetch_text(client, ref, path, sha, limit=_FILE_BYTES)
            if text:
                files.append((path, text))
    except ReplyTooLarge as exc:
        raise _Reject("repo_too_large") from exc
    except RepoGone as exc:
        raise _Reject("snapshot_gone") from exc
    return _Material(commits=commits, tree=tree, readme=readme, files=files)


async def _read_first(client: httpx.AsyncClient, ref: RepoRef, work: _Work) -> _FirstRead:
    """Everything a first judgement needs, rejecting as soon as the answer is known."""
    try:
        repo = await fetch_repo(client, ref)
    except RepoGone as exc:
        raise _Reject("repo_gone") from exc
    if repo.get("private") is True:
        raise _Reject("repo_private")
    if repo.get("fork") is True:
        raise _Reject("repo_fork")
    created_at = parse_timestamp(repo.get("created_at"))
    if created_at is None:
        raise GitHubUnavailable("GitHub's repo reply had no creation time")
    # GitHub's own time, every time: the save-time check may have been skipped.
    if created_at < work.started_at - REPO_CREATION_GRACE:
        raise _Reject("created_before_start")
    branch = repo.get("default_branch")
    if not isinstance(branch, str) or not branch:
        raise GitHubUnavailable("GitHub's repo reply had no default branch")

    try:
        pushes = await fetch_pushes(client, ref, branch)
    except ReplyTooLarge as exc:  # before GitHubUnavailable, which it subclasses: not a retry
        raise _Reject("repo_too_large") from exc
    except RepoGone as exc:
        raise _Reject("repo_gone") from exc
    pushed, late = snapshot(pushes, work.deadline_at)
    if pushed is None:
        raise _Reject("no_push_in_window")

    # The tree first: a 404 here is a commit GitHub no longer has, where a 404 on the proof
    # file below only says the file is not there.
    tree = await _read_tree(client, ref, pushed.sha)
    try:
        proof = await fetch_text(client, ref, PROOF_FILE, pushed.sha, limit=_PROOF_BYTES)
    except RepoGone as exc:
        raise _Reject("snapshot_gone") from exc
    if not proof_matches(proof, work.proof_code):
        raise _Reject("proof_missing")

    material = await _read_material(client, ref, pushed.sha, tree)
    return _FirstRead(
        material=material, created_at=created_at, pushed=pushed, pushes=pushes, late=late
    )


def _judge_input(work: _Work, material: _Material, signals: Mapping[str, Any]) -> JudgeInput:
    """What the model may read, and everything that is scanned for text aimed at it, in full:
    every tree path is here, however many the prompt keeps (see :func:`_prompt_of`). The
    recording's link is part of the write-up mapping, so it is in the fence code and the input
    hash, but it is not one of the four fields the prompt shows; there is no field for an
    appeal reason."""
    return JudgeInput(
        brief=work.brief,
        writeup={**work.fields, "video_url": work.video_url or ""},
        readme=material.readme,
        tree=[entry.path for entry in material.tree],
        commits=material.commits,
        files=material.files,
        signals=signals,
    )


def _prompt_of(inp: JudgeInput) -> tuple[str, str, str]:
    """``(system, user, input hash)`` for ``inp``. The prompt builder is handed the first
    ``_INPUT_TREE_PATHS`` tree paths (it shows 250); the caller scans ``inp`` itself, all of it."""
    system, user = build_judge_prompt(replace(inp, tree=list(inp.tree)[:_INPUT_TREE_PATHS]))
    return system, user, input_sha256(user)


# ---------------------------------------------------------------------------
# One reading
# ---------------------------------------------------------------------------


def _run_record(verdict: Verdict, model: str, input_hash: str, moment: datetime) -> dict[str, Any]:
    """One reading as stored in ``judge_runs``."""
    return {
        "scores": dict(verdict.scores),
        "notes": dict(verdict.notes),
        "strengths": list(verdict.strengths),
        "improvements": list(verdict.improvements),
        "summary": verdict.summary,
        "model": model,
        "prompt_version": JUDGE_PROMPT_VERSION,
        "input_sha256": input_hash,
        "at": moment.isoformat(),
    }


def _readable(run: Mapping[str, Any]) -> bool:
    scores = run.get("scores")
    return isinstance(scores, dict) and all(
        isinstance(scores.get(name), int | float) and not isinstance(scores.get(name), bool)
        for name in CRITERIA
    )


def _final_scores(runs: list[dict[str, Any]]) -> dict[str, float]:
    """The first reading's scores, or the mean of the two readings after an appeal."""
    first = runs[0]["scores"]
    if len(runs) == 1:
        return dict(first)
    return average_runs(first, runs[1]["scores"])


def _strings(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _flat_notes(stored: Mapping[str, Any]) -> dict[str, Any]:
    """``notes`` as stored on the attempt and shown on the page: a note per criterion, then
    strengths, improvements and a summary. Anything missing is empty."""
    return {
        **{name: str(stored.get(name) or "") for name in CRITERIA},
        "strengths": _strings(stored.get("strengths")),
        "improvements": _strings(stored.get("improvements")),
        "summary": str(stored.get("summary") or ""),
    }


def _notes_of(run: Mapping[str, Any]) -> dict[str, Any]:
    """The attempt's ``notes`` from one stored reading."""
    notes = run.get("notes")
    return _flat_notes(
        {
            **(notes if isinstance(notes, dict) else {}),
            "strengths": run.get("strengths"),
            "improvements": run.get("improvements"),
            "summary": run.get("summary"),
        }
    )


def _result_values(runs: list[dict[str, Any]]) -> dict[str, Any]:
    scores = _final_scores(runs)
    return {
        "scores": scores,
        "total": Decimal(str(total(scores))),
        "notes": _notes_of(runs[0]),
        "judge_runs": runs,
    }


def _reusable(runs: list[dict[str, Any]], input_hash: str, model: str) -> bool:
    """A stored first reading of this very input, by this model and prompt version, is the same
    judgement and is never bought again."""
    return bool(
        runs
        and _readable(runs[0])
        and runs[0].get("input_sha256") == input_hash
        and runs[0].get("model") == model
        and runs[0].get("prompt_version") == JUDGE_PROMPT_VERSION
    )


async def _ask(system: str, user: str, model: str) -> Verdict | None:
    """One model call, read strictly. ``None`` for any error or unreadable reply: never a score."""
    try:
        raw = await _chat(
            system,
            user,
            model=model,
            temperature=0,
            max_tokens=JUDGE_MAX_TOKENS,
            json_mode=True,
            reasoning_effort=JUDGE_REASONING_EFFORT,
        )
        # A length and nothing of the reply: enough to tell a verdict cut off at max_tokens.
        logger.info("practice: the judge's reply was %d characters", len(raw))
        return parse_verdict(raw)
    except VerdictError:
        logger.warning("practice: the judge's reply could not be read")
    except Exception as exc:
        logger.warning("practice: the judge's model call failed (%s)", type(exc).__name__)
    return None


async def _fail(attempt_id: uuid.UUID, moment: datetime) -> _Outcome:
    """Record one failed pass: ``judge_attempts`` up, ``next_judge_at`` backed off.

    The sixth failure of a first judgement makes it ``judge_failed``; the sixth of an appeal's
    second reading gives the appeal up and leaves the first score standing. Nothing here ever
    writes a score. Reads the current counters itself, so a safety net may call it blind.
    """
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(PracticeAttempt.status, PracticeAttempt.judge_attempts).where(
                    PracticeAttempt.id == attempt_id
                )
            )
        ).one_or_none()
    if row is None:
        return _Outcome("missing", "idle")
    status, prior = row[0], int(row[1] or 0)
    if status not in (_QUEUED, _JUDGED):
        return _Outcome(status, "idle")
    failures = prior + 1
    exhausted = failures >= MAX_JUDGE_ATTEMPTS
    values: dict[str, Any] = {
        "judge_attempts": failures,
        "next_judge_at": None if exhausted else moment + timedelta(hours=BACKOFF_HOURS[prior]),
    }
    after = status
    if exhausted and status == _QUEUED:
        values["status"] = after = "judge_failed"
    return await _apply(
        attempt_id,
        {"status": status, "judge_attempts": prior},
        values,
        _Outcome(after, "failed" if exhausted else "retried"),
    )


async def _reject(attempt_id: uuid.UUID, code: str) -> _Outcome:
    return await _apply(
        attempt_id,
        {"status": _QUEUED},
        {"status": "rejected", "reject_reason": code, "next_judge_at": None},
        _Outcome("rejected", "rejected"),
    )


def _snapshot_values(read: _FirstRead, signals: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_sha": read.pushed.sha,
        "snapshot_pushed_at": read.pushed.at,
        "signals": dict(signals),
        "repo_created_at": read.created_at,
    }


def _cleared_flag(input_hash: str) -> str:
    return f"{HOLD_CLEARED_FLAG}:{input_hash}"


def _must_hold(work: _Work, markers: list[str], input_hash: str) -> bool:
    """Text aimed at the grader holds the attempt, unless an admin has cleared a hold for exactly
    this input (its hash); an input that has changed since is checked again."""
    return bool(markers) and _cleared_flag(input_hash) not in work.flags


async def _hold(
    work: _Work,
    markers: list[str],
    input_hash: str,
    values: Mapping[str, Any] | None = None,
) -> _Outcome:
    """Keep the attempt off the leaderboard and away from the model until an admin clears it.

    The hash of the held input is stored (``judge_input_sha256``), which is what ``admin_clear``
    ties the clear to. ``values`` are written with it (the snapshot facts, for a first judgement).
    The attempt keeps its status: ``queued`` for a first judgement, ``judged`` for an appeal.
    """
    flags = work.flags if INJECTION_FLAG in work.flags else [*work.flags, INJECTION_FLAG]
    logger.info("practice: attempt %s held (%s)", work.id, ",".join(markers))
    return await _apply(
        work.id,
        {"status": work.status},
        {
            **(values or {}),
            "judge_input_sha256": input_hash,
            "held": True,
            "flags": flags,
            "next_judge_at": None,
        },
        _Outcome(work.status, "held"),
    )


async def _first_judgement(work: _Work, moment: datetime) -> _Outcome:
    if not work.repo_owner or not work.repo_name:
        return await _reject(work.id, "no_submission")
    ref = RepoRef(work.repo_owner, work.repo_name)
    try:
        async with github_client() as client:
            read = await _read_first(client, ref, work)
    except _Reject as exc:
        return await _reject(work.id, exc.code)
    except (GitHubUnavailable, ValueError) as exc:
        logger.info("practice: attempt %s: GitHub read failed (%s)", work.id, type(exc).__name__)
        return await _fail(work.id, moment)

    signals = compute_signals(
        repo_created_at=read.created_at,
        started_at=work.started_at,
        deadline_at=work.deadline_at,
        pushes=read.pushes,
        late_pushes=read.late,
        commits=read.material.commits,
        tree=read.material.tree,
        readme_chars=len(read.material.readme),
        writeup=work.fields,
        proof_found=True,
    )
    inp = _judge_input(work, read.material, signals)
    system, user, input_hash = _prompt_of(inp)
    model = get_settings().groq_model

    if _reusable(work.runs, input_hash, model):
        values = {**_snapshot_values(read, signals), **_result_values(work.runs)}
        return await _apply(
            work.id,
            {"status": _QUEUED},
            {
                **values,
                "status": _JUDGED,
                "judge_input_sha256": input_hash,
                "judged_at": moment,
                "next_judge_at": None,
            },
            _Outcome(_JUDGED, "judged"),
        )

    markers = input_markers(inp)  # over the original text, not the cleaned prompt copies
    if _must_hold(work, markers, input_hash):
        return await _hold(work, markers, input_hash, _snapshot_values(read, signals))

    if not await _consume_budget(moment):
        return _Outcome(_QUEUED, "budget")
    verdict = await _ask(system, user, model)
    if verdict is None:
        return replace(await _fail(work.id, moment), llm=True)

    run = _run_record(verdict, model, input_hash, moment)
    return await _apply(
        work.id,
        {"status": _QUEUED},
        {
            **_snapshot_values(read, signals),
            **_result_values([run]),
            "status": _JUDGED,
            "judge_input_sha256": input_hash,
            "judged_at": moment,
            "next_judge_at": None,
        },
        _Outcome(_JUDGED, "judged", llm=True),
    )


async def _appeal_rerun(work: _Work, moment: datetime) -> _Outcome:
    """The second, blind reading after an appeal: the same snapshot, the same input, no reason.

    Repo content is never stored, so the files are read again at the stored snapshot SHA and the
    stored signals are reused as they were. The two readings are averaged, and only ever when
    they are readings of the same input; before the model is called the rebuilt input is held
    to the same checks as a first judgement:

    * a person has already reviewed the result (an override): nothing is read, the row is left
      as it is;
    * the original text reads as aimed at the grader and no hold was cleared for exactly this
      input: the attempt is held and the model is not called;
    * the input's hash is not the first reading's: the readings would not be of the same thing,
      so the appeal is refused (``appeal_input_changed``), the first score stands, and an admin
      decides (``admin_requeue`` judges the current input afresh).
    """
    if work.override_note:  # a person has decided: a model must not overwrite that
        return _Outcome(work.status, "idle")
    if not (work.repo_owner and work.repo_name and work.snapshot_sha and work.signals is not None):
        return await _fail(work.id, moment)
    ref = RepoRef(work.repo_owner, work.repo_name)
    try:
        async with github_client() as client:
            tree = await _read_tree(client, ref, work.snapshot_sha)
            material = await _read_material(client, ref, work.snapshot_sha, tree)
    except (_Reject, GitHubUnavailable, ValueError) as exc:
        logger.info("practice: attempt %s: appeal read failed (%s)", work.id, type(exc).__name__)
        return await _fail(work.id, moment)

    inp = _judge_input(work, material, work.signals)
    system, user, input_hash = _prompt_of(inp)
    markers = input_markers(inp)  # over the original text, as for a first judgement
    if _must_hold(work, markers, input_hash):
        return await _hold(work, markers, input_hash)
    if work.runs[0].get("input_sha256") != input_hash:
        logger.info("practice: attempt %s: the appeal's input is not the first reading's", work.id)
        return await _apply(
            work.id,
            {"status": _JUDGED},
            {"flags": [*work.flags, APPEAL_INPUT_CHANGED_FLAG], "next_judge_at": None},
            _Outcome(_JUDGED, "idle"),
        )

    if not await _consume_budget(moment):
        return _Outcome(_JUDGED, "budget")
    model = get_settings().groq_model
    verdict = await _ask(system, user, model)
    if verdict is None:
        return replace(await _fail(work.id, moment), llm=True)

    runs = [work.runs[0], _run_record(verdict, model, input_hash, moment)]
    values = _result_values(runs)
    return await _apply(
        work.id,
        {"status": _JUDGED},
        {**values, "next_judge_at": None},
        _Outcome(_JUDGED, "judged", llm=True),
    )


async def _judge(attempt_id: uuid.UUID, moment: datetime) -> _Outcome:
    work = await _load(attempt_id)
    if work.held:  # waiting for an admin: nothing is read and nothing is spent
        return _Outcome(work.status, "idle")
    if work.status == _QUEUED:
        return await _first_judgement(work, moment)
    if (
        work.status == _JUDGED
        and work.appealed
        and len(work.runs) == 1
        and APPEAL_INPUT_CHANGED_FLAG not in work.flags  # refused once, left for an admin
    ):
        return await _appeal_rerun(work, moment)
    return _Outcome(work.status, "idle")


async def judge_attempt(attempt_id: uuid.UUID, *, now: datetime | None = None) -> str:
    """Judge one attempt and return its status afterwards.

    A ``queued`` attempt gets its first judgement (or is rejected, held, backed off or left
    queued because the day's budget is spent). A ``judged`` attempt with an appeal waiting gets
    its second reading. Any other attempt, and any held one, is left alone. Raises
    :class:`PracticeNotFound` for an id that does not exist.
    """
    return (await _judge(attempt_id, _moment(now))).status


# ---------------------------------------------------------------------------
# The scheduled step
# ---------------------------------------------------------------------------


def _counts(tally: Mapping[str, int]) -> dict[str, int | str]:
    return {**tally}


def _per_run_cap() -> int:
    return JUDGE_PER_RUN_MAX if get_settings().github_token else JUDGE_PER_RUN_MAX_NO_TOKEN


async def _due_attempts(moment: datetime, cap: int) -> list[uuid.UUID]:
    """Attempts the judge may take now, longest-waiting first: queued attempts that are not held
    and whose backoff is over, and judged attempts with an appeal's second reading waiting."""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(PracticeAttempt.id)
            .where(
                PracticeAttempt.held.is_(False),
                or_(
                    and_(
                        PracticeAttempt.status == _QUEUED,
                        or_(
                            PracticeAttempt.next_judge_at.is_(None),
                            PracticeAttempt.next_judge_at <= moment,
                        ),
                    ),
                    and_(
                        PracticeAttempt.status == _JUDGED,
                        PracticeAttempt.appealed_at.is_not(None),
                        PracticeAttempt.next_judge_at.is_not(None),
                        PracticeAttempt.next_judge_at <= moment,
                    ),
                ),
            )
            .order_by(PracticeAttempt.next_judge_at, PracticeAttempt.id)
            .limit(cap)
        )
        return list(rows.scalars().all())


async def run_judge_step(
    limit: int,
    *,
    now: datetime | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, int | str]:
    """Judge up to ``min(limit, 4)`` due attempts (2 without a GitHub token), 65 s apart.

    The wait is only between model calls: attempts that are rejected or held cost no tokens and
    are not waited for. When the day's budget is spent nothing is read and nothing changes.
    Never raises: one attempt that blows up is counted in ``errors`` and backed off like a failure
    so it cannot hold a slot for ever, and a failure of the step itself is ``{"error": <class>}``.
    """
    tally = {
        "processed": 0,
        "judged": 0,
        "rejected": 0,
        "held": 0,
        "retried": 0,
        "failed": 0,
        "errors": 0,
        "budget_exhausted": 0,
    }
    try:
        moment = _moment(now)
        cap = min(max(limit, 0), _per_run_cap())
        if cap == 0:
            return _counts(tally)
        if await _budget_spent(moment):
            tally["budget_exhausted"] = 1
            return _counts(tally)
        owes_a_wait = False
        for position, attempt_id in enumerate(await _due_attempts(moment, cap)):
            if position and await _budget_spent(moment):  # the last call took the last unit
                tally["budget_exhausted"] = 1
                break
            if owes_a_wait:
                await sleep(JUDGE_SPACING_SECONDS)
                owes_a_wait = False
            try:
                outcome = await _judge(attempt_id, moment)
            except Exception as exc:
                logger.warning(
                    "practice: judging attempt %s raised %s", attempt_id, type(exc).__name__
                )
                tally["processed"] += 1
                tally["errors"] += 1
                await _fail_quietly(attempt_id, moment)
                continue
            if outcome.kind == "budget":
                tally["budget_exhausted"] = 1
                break
            tally["processed"] += 1
            if outcome.kind in tally:
                tally[outcome.kind] += 1
            owes_a_wait = owes_a_wait or outcome.llm
        return _counts(tally)
    except Exception as exc:
        logger.warning("practice: the judge step failed (%s)", type(exc).__name__)
        return {"error": type(exc).__name__}


async def _fail_quietly(attempt_id: uuid.UUID, moment: datetime) -> None:
    try:
        await _fail(attempt_id, moment)
    except Exception as exc:
        logger.warning(
            "practice: could not back off attempt %s (%s)", attempt_id, type(exc).__name__
        )


# ---------------------------------------------------------------------------
# The owner's view and the appeal
# ---------------------------------------------------------------------------


async def _own_attempt(session: AsyncSession, user: uuid.UUID, slug: str) -> PracticeAttempt:
    attempt = (
        await session.execute(
            select(PracticeAttempt)
            .join(PracticeChallenge, PracticeChallenge.id == PracticeAttempt.challenge_id)
            .where(PracticeChallenge.slug == slug, PracticeAttempt.user_id == user)
        )
    ).scalar_one_or_none()
    if attempt is None:
        raise PracticeNotFound(_NO_ATTEMPT)
    return attempt


def _scores_payload(stored: object) -> dict[str, float] | None:
    if not isinstance(stored, dict):
        return None
    scores: dict[str, float] = {}
    for name in CRITERIA:
        value = stored.get(name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        scores[name] = value
    return scores


def _notes_payload(stored: object) -> dict[str, Any] | None:
    return _flat_notes(stored) if isinstance(stored, dict) else None


def _signals_payload(stored: object) -> dict[str, int | float | bool] | None:
    if not isinstance(stored, dict):
        return None
    return {
        str(key): value for key, value in stored.items() if isinstance(value, bool | int | float)
    }


def _result_payload(attempt: PracticeAttempt, moment: datetime) -> dict[str, Any]:
    judged = attempt.status == _JUDGED
    judged_at = _aware(attempt.judged_at) if judged and attempt.judged_at else None
    until = judged_at + APPEAL_WINDOW if judged_at is not None else None
    used = attempt.appealed_at is not None
    owner, name, sha = attempt.repo_owner, attempt.repo_name, attempt.snapshot_sha
    return {
        "status": attempt.status,
        "held": bool(attempt.held),
        "reject_reason": (
            REJECT_MESSAGES.get(attempt.reject_reason, attempt.reject_reason)
            if attempt.reject_reason
            else None
        ),
        "total": float(attempt.total) if attempt.total is not None else None,
        "scores": _scores_payload(attempt.scores),
        "weights": dict(WEIGHTS),
        "notes": _notes_payload(attempt.notes),
        "signals": _signals_payload(attempt.signals),
        "snapshot_sha": sha,
        "snapshot_url": (
            RepoRef(owner, name).html_url + f"/commit/{sha}" if owner and name and sha else None
        ),
        "appeal": {
            "available": until is not None and not used and moment <= until,
            "used": used,
            "until": _iso(until) if until is not None else None,
        },
        "rejudged": len(_list_of_dicts(attempt.judge_runs)) >= 2,
    }


async def attempt_result(user_id: str, slug: str, *, now: datetime | None = None) -> dict[str, Any]:
    """The signed-in user's own result for the build ``slug``. Reads the row; no model, no GitHub.

    The shape is ``AttemptResult`` in ``frontend/lib/api.ts``. A ``reject_reason`` is the sentence
    the owner reads, not the stored code. Raises :class:`PracticeNotFound` when the user has no
    attempt at that build, which is also what someone else's attempt looks like.
    """
    moment = _moment(now)
    user = _user(user_id)
    async with AsyncSessionLocal() as session:
        attempt = await _own_attempt(session, user, slug)
    return _result_payload(attempt, moment)


def _printable(text: str) -> str:
    """``text`` without control or invisible characters; newlines and tabs stay."""
    return "".join(ch for ch in str(text) if ch in "\n\t" or ch.isprintable()).strip()


def _clean_reason(reason: str) -> str:
    text = _printable(reason)
    if not text:
        raise PracticeInvalid(_NEEDS_REASON)
    if len(text) > APPEAL_REASON_CHARS:
        raise PracticeInvalid(_REASON_TOO_LONG)
    return text


async def request_appeal(
    user_id: str, slug: str, reason: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Appeal a result: once, within seven days of it, with a reason of at most 500 characters.

    The reason is stored for an admin and never reaches the model. The appeal marks the attempt
    for a second blind reading of the same input, made by the scheduled judge step; the owner
    keeps the first score until it arrives. Raises :class:`PracticeConflict` when the attempt is
    not judged, a person has already reviewed it (an admin's override, or a result with no
    stored reading of the model to re-read), the appeal is used or the window has closed,
    :class:`PracticeInvalid` for a reason that is empty or too long, and
    :class:`PracticeNotFound` for someone else's attempt.
    """
    moment = _moment(now)
    user = _user(user_id)
    async with AsyncSessionLocal() as session:
        attempt = await _own_attempt(session, user, slug)
        if attempt.status != _JUDGED or attempt.judged_at is None:
            raise PracticeConflict(_NOTHING_TO_APPEAL)
        if attempt.override_note or not _list_of_dicts(attempt.judge_runs):
            raise PracticeConflict(_REVIEWED_ALREADY)
        if attempt.appealed_at is not None:
            raise PracticeConflict(_APPEALED_ALREADY)
        if moment > _aware(attempt.judged_at) + APPEAL_WINDOW:
            raise PracticeConflict(_APPEAL_CLOSED)
        attempt_id = attempt.id
    text = _clean_reason(reason)
    async with AsyncSessionLocal() as session:
        changed = await session.execute(
            update(PracticeAttempt)
            .where(
                PracticeAttempt.id == attempt_id,
                PracticeAttempt.status == _JUDGED,
                PracticeAttempt.appealed_at.is_(None),
                PracticeAttempt.override_note.is_(None),  # an override may land since the read
            )
            .values(appeal_reason=text, appealed_at=moment, next_judge_at=moment, judge_attempts=0)
            .execution_options(synchronize_session=False)
        )
        if not _matched(changed):
            await session.rollback()
            reviewed = (
                await session.execute(
                    select(PracticeAttempt.override_note).where(PracticeAttempt.id == attempt_id)
                )
            ).scalar_one_or_none()
            raise PracticeConflict(_REVIEWED_ALREADY if reviewed else _APPEALED_ALREADY)
        await session.commit()
    return await attempt_result(user_id, slug, now=moment)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


async def _admin_attempt(session: AsyncSession, attempt_id: uuid.UUID) -> PracticeAttempt:
    attempt = await session.get(PracticeAttempt, attempt_id)
    if attempt is None:
        raise PracticeNotFound(_NO_SUCH_ATTEMPT)
    return attempt


async def admin_clear(attempt_id: uuid.UUID, *, now: datetime | None = None) -> None:
    """Release a hold: the attempt may be ranked, and a queued one, or a judged one whose
    appeal is waiting for its second reading, is due at once.

    The flag ``injection_text`` stays as history, and ``hold_cleared:<hash>`` is added, with the
    hash of the input the hold was for (stored when it was held). Only an input with exactly
    that hash goes ahead unheld; if the text has changed since, it is checked again. A hold with
    no stored hash is released all the same, but the clear covers no input and is recorded as a
    bare ``hold_cleared``. This is how a repo built for an AI brief, held falsely, is put right.
    """
    moment = _moment(now)
    async with AsyncSessionLocal() as session:
        attempt = await _admin_attempt(session, attempt_id)
        # One clear at a time: an earlier one was for an input that is no longer this one.
        flags = [flag for flag in (attempt.flags or []) if not flag.startswith(HOLD_CLEARED_FLAG)]
        flags.append(
            _cleared_flag(attempt.judge_input_sha256)
            if attempt.judge_input_sha256
            else HOLD_CLEARED_FLAG
        )
        attempt.flags = flags
        attempt.held = False
        waiting_appeal = (
            attempt.status == _JUDGED
            and attempt.appealed_at is not None
            and len(_list_of_dicts(attempt.judge_runs)) == 1
            and APPEAL_INPUT_CHANGED_FLAG not in flags
        )
        if attempt.status == _QUEUED or waiting_appeal:
            attempt.next_judge_at = moment
        await session.commit()


async def admin_requeue(attempt_id: uuid.UUID, *, now: datetime | None = None) -> None:
    """Put an attempt back in the queue with a fresh set of tries, whatever became of it.

    A stored result for the same input, model and prompt version is not bought again: the judge
    restores it. A hold is not cleared here; that is :func:`admin_clear`.
    """
    moment = _moment(now)
    async with AsyncSessionLocal() as session:
        attempt = await _admin_attempt(session, attempt_id)
        attempt.status = _QUEUED
        attempt.judge_attempts = 0
        attempt.next_judge_at = moment
        attempt.reject_reason = None
        await session.commit()


def _checked_scores(scores: Mapping[str, int]) -> dict[str, int]:
    checked: dict[str, int] = {}
    for name in CRITERIA:
        value = scores.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
            raise PracticeInvalid(f"The score for {name} must be a whole number from 0 to 10.")
        checked[name] = value
    return checked


async def admin_override(
    attempt_id: uuid.UUID,
    scores: Mapping[str, int],
    note: str,
    *,
    now: datetime | None = None,
) -> None:
    """Set the final scores by hand, with a note saying why. The attempt becomes ``judged`` and
    leaves any hold; the model's readings are kept as they are. Only an attempt that is queued,
    judged, ``judge_failed`` or rejected can be overridden. Raises :class:`PracticeInvalid` for
    a missing or out-of-range score or a missing or over-long note, and
    :class:`PracticeConflict` for an attempt in any other status (not yet submitted, or missed).
    """
    moment = _moment(now)
    checked = _checked_scores(scores)
    text = _printable(note)
    if not text:
        raise PracticeInvalid(_NEEDS_NOTE)
    if len(text) > OVERRIDE_NOTE_CHARS:
        raise PracticeInvalid(_NOTE_TOO_LONG)
    async with AsyncSessionLocal() as session:
        attempt = await _admin_attempt(session, attempt_id)
        if attempt.status not in _OVERRIDABLE:
            raise PracticeConflict(_NOT_READY_TO_OVERRIDE)
        attempt.scores = checked
        attempt.total = Decimal(str(total(checked)))
        attempt.override_note = text
        attempt.status = _JUDGED
        attempt.held = False
        attempt.reject_reason = None
        attempt.next_judge_at = None
        attempt.judged_at = attempt.judged_at or moment
        await session.commit()
