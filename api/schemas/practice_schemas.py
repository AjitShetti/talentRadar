"""
api/schemas/practice_schemas.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Request and response models for Practice (``/api/v1/practice``).

The response models mirror what ``services/practice`` returns and what the Practice page
types in ``frontend/lib/api.ts``. They list every field a route may send, so a field that
is not named here (a streak, say) cannot reach a client by accident.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt

from domain.practice_pick import Difficulty, Feel

# --------------------------------------------------------------------------- requests


class DoneRequestSchema(BaseModel):
    """Mark today's question done, optionally with the one-tap rating."""

    feel: Feel | None = None


class FeelRequestSchema(BaseModel):
    """Rate the question that was marked done."""

    feel: Feel


# --------------------------------------------------------------------------- today


class PracticeQuestionSchema(BaseModel):
    """A pointer to a LeetCode problem. Never its statement."""

    id: int
    slug: str
    title: str
    difficulty: Difficulty
    topics: list[str]
    url: str


class ContestSchema(BaseModel):
    """The weekly LeetCode contest. ``show`` is true from Saturday 20:00 IST until it ends."""

    starts_at: str
    ends_at: str
    show: bool
    live: bool
    url: str


class PracticeTodaySchema(BaseModel):
    """The day's question, whether it is done, and the links beside it."""

    day: str = Field(description="The IST calendar date, YYYY-MM-DD")
    catalogue: Literal["ok", "empty"]
    catalogue_size: int
    track: str
    track_label: str
    question: PracticeQuestionSchema | None
    reason: str
    relaxed: list[str]
    done_at: str | None
    feel: Feel | None
    swaps_left: int
    leetcode_daily_url: str
    contest: ContestSchema


# --------------------------------------------------------------------------- history


class HistoryDaySchema(BaseModel):
    """One calendar cell. ``difficulty`` and ``topic`` are set only on a practised day."""

    day: str
    done: bool
    difficulty: str | None
    topic: str | None


class HistoryCountsSchema(BaseModel):
    """Three neutral counts. There is deliberately no current streak."""

    this_month: int
    last_30: int
    longest_run: int


class TopicCountSchema(BaseModel):
    topic: str
    count: int


class PracticeHistorySchema(BaseModel):
    days: list[HistoryDaySchema]
    counts: HistoryCountsSchema
    topics: list[TopicCountSchema]


# --------------------------------------------------------------------------- challenges

#: A request field is capped well above its real bound, so the service can say which bound
#: and by how much; only an absurd body is turned away here.
_WRITEUP_FIELD_CAP = 10_000

AttemptStatus = Literal[
    "started", "submitted", "queued", "judged", "rejected", "judge_failed", "missed"
]


class WriteupSchema(BaseModel):
    """The four write-up fields, as stored and as sent."""

    decisions: str = Field(max_length=_WRITEUP_FIELD_CAP)
    tradeoffs: str = Field(max_length=_WRITEUP_FIELD_CAP)
    ai_usage: str = Field(max_length=_WRITEUP_FIELD_CAP)
    next_steps: str = Field(max_length=_WRITEUP_FIELD_CAP)


class SubmissionRequestSchema(BaseModel):
    """Save, or replace, the submission. The service holds the real bounds."""

    repo_url: str = Field(max_length=300)
    writeup: WriteupSchema
    video_url: str | None = Field(default=None, max_length=1000)


class ChallengeBriefSchema(BaseModel):
    """A brief as the challenge page shows it: plain fields, no Markdown."""

    slug: str
    title: str
    track: str
    hours: Literal[24, 48]
    summary: str
    context: list[str]
    must_have: list[str]
    nice_to_have: list[str]
    constraints: list[str]
    deliverables: list[str]
    judge_focus: list[str]
    bengaluru_context: str


class AttemptSchema(BaseModel):
    """The signed-in user's own attempt. The proof code is shown to its owner and no one else."""

    status: AttemptStatus
    started_at: str
    deadline_at: str
    proof_code: str
    repo_url: str | None
    writeup: WriteupSchema | None
    video_url: str | None
    submitted_at: str | None
    editable_until: str
    total: float | None
    held: bool


class ChallengeSchema(BaseModel):
    """One edition, its brief and the signed-in user's attempt at it."""

    slug: str
    opens_at: str
    closes_at: str
    last_start_at: str
    brief: ChallengeBriefSchema
    attempt: AttemptSchema | None


class PastChallengeSchema(BaseModel):
    """A closed edition with the signed-in user's own status and total."""

    slug: str
    title: str
    track: str
    opens_at: str
    status: AttemptStatus | None
    total: float | None
    held: bool


