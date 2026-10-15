"""
domain/practice_signals.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
The deterministic facts about a take-home that are shown to the judge and to its owner.

They are evidence, not points: nothing here is a score, and no formula over them feeds
one (fifty one-line commits would game it). Every input is a value the caller already
holds, so the same inputs always give the same signals.

Only GitHub's own times decide anything: the repo's ``created_at`` and the push
timestamps. A commit's claimed date is never read here, so a commit backdated by the
author, or fifty of them in one push, cannot move any signal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from domain.practice_repo import (
    REPO_CREATION_GRACE,
    CommitLine,
    Push,
    TreeEntry,
    is_test_path,
)

#: The write-up fields that are prose. ``video_url`` is a link, never counted.
WRITEUP_TEXT_FIELDS = ("decisions", "tradeoffs", "ai_usage", "next_steps")

Signals = dict[str, int | float | bool]


def _commits_carried(push: Push, positions: Mapping[str, int], commit_count: int) -> int:
    """The commits one push brought: those from its ``sha`` back to, not including, its ``before``.

    ``positions`` maps a commit id to its place in the newest-first commit list. A
    ``before`` that is not in the list (a new branch, or history that was rewritten)
    counts every commit up to the end of the list. A ``sha`` that is not in the list, or a
    ``before`` that sits at or ahead of it, carries nothing.
    """
    start = positions.get(push.sha)
    if start is None:
        return 0
    stop = positions.get(push.before, commit_count)
    return max(stop - start, 0)


def compute_signals(
    *,
    repo_created_at: datetime | None,
    started_at: datetime,
    deadline_at: datetime,
    pushes: Sequence[Push],
    late_pushes: int,
    commits: Sequence[CommitLine],
    tree: Sequence[TreeEntry],
    readme_chars: int,
    writeup: Mapping[str, str],
    proof_found: bool,
) -> Signals:
    """The signals for one submission.

    ``pushes`` are the pushes to the judged branch; those outside ``[started_at,
    deadline_at]`` are not counted as in the window, so passing all of them is safe.
    ``late_pushes`` is how many came after the deadline (the second value of
    ``practice_repo.snapshot``) and is reported as it is. ``commits`` is the newest-first
    commit list at the snapshot; ``tree`` its files.
    """
    in_window = [push for push in pushes if started_at <= push.at <= deadline_at]
    span = (
        (max(push.at for push in in_window) - min(push.at for push in in_window)).total_seconds()
        if in_window
        else 0.0
    )
    positions: dict[str, int] = {}
    for position, commit in enumerate(commits):
        positions.setdefault(commit.sha, position)

    return {
        "repo_created_in_window": repo_created_at is not None
        and started_at - REPO_CREATION_GRACE <= repo_created_at <= deadline_at,
        "pushes_in_window": len(in_window),
        "push_span_hours": round(span / 3600, 1),
        "commits_at_snapshot": len(commits),
        "largest_push_commits": max(
            (_commits_carried(push, positions, len(commits)) for push in in_window), default=0
        ),
        "tests_present": any(is_test_path(entry.path) for entry in tree),
        "readme_chars": readme_chars,
        "writeup_words": sum(len(writeup.get(field, "").split()) for field in WRITEUP_TEXT_FIELDS),
        "proof_found": proof_found,
        "late_pushes_ignored": max(late_pushes, 0),
    }