class ChallengesOverviewSchema(BaseModel):
    current: ChallengeSchema | None
    next_opens_at: str | None
    past: list[PastChallengeSchema]


# --------------------------------------------------------------------------- the result

#: Capped well above the real bound (500), as the write-up fields are: the service says by how much.
_REASON_CAP = 5_000


class AppealRequestSchema(BaseModel):
    """The owner's reason for an appeal. An admin reads it; the model never does."""

    reason: str = Field(max_length=_REASON_CAP)


class OverrideRequestSchema(BaseModel):
    """An admin's scores, whole numbers 0 to 10 for each criterion, and the note saying why."""

    scores: dict[str, StrictInt]
    note: str = Field(max_length=_REASON_CAP)


class ResultNotesSchema(BaseModel):
    """The judge's words: a note per criterion, then strengths, improvements and a summary."""

    thinking: str
    tradeoffs: str
    ai_usage: str
    build_process: str
    meets_brief: str
    code_quality: str
    strengths: list[str]
    improvements: list[str]
    summary: str


AppealState = Literal["none", "open", "closed", "reviewed", "pending", "done", "gave_up", "refused"]


class AppealStateSchema(BaseModel):
    """Whether the owner may appeal now, whether they have, until when, and how it stands.

    ``state``: ``none`` (nothing judged yet), ``open`` (the form is offered), ``closed`` (the
    seven days are over), ``reviewed`` (a person has decided this result, so there is nothing
    for the judge to re-read), then for an appeal made: ``pending`` (waiting), ``done`` (two
    readings averaged), ``gave_up`` (the second reading could not be made) or ``refused`` (it
    would not have been of the same material). The last two leave the first score standing.
    """

    available: bool
    used: bool
    until: str | None
    state: AppealState


class AttemptResultSchema(BaseModel):
    """The owner's result: scores and notes once judged, the reason if rejected, and whether it is
    held for review. ``reject_reason`` is a sentence for the owner, not a code."""

    status: AttemptStatus
    held: bool
    reject_reason: str | None
    total: float | None
    scores: dict[str, float] | None
    weights: dict[str, int]
    notes: ResultNotesSchema | None
    signals: dict[str, int | float | bool] | None
    snapshot_sha: str | None
    snapshot_url: str | None
    appeal: AppealStateSchema
    rejudged: bool


# --------------------------------------------------------------------------- the leaderboards

#: Capped well above the real bound (32), as the write-up fields are: the service says which rule.
_NAME_CAP = 200


class BoardRowSchema(BaseModel):
    """One ranked attempt on a challenge board. ``repo_url`` is set only for someone who chose
    to show their repo; ``you`` marks the signed-in person's own row."""

    rank: int
    name: str
    total: float
    hours_used: float | None
    repo_url: str | None
    you: bool


class ChallengeBoardSchema(BaseModel):
    """One edition's board: the top rows, how many are ranked, how many wait to be judged
    (``queued``) or for a person to check them (``held``), and the signed-in person's own row
    wherever it falls."""

    provisional: bool
    total_entries: int
    queued: int
    held: int
    rows: list[BoardRowSchema]
    me: BoardRowSchema | None


class OverallRowSchema(BaseModel):
    rank: int
    name: str
    score: float
    entries: int
    you: bool


class OverallMeSchema(BaseModel):
    rank: int
    name: str
    score: float
    entries: int


class OverallBoardSchema(BaseModel):
    """Each person's best four totals over the last twelve editions."""

    window_challenges: int
    rows: list[OverallRowSchema]
    me: OverallMeSchema | None


class SettingsRequestSchema(BaseModel):
    """All three switches at once. ``display_name`` may be null (or blank) to go by the handle."""

    display_name: str | None = Field(max_length=_NAME_CAP)
    show_repo: StrictBool
    contest_reminder: StrictBool


class PracticeSettingsSchema(BaseModel):
    """The person's Practice settings, and the handle they appear under without a name."""

    display_name: str | None
    show_repo: bool
    contest_reminder: bool
    handle: str


class HeldAttemptSchema(BaseModel):
    """An attempt the judge held, for an admin: enough to find it and clear it, nothing the
    candidate wrote."""

    id: str
    challenge: str
    title: str
    status: AttemptStatus
    flags: list[str]
    held: bool
    cleared: bool
    submitted_at: str | None


class HeldAttemptsSchema(BaseModel):
    attempts: list[HeldAttemptSchema]
