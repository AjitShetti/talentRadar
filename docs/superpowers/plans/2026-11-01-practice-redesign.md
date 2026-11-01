# Practice Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Practice opens on the person's progress, gives one problem a day (three system design days drawn in Excalidraw with immediate feedback, four DSA days on LeetCode), and shows the contest ranking under the contest instead of on its own page.

**Architecture:** Pure rules in `domain/` (`practice_schedule`, new `practice_design`, new `practice_design_feedback`), thin I/O in `services/practice/` (`daily` grows a `kind`; new `design_catalogue`, `design`, `design_feedback`), one Alembic migration, and a reordered `/practice` page plus a new `/practice/design/[slug]` that lazy-loads Excalidraw.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, pytest on SQLite, Groq via `services.llm._chat`, Next.js 14 App Router, React 18, `@excalidraw/excalidraw` 0.18.1, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-01-practice-redesign-design.md`. Approved mockup: https://claude.ai/artifact/XVPeospJQAb2q6kBDBooFu (local copy: `C:\Users\ajitp\AppData\Local\Temp\claude\D--projects-talentRadar--claude-worktrees-practice-questions-leaderboard-363f90\655171b9-0fe0-44db-8266-233c6cf9f2db\scratchpad\talentradar-practice-mockup.html`).

## Global Constraints

- IST days throughout, via `domain.practice_schedule.ist_day`; frontend times via `frontend/lib/practice-time.ts`.
- Nothing in the server, CI or a fixture requests leetcode.com. No LeetCode statement is stored.
- Never a made-up score: a failed, refused or unreadable model call stores nothing.
- No read path calls a model. No database transaction is open during a model call.
- Model id comes from `get_settings().groq_model`, never a literal.
- Text from a person reaches a prompt only flattened, bounded and fenced with a nonce.
- Another user's row, or a row that does not exist, is a 404, the same answer for both.
- Routers are validation and delegation only. mypy strict; full annotations.
- Nothing may require the cache: every `CacheBackend` failure degrades, never raises to the page.
- Frontend: both themes; never a hard-coded white or black overlay, write `rgb(var(--hi) / .08)` and `calc(<alpha> * var(--shade))`; no sideways page scroll at phone width.
- Exact values: design days Tue/Thu/Sat IST; answers open until 06:00 IST next morning; scene ≤ 200,000 bytes and ≤ 500 elements; reasoning ≤ 4,000 characters; feedback needs ≥ 3 labelled shapes and ≥ 40 characters of reasoning; ten saves a minute; 3 feedback a person per IST day; 150 a UTC day; 1 a minute by default; input ≤ 3,000 tokens; `max_tokens` 1,500; drawings cleared at 180 days; answer rows deleted at 400 days; percent and distribution hidden below 10 ranked; 20 bands of 20 across 0–400.
- Copy on Practice pages says "contest", not "build". LeetCode's own is "LeetCode's own weekly contest". The URL `/practice/challenges` does not change.

## Review Focus

1. **A malformed drawing.** `elements` that is not a list, entries that are not objects, a label 100 KB long, or text closing a fence must give a plain 422 or be read harmlessly, never a 500 and never reach the prompt unfenced. Pinned in Tasks 4, 5 and 10.
2. **Two presses or two tabs.** Two simultaneous feedback requests make one model call; the second answers 409 and the page keeps waiting. Pinned in Task 11.
3. **A page left open across the cut-off.** Saved 23:59, pressed 00:10: credited to yesterday. A save after 06:00 answers 409, the page turns read-only and leaves the unsaved work on screen. Pinned in Tasks 10 and 19.
4. **A problem removed or renamed after a row stored it.** `today` still answers, and a past answer whose problem file is gone is a 404, never a 500. Pinned in Tasks 7 and 10.
5. **The board loading empty.** Excalidraw fires `onChange` on mount: that must never overwrite a saved drawing with an empty one. If its chunk fails to load, the page shows the problem and a message, not a blank. Built in Task 18, pinned in Task 19.

---

## Task 1: Calibrate the UI check

Run before any page task. Model: sonnet for the checker; the controller plants.

- [ ] **Step 1: Plant.** Create a temporary worktree with `mktemp -d` outside the checkout. In its `frontend/app/practice/page.tsx`, change `.pr-section-head h2` usage so a section heading sets the weight to 500 inline and leaves the typeface alone.
- [ ] **Step 2: Check.** Dispatch a fresh agent with `ui-consistency:verifying` and the `/practice` checklist from Task 16, told only that the copy differs from the checklist somewhere, and not to open the original.
- [ ] **Step 3: Record.** If the plant is not reported, stop and tell the user the reviewer is blind for it, and dispatch a separate checker for page tasks. Otherwise tick this task and note what the plant tested (a heading's weight) and what it did not (sizes, scrolling, words). Remove the worktree. No commit.

## Task 2: Which kind a day is [haiku]

**Files:** Modify `domain/practice_schedule.py`. Test: `tests/test_practice_schedule.py`.

**Interfaces — Produces:**
- `DayKind = Literal["dsa", "design"]`, `DESIGN_WEEKDAYS = frozenset({1, 3, 5})`, `ANSWER_CLOSES_IST = time(6, 0)`
- `kind_for_day(day: date) -> DayKind`
- `effective_kind(scheduled: DayKind, *, has_dsa: bool, has_design: bool) -> DayKind | None`
- `answer_open(now: datetime, day: date) -> bool` (naive `now` raises `ValueError`)

- [ ] **Step 1: Write the failing tests**

```python
def test_design_days_are_tuesday_thursday_saturday() -> None:
    kinds = [kind_for_day(date(2026, 11, 2) + timedelta(days=n)) for n in range(7)]  # Mon..Sun
    assert kinds == ["dsa", "design", "dsa", "design", "dsa", "design", "dsa"]

def test_the_kind_changes_at_ist_midnight_not_utc() -> None:
    assert kind_for_day(ist_day(datetime(2026, 11, 2, 18, 29, tzinfo=UTC))) == "dsa"     # Mon 23:59 IST
    assert kind_for_day(ist_day(datetime(2026, 11, 2, 18, 30, tzinfo=UTC))) == "design"  # Tue 00:00 IST

@pytest.mark.parametrize(("scheduled", "dsa", "design", "expected"), [
    ("dsa", True, True, "dsa"), ("dsa", False, True, "design"),
    ("design", True, True, "design"), ("design", True, False, "dsa"),
    ("dsa", False, False, None), ("design", False, False, None),
])
def test_an_empty_list_falls_back_to_the_other_kind(scheduled, dsa, design, expected) -> None:
    assert effective_kind(scheduled, has_dsa=dsa, has_design=design) == expected

def test_an_answer_stays_open_until_six_the_next_morning() -> None:
    tuesday = date(2026, 11, 3)
    assert answer_open(datetime(2026, 11, 4, 0, 29, tzinfo=UTC), tuesday) is True   # Wed 05:59 IST
    assert answer_open(datetime(2026, 11, 4, 0, 30, tzinfo=UTC), tuesday) is False  # Wed 06:00 IST

def test_answer_open_refuses_a_naive_time() -> None:
    with pytest.raises(ValueError):
        answer_open(datetime(2026, 11, 4), date(2026, 11, 3))
```

- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_practice_schedule.py -v`. Expected: ImportError on the new names.
- [ ] **Step 3: Implement the four names in `domain/practice_schedule.py`.** Pure, no clock.
- [ ] **Step 4: Run to verify they pass.** Same command. Expected: all PASS.
- [ ] **Step 5: Commit** with `feat(practice): say which kind of problem a day has`.

## Task 3: Design problems, loader and picker [sonnet]

**Files:** Create `domain/practice_design.py`, `services/practice/design_catalogue.py`, `tests/test_practice_design.py`. Modify `domain/practice_pick.py` (rename `_rng` to public `day_rng`; update its callers).

**Interfaces:**
- Consumes: `HistoryItem`, `topic_label`, `NEGLECT_AFTER_DAYS`, `NEGLECT_FACTOR`, `RECENT_DAYS`, `RECENT_FACTOR`, `day_rng` from `domain.practice_pick`; `Track` from `domain.practice_topics`.
- Produces in `domain/practice_design.py`:
  - frozen `DesignProblem(slug: str, title: str, tracks: tuple[Track, ...], minutes: int, topics: tuple[str, ...], summary: str, context: tuple[str, ...], must_handle: tuple[str, ...], show_in_answer: tuple[str, ...])`
  - frozen `DesignPick(problem: DesignProblem, topic: str, reason: str, repeat: bool)`
  - `DESIGN_TOPICS: tuple[str, ...]`: `caching, queues, data-modelling, realtime, search, payments, rate-limiting, storage, feeds, scheduling, consistency, observability, ml-serving, pipelines, offline-sync, api-design`
  - `pick_design(*, user_id: str, day: date, swaps: int, track: Track, history: Sequence[HistoryItem], problems: Sequence[DesignProblem]) -> DesignPick | None`
- Produces in `services/practice/design_catalogue.py`:
  - `DESIGN_DIR = <repo>/data/practice/system_design`
  - `parse_problem(raw: object) -> DesignProblem` (raises `ValueError` naming the bad field)
  - `load_problems(path: Path = DESIGN_DIR) -> tuple[DesignProblem, ...]` cached once per process like `load_catalogue`; a missing folder is `()`; an unreadable or invalid file is logged and skipped, so one bad file never breaks today's page.

**Picker rules** (spec 4.3): candidates are problems whose `tracks` include `track` or `"general"`; if none, all problems. Draw a topic by weight using the DSA neglect rule (base 1.0, ×2 never practised or older than 21 days, ×0.3 within 2 days), then a problem in that topic that is not done. Each swap replays a fresh draw excluding earlier draws of that day, as `pick_question` does. When every candidate is done, the one done longest ago comes back with `repeat=True`. Reason format keeps the `" — "` separator the copilot card splits on: `"Caching — you have not practised it yet."`; repeat: `f"You did this {weeks} weeks ago. See what you would change."` (`weeks` at least 1).

- [ ] **Step 1: Write the failing tests** (a local `problem(slug, topics, tracks)` builder, no files):

```python
def test_the_same_inputs_give_the_same_pick() -> None: ...
def test_a_swap_gives_a_different_problem(): ...  # swaps=1 slug != swaps=0 slug
def test_only_the_persons_track_and_general_are_picked() -> None: ...
def test_a_done_problem_does_not_come_back_while_others_remain() -> None: ...
def test_a_problem_swapped_away_and_not_done_can_come_back() -> None: ...  # done=False is not done
def test_when_all_are_done_the_oldest_comes_back_and_says_so() -> None:
    picked = pick_design(...)
    assert picked.repeat is True and picked.problem.slug == "oldest"
    assert picked.reason == "You did this 14 weeks ago. See what you would change."
def test_no_problems_gives_none() -> None: ...
def test_the_reason_splits_on_the_topic_separator() -> None:
    assert " — " in pick_design(...).reason

def test_parse_problem_names_the_bad_field() -> None:
    with pytest.raises(ValueError, match="must_handle"): parse_problem({**GOOD, "must_handle": "x"})
def test_load_problems_skips_a_bad_file_and_keeps_the_rest(tmp_path) -> None: ...
def test_load_problems_on_a_missing_folder_is_empty(tmp_path) -> None: ...
```

- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_practice_design.py tests/test_practice_pick.py -v`. Expected: ImportError.
- [ ] **Step 3: Implement** the names above. Validate the way `load_briefs` in `services/practice/challenges.py` validates a brief: slug pattern `[a-z0-9]+(?:-[a-z0-9]+)*`, non-empty text and lists, `minutes` 15–60, tracks and topics from the known sets, unknown keys refused.
- [ ] **Step 4: Run to verify they pass.** Same command, plus `mypy domain/practice_design.py services/practice/design_catalogue.py`.
- [ ] **Step 5: Commit** with `feat(practice): design problems and their pick`.

## Task 4: Reading a drawing [sonnet]

**Files:** Modify `domain/practice_design.py`, `tests/test_practice_design.py`.

**Interfaces — Produces:** `MAX_SCENE_BYTES = 200_000`, `MAX_ELEMENTS = 500`, `MAX_REASONING_CHARS = 4_000`, `MIN_LABELLED_SHAPES = 3`, `MIN_REASONING_CHARS = 40`, `REFUSED_ELEMENT_TYPES = frozenset({"image", "embeddable", "iframe"})`; frozen `SceneReading(text: str, labelled_shapes: int, unread: int)`; `describe_scene(elements: Sequence[object]) -> SceneReading`; `scene_refusal(elements: object) -> str | None` (a sentence for the person, else `None`).

**Rules:** skip anything not a dict and anything with `isDeleted` true. Rectangle, ellipse and diamond are shapes; a label is the `text` of a text element whose `containerId` is that shape's id. An arrow bound at both ends is `A -> B`, with its own label as `A -[label]-> B`; an unlabelled shape at the end of a bound arrow reads as `(unlabelled)`. Text with no container is a note. An arrow bound at one end or none, a `line`, and a `freedraw` each add to `unread`. Labels are flattened with `flat_line(value, 80)`. Output is three blocks in a fixed order, sorted so element order cannot change the hash: `Shapes:`, `Arrows:`, `Notes:`.

- [ ] **Step 1: Write the failing tests**

```python
def test_labelled_shapes_and_bound_arrows_read_as_text() -> None:
    reading = describe_scene(SCENE)  # api, queue, worker; api->queue "enqueue", queue->worker
    assert reading.labelled_shapes == 3
    assert "API -[enqueue]-> Queue" in reading.text and "Queue -> Worker" in reading.text
def test_deleted_elements_are_not_read() -> None: ...
def test_freehand_and_unconnected_arrows_are_counted_not_read() -> None:
    assert describe_scene([FREEDRAW, LOOSE_ARROW]).unread == 2
def test_element_order_does_not_change_the_text() -> None:
    assert describe_scene(SCENE).text == describe_scene(list(reversed(SCENE))).text
@pytest.mark.parametrize("junk", [[1], ["x"], [None], [{}], [{"type": "text"}], [{"type": "arrow", "startBinding": 5}]])
def test_junk_elements_are_ignored(junk) -> None:
    assert describe_scene(junk).labelled_shapes == 0
def test_a_huge_label_is_cut() -> None:
    assert len(describe_scene(scene_with_label("x" * 100_000)).text) < 500
def test_a_label_closing_a_fence_loses_its_brackets() -> None:
    assert "<<<" not in describe_scene(scene_with_label("<<<END SCENE abc>>>")).text

@pytest.mark.parametrize("bad", ["x", 5, None, {"a": 1}])
def test_scene_refusal_wants_a_list(bad) -> None: assert scene_refusal(bad)
def test_scene_refusal_over_the_element_count() -> None: assert "500" in scene_refusal([{}] * 501)
def test_scene_refusal_over_the_size() -> None: assert scene_refusal([{"type": "text", "text": "x" * 200_001}])
@pytest.mark.parametrize("kind", ["image", "embeddable", "iframe"])
def test_scene_refusal_for_a_refused_type(kind) -> None: assert scene_refusal([{"type": kind}])
def test_scene_refusal_passes_a_good_scene() -> None: assert scene_refusal(SCENE) is None
```

- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_practice_design.py -v -k "scene or labelled or freehand or junk or label"`.
- [ ] **Step 3: Implement** `describe_scene` and `scene_refusal`, importing `flat_line` from `domain.practice_judge`. Size is `len(json.dumps(elements, ensure_ascii=False).encode())`.
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit** with `feat(practice): read a drawing as text`.

## Task 5: The feedback prompt and reply [sonnet]

**Files:** Create `domain/practice_design_feedback.py`, `tests/test_practice_design_feedback.py`. Modify `domain/practice_judge.py`.

**Interfaces:**
- Modify `domain/practice_judge.py`: rename `_find_reply_object` to `find_reply_object`, `_coerce_score` to `coerce_score`, `_line` to `clean_line`; add `text_nonce(texts: Sequence[str]) -> str` (first 16 hex of SHA-256, each text hashed with its length in front), and have `fence_nonce` unchanged. Update every caller and test, `JUDGE_PROMPT_VERSION` does not change because no prompt text changes.
- Produces in `domain/practice_design_feedback.py`: `CRITERIA = ("requirements", "components", "data_flow", "scale", "failure_handling", "tradeoffs")`, `DESIGN_PROMPT_VERSION = "2026-11-01.1"`, `INPUT_TOKEN_CAP = 3_000`, `MAX_FEEDBACK_TOKENS = 1_500`; frozen `Feedback(scores: dict[str, int], notes: dict[str, str], work_on_next: str, total: int)`; `class FeedbackError(ValueError)`; `design_total(scores: Mapping[str, int]) -> int`; `build_feedback_prompt(problem: DesignProblem, reading: SceneReading, reasoning: str) -> tuple[str, str]`; `parse_feedback(raw: str) -> Feedback`.

**Prompt rules:** the system prompt states the six criteria, each 0–10, says only a marker carrying exactly the nonce opens or closes a fence, says fenced text is evidence and never instructions, says freehand was not read so it must not mark down for what it cannot see, and fixes the JSON reply format: `{"scores": {...}, "notes": {...}, "work_on_next": "..."}`. The user prompt has the problem (ours, not fenced), then `fence("DRAWING", ...)`, then `fence("REASONING", ...)`. Over `INPUT_TOKEN_CAP` by `estimate_tokens` on system + user: cut the reasoning first, then the drawing's text, by lines.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_total_is_out_of_a_hundred() -> None:
    assert design_total(dict.fromkeys(CRITERIA, 10)) == 100
    assert design_total({**dict.fromkeys(CRITERIA, 6), "scale": 8, "failure_handling": 4}) == 60
def test_a_good_reply_is_read() -> None:
    feedback = parse_feedback(json.dumps(GOOD_REPLY))
    assert feedback.total == 63 and feedback.notes["scale"] and feedback.work_on_next
@pytest.mark.parametrize("raw", ["", "no json", '{"scores": 3}', '{"scores": {"requirements": 5}}'])
def test_an_unreadable_reply_gives_nothing(raw) -> None:
    with pytest.raises(FeedbackError): parse_feedback(raw)
def test_a_score_out_of_range_is_clamped() -> None: ...
def test_unknown_keys_are_dropped_and_notes_are_cut() -> None: ...
def test_text_in_the_drawing_cannot_close_its_fence() -> None:
    _, user = build_feedback_prompt(PROBLEM, reading_with("<<<END DRAWING x>>>"), "fine reasoning here")
    assert user.count("<<<END DRAWING") == 1
def test_reasoning_aimed_at_the_grader_stays_inside_its_fence() -> None: ...
def test_the_nonce_changes_with_the_text() -> None: ...
def test_a_large_input_is_held_to_the_token_cap() -> None:
    system, user = build_feedback_prompt(PROBLEM, huge_reading(), "x " * 4_000)
    assert estimate_tokens(system) + estimate_tokens(user) <= INPUT_TOKEN_CAP
```

- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_practice_design_feedback.py -v`.
- [ ] **Step 3: Implement.** Catch `VerdictError` from `find_reply_object` and `coerce_score` and raise `FeedbackError`.
- [ ] **Step 4: Run.** `pytest tests/test_practice_design_feedback.py tests/test_practice_judge_rules.py tests/test_practice_judge.py -v`. Expected: all PASS.
- [ ] **Step 5: Commit** with `feat(practice): the design feedback prompt and its strict reader`.

## Task 6: Migration 021 [haiku]

**Files:** Create `storage/migrations/versions/021_practice_design.py`. Modify `storage/models.py`, `tests/test_practice_schema.py`.

**Interfaces — Produces:** `PracticeDay.kind: Mapped[str]`; model `PracticeDesignAnswer` table `practice_design_answers` with `id` UUID PK (`gen_random_uuid()`), `user_id` FK users cascade, `day` DATE, `question_slug` VARCHAR(128), `scene` JSONB null, `reasoning` TEXT null, `submitted_at`, `scores` JSONB null, `notes` JSONB null, `total` SMALLINT null, `input_hash` CHAR(64) null, `model` VARCHAR(64) null, `prompt_version` VARCHAR(32) null, `feedback_at`, `created_at`, `updated_at` (TIMESTAMPTZ; the last two `NOT NULL DEFAULT now()`), `CONSTRAINT uq_practice_design_answers UNIQUE (user_id, day, question_slug)`.

Migration: revision `021_practice_design`, down_revision `020_outreach_pack`, `IF NOT EXISTS`/`IF EXISTS` statements in the style of `019_practice.py`, including `ALTER TABLE practice_days ADD COLUMN IF NOT EXISTS kind VARCHAR(8) NOT NULL DEFAULT 'dsa'`. Downgrade drops the table and the column.

- [ ] **Step 1: Extend `tests/test_practice_schema.py`** with the new table and column in its drift comparison, plus `test_existing_rows_read_as_dsa`, `test_one_answer_per_user_day_and_slug` (a second insert raises `IntegrityError`), `test_021_points_at_020`.
- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_practice_schema.py -v`.
- [ ] **Step 3: Implement** the model and migration.
- [ ] **Step 4: Run.** Same command, and `pytest tests/test_outreach_pack_schema.py -v` (single head still holds).
- [ ] **Step 5: Commit** with `feat(practice): store a day's kind and design answers`.

## Task 7: `daily.py` learns kinds [sonnet]

**Files:** Modify `services/practice/daily.py`, `api/schemas/practice_schemas.py`, `tests/test_practice_daily.py`.

**Interfaces:**
- Consumes: Task 2, `pick_design`, `load_problems`, `PracticeDay.kind`.
- Produces: rename to public `DayState` (`_Day`), `load_day` (`_load`), `ShownProblem` (`_Shown`), `shown_problem` (`_shown`). `ShownProblem` gains `kind: DayKind`, `design: DesignProblem | None`, and `question: Question | None`; properties `slug: str`, `difficulty: str` (`"medium"` for design). `DayState` gains `problems: tuple[DesignProblem, ...]`, `design_history: tuple[HistoryItem, ...]`, `has_answer: bool`.
- Payload: `today` adds `kind: DayKind | None` and `design: {slug, title, minutes, topics, has_answer} | None`. `question` stays for DSA and is null on a design day.

**Rules:**
- `load_day`: `history` is `kind = 'dsa'` rows only (180 days, as now). `design_history` is every `kind = 'design'` row before today. Today's row is found regardless of kind.
- `shown_problem`: a row's stored `kind` and slug win while the slug is still in that list; if it is gone, pick afresh for that kind; else `effective_kind(kind_for_day(day), has_dsa=..., has_design=...)`.
- `swap`: same kind as `shown_problem`; `_claim_swap` writes `kind`.
- `mark_done`: raises `PracticeConflict` when the day's problem is design, message `"Draw this one and ask for feedback: that is what marks it done."`
- `undo_done` works on both kinds.
- Change the module docstring's "No streak" paragraph and `MAX_WEEKS` later in Task 8.

- [ ] **Step 1: Write the failing tests**, adding `kind`, `design` to `TODAY_KEYS`:

```python
TUESDAY = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)
async def test_a_tuesday_is_a_design_day(db, asha) -> None:
    payload = await daily.today(asha, now=TUESDAY)
    assert payload["kind"] == "design" and payload["question"] is None and payload["design"]["slug"]
async def test_a_monday_is_a_dsa_day(db, asha) -> None: ...
async def test_a_dsa_day_with_no_catalogue_shows_a_design_problem(db, asha, empty_catalogue) -> None: ...
async def test_a_design_day_with_no_problems_shows_a_dsa_question(db, asha, no_problems) -> None: ...
async def test_with_neither_list_the_catalogue_is_empty(db, asha, empty_catalogue, no_problems) -> None:
    assert (await daily.today(asha, now=TUESDAY))["catalogue"] == "empty"
async def test_mark_done_on_a_design_day_is_refused(db, asha) -> None:
    with pytest.raises(PracticeConflict): await daily.mark_done(asha, None, now=TUESDAY)
async def test_swap_on_a_design_day_stays_design(db, asha) -> None: ...
async def test_design_rows_do_not_move_the_dsa_pick(db, asha) -> None: ...
async def test_a_stored_design_slug_no_longer_in_the_list_still_answers(db, asha) -> None: ...
async def test_has_answer_follows_todays_answer(db, asha) -> None: ...
```

- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_practice_daily.py -v`.
- [ ] **Step 3: Implement** in `daily.py`, and add `PracticeDesignBlockSchema`, `kind`, `design` to `PracticeTodaySchema`. Patch `load_problems` in the test fixture beside `load_catalogue` so existing tests keep a fixed list. The module's `NOW` is a Thursday, now a design day, so existing DSA tests move: change `NOW` to Wed 14 Oct 2026 06:00 UTC and `TODAY` to match, and do the same in `tests/test_practice_copilot.py`.
- [ ] **Step 4: Run.** `pytest tests/test_practice_daily.py tests/test_practice_copilot.py -v`. Expected: PASS. Copilot tests may need the same date change; Task 12 owns their behaviour.
- [ ] **Step 5: Commit** with `feat(practice): a day is DSA or system design`.

## Task 8: History for the graph, and totals that never fall [sonnet]

**Files:** Modify `services/practice/daily.py`, `services/practice/challenges.py`, `api/routers/practice.py`, `api/schemas/practice_schemas.py`, `tests/test_practice_daily.py`, `tests/test_practice_challenges.py`.

**Interfaces — Produces** on `history()`:
- `MAX_WEEKS = 53`; router `Query(ge=1, le=53)`.
- `days[]`: adds `kind: DayKind | None`, `contest: bool` (the IST day of the user's `practice_attempts.submitted_at`).
- `counts`: adds `current_streak: int`; `this_month`, `last_30`, `longest_run` stay.
- `solved: {easy, medium, hard, design, total}`: distinct `(kind, slug)` among all done rows; design rows count only under `design`.
- `available: {easy: int | None, medium: int | None, hard: int | None, design: int | None}`: from `load_catalogue` and `load_problems`, `None` for a list not loaded.
- `topics`: all done DSA rows, by drawn topic.
- `_current_streak(days: Iterable[date], today: date) -> int`: consecutive done days ending today if today is done, else ending yesterday.
- `_prune_days` in `challenges.py`: adds `PracticeDay.done_at.is_(None)`.

- [ ] **Step 1: Write the failing tests**, updating `HISTORY_KEYS`, `HISTORY_DAY_KEYS`, `COUNT_KEYS`; replace the three "streak" absence assertions (`test_practice_daily.py` lines ~254, ~940, ~1135, ~1215) because the reversal is deliberate:

```python
async def test_the_streak_counts_up_to_yesterday_while_today_is_open(db, asha) -> None: ...
async def test_doing_today_extends_the_streak(db, asha) -> None: ...
async def test_a_missed_day_ends_the_streak(db, asha) -> None: ...
async def test_solved_counts_distinct_problems(db, asha) -> None:
    # the same design slug done on two days
    assert (await daily.history(asha, now=NOW))["solved"]["design"] == 1
async def test_design_rows_are_not_counted_as_medium(db, asha) -> None: ...
async def test_available_is_none_for_a_list_not_loaded(db, asha, empty_catalogue) -> None: ...
async def test_a_submitted_contest_marks_its_day_without_counting_as_solved(db, asha) -> None: ...
async def test_rows_older_than_retention_still_count_in_solved(db, asha) -> None: ...
async def test_fifty_three_weeks_are_allowed_and_fifty_four_are_not(client, asha) -> None: ...
# tests/test_practice_challenges.py
async def test_prune_keeps_done_days_and_drops_undone_old_ones(db, asha) -> None: ...
```

- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_practice_daily.py tests/test_practice_challenges.py -v`.
- [ ] **Step 3: Implement**; update the schema docstrings ("deliberately no current streak") and the `daily.py` module docstring and `RETENTION_DAYS` comment to say what is now true.
- [ ] **Step 4: Run.** Same command. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(practice): history for the year graph, with a streak and distinct solved counts`.

## Task 9: Where you stand on the overall board [haiku]

**Files:** Modify `domain/practice_board.py`, `services/practice/board.py`, `api/schemas/practice_schemas.py`, `tests/test_practice_board.py`.

**Interfaces — Produces:** `RANKED_FOR_STANDING = 10`; `top_percent(rank: int | None, total_ranked: int) -> int | None` (`ceil(rank / total_ranked * 100)`); `score_bands(scores: Sequence[float]) -> list[int] | None` (20 bands of 20 across 0–400, a 400 lands in the last, `None` below 10 scores). `overall_board` adds `total_ranked: int`, `top_percent: int | None`, `distribution: list[int] | None`, all built from the cached entries with no new query.

- [ ] **Step 1: Write the failing tests**

```python
def test_top_percent_rounds_up() -> None: assert top_percent(3, 25) == 12
def test_top_percent_is_hidden_below_ten_ranked() -> None: assert top_percent(1, 9) is None
def test_top_percent_is_none_without_a_rank() -> None: assert top_percent(None, 50) is None
def test_bands_are_twenty_and_hold_every_score() -> None:
    bands = score_bands([0.0, 19.9, 20.0, 400.0] * 3)
    assert len(bands) == 20 and sum(bands) == 12 and bands[0] == 6 and bands[1] == 3 and bands[-1] == 3
def test_bands_are_hidden_below_ten() -> None: assert score_bands([1.0] * 9) is None
async def test_the_overall_board_reports_where_you_stand(db, ...) -> None: ...
```

- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_practice_board.py -v`.
- [ ] **Step 3: Implement**, and add the three fields to `OverallBoardSchema`.
- [ ] **Step 4: Run.** Same command. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(practice): where you stand on the overall board`.

## Task 10: Reading and saving an answer [sonnet]

**Files:** Create `services/practice/design.py`, `tests/test_practice_design_service.py`. Modify `api/routers/practice.py`, `api/schemas/practice_schemas.py`, `services/practice/challenges.py` (`prune`), `services/practice/steps.py`, `tests/test_practice_steps.py`.

**Interfaces:**
- Consumes: Tasks 2–7. `daily.load_day`, `daily.shown_problem`.
- Produces:
  - `read_page(user_id: str, slug: str, day: date | None = None, *, now: datetime | None = None) -> dict[str, Any]`
  - `save_answer(user_id: str, slug: str, elements: object, reasoning: object, *, now: datetime | None = None) -> dict[str, Any]` returning `{day, saved_at}`
  - `open_day(session, user: uuid.UUID, slug: str, moment: datetime) -> date | None`
  - `prune_answers(moment: datetime) -> int`
- Page payload: `{problem: {slug,title,minutes,topics,summary,context,must_handle,show_in_answer}, day: str | None, answer: {elements: list | None, reasoning: str, saved_at: str} | None, editable: bool, done: bool, last_answer_day: str | None, feedback: {scores, notes, work_on_next, total, at} | None, feedback_problem: None}`
- Routes: `GET /practice/design/{slug}?day=YYYY-MM-DD`, `PUT /practice/design/{slug}/answer`. Request body `{elements: list[Any], reasoning: str}` with a generous cap, so the service's sentence is what a person reads.

**Rules** (spec 4.5):
- `open_day`: today, if the slug is today's shown design problem; else yesterday, if `answer_open(moment, yesterday)` and yesterday's row is design with that slug; else `None`. Today wins on a tie.
- `read_page` without `day`: the open day's answer, editable; else the newest answer, read-only; else 404. With `day`: that answer, editable only if it is the open day; else 404. A problem no longer in the list is a 404.
- A swapped-away answer is simply an answer whose slug is not its day's row slug, so `open_day` never returns its day for it.
- `last_answer_day`: the newest answer of this slug on an earlier day that still has a scene.
- `save_answer`: `scene_refusal` → `PracticeInvalid`; reasoning over 4,000 → `PracticeInvalid`; `open_day` is `None` → `PracticeConflict`; rate guard is `tr:practice:design-save:<user>:<minute>` above ten → `PracticeRateLimited`, in the same way as `challenges._count_save`. First save inserts the `practice_days` row (`kind='design'`, `difficulty='medium'`, not done) and the answer in one transaction; a lost insert race finds the row and goes on. Stored text goes through `challenges._clean_text`, made public as `clean_text`.
- `prune_answers`: clears `scene` and `reasoning` past 180 days and deletes rows past 400. `challenges.prune` returns a new `pruned_answers` key, and `steps._challenges_step` reports it; update its docstring and the "exactly" test.

- [ ] **Step 1: Write the failing tests** (SQLite fixture copied from `tests/test_practice_daily.py`; `load_problems` patched to three fixtures; `TUESDAY` as Task 7):

```python
async def test_todays_problem_opens_with_no_answer_and_writes_nothing(db, asha) -> None: ...
async def test_a_problem_that_is_not_today_and_never_answered_is_a_404(db, asha) -> None: ...
async def test_another_persons_answer_is_a_404(db, asha, ben) -> None: ...
async def test_first_save_pins_the_problem_and_the_day(db, asha) -> None: ...
async def test_saving_twice_keeps_one_answer(db, asha) -> None: ...
async def test_two_first_saves_at_once_end_in_one_row(db, asha) -> None: ...
@pytest.mark.parametrize("bad", ["x", 5, None, [{"type": "image"}]])
async def test_a_bad_drawing_is_refused_in_words(db, asha, bad) -> None: ...
async def test_reasoning_over_the_limit_is_refused(db, asha) -> None: ...
async def test_the_eleventh_save_in_a_minute_is_limited(db, asha) -> None: ...
async def test_yesterdays_problem_stays_open_until_six(db, asha) -> None: ...
async def test_after_six_it_is_read_only_and_a_save_is_refused(db, asha) -> None: ...
async def test_a_swapped_away_answer_is_read_only_and_uncredited(db, asha) -> None: ...
async def test_a_repeat_starts_blank_and_points_at_the_last_answer(db, asha) -> None:
    assert page["answer"] is None and page["last_answer_day"] == "2026-08-04"
async def test_a_past_answer_opens_read_only_by_day(db, asha) -> None: ...
async def test_a_day_that_is_not_a_date_is_a_422(client, asha) -> None: ...
async def test_a_past_answer_whose_problem_was_removed_is_a_404(db, asha) -> None: ...
async def test_prune_clears_old_drawings_and_deletes_older_rows(db, asha) -> None: ...
async def test_the_routes_need_a_token(client) -> None: ...
```

- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_practice_design_service.py tests/test_practice_steps.py -v`.
- [ ] **Step 3: Implement.** Keep the router thin; add `DesignPageSchema`, `DesignAnswerRequestSchema`, `DesignSavedSchema`.
- [ ] **Step 4: Run.** Same command plus `mypy services/practice/design.py api/routers/practice.py`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(practice): open, save and read design answers`.

## Task 11: Immediate feedback [sonnet]

**Files:** Create `services/practice/design_feedback.py`, `tests/test_practice_design_feedback_service.py`. Modify `api/routers/practice.py`, `config/settings.py`, `services/practice/judge.py`, `tests/test_practice_judge.py`.

**Interfaces:**
- Consumes: Tasks 4, 5, 10; `services.llm._chat`, `services.llm.is_request_too_large`, `services.llm_budget.claim`.
- Produces:
  - `request_feedback(user_id: str, slug: str, *, now: datetime | None = None) -> dict[str, Any]`: the page payload from `read_page`, with `feedback_problem` set to `"limit"` or `"busy"` when no result was made.
  - `PER_USER_DAILY_DESIGN_FEEDBACK = 3`, `DAILY_DESIGN_FEEDBACK_BUDGET = 150`.
  - `settings.design_feedback_per_minute: int` default 1.
  - In `judge.py`: `JUDGING_KEY = "tr:practice:judging"`, TTL 600 s, set by `run_judge_step` once `_due_attempts` is non-empty, deleted in a `finally`. No other judge change.
  - Route `POST /practice/design/{slug}/feedback`.

**Order, exactly** (spec 4.6), with no session open across the model call:
1. `open_day` → none and no answer: 404 (`PracticeNotFound`); an answer exists but no open day: `PracticeConflict`. 
2. `describe_scene` and reasoning below the minimums → `PracticeInvalid` naming which is missing; the day is not marked done.
3. Update the row `done_at` where null; commit.
4. Build the prompt. Same `input_sha256`, `settings.groq_model` and `DESIGN_PROMPT_VERSION` as stored → return the stored result, no call, no budget.
5. Lock `tr:lock:design-feedback:<user>:<answer id>`, using the `_claim`/`_release` pattern in `services/outreach/pack.py`; lost → `PracticeConflict("Your feedback is on its way.")`. Release in a `finally`.
6. `JUDGING_KEY` present → `busy`. Claim per-minute `tr:budget:design-feedback-minute:<minute>` (TTL 120) → over: `busy`. Claim `tr:budget:design-feedback:user:<user>:<IST day>` → over: `limit`. Claim `tr:budget:design-feedback:<UTC day>` → over: `limit`.
7. `_chat(system, user, model=..., temperature=0, max_tokens=MAX_FEEDBACK_TOKENS, json_mode=True, reasoning_effort="medium")`. `_chat` is synchronous or async as defined in `services/llm.py`: call it the way `services/practice/judge.py:784` does.
8. `parse_feedback`; store scores, notes (with `work_on_next` under its own key), total, hash, model, version, `submitted_at` if empty, `feedback_at`. A newer result replaces the older.

Any exception from the model, or `FeedbackError`, or a too-large request → `busy`, logged by class name, nothing stored.

- [ ] **Step 1: Write the failing tests**, with `_chat` patched:

```python
async def test_a_good_reply_is_stored_and_returned(db, asha, chat) -> None: ...
async def test_the_day_is_done_before_the_model_is_called(db, asha, chat) -> None:
    # chat side effect reads the row in a new session and asserts done_at is set
async def test_a_model_failure_stores_no_score_and_keeps_the_day(db, asha, failing_chat) -> None:
    page = await design_feedback.request_feedback(asha, SLUG, now=TUESDAY)
    assert page["feedback"] is None and page["feedback_problem"] == "busy" and page["done"] is True
async def test_an_unreadable_reply_stores_no_score(db, asha, chat) -> None: ...
async def test_the_same_input_makes_no_second_call_and_spends_no_budget(db, asha, chat) -> None: ...
async def test_a_changed_prompt_version_calls_again(db, asha, chat, monkeypatch) -> None: ...
async def test_too_little_drawn_is_refused_and_not_marked_done(db, asha, chat) -> None: ...
async def test_too_little_reasoning_is_refused_and_not_marked_done(db, asha, chat) -> None: ...
async def test_the_fourth_request_of_a_day_is_over_the_limit(db, asha, chat) -> None: ...
async def test_the_shared_budget_stops_everyone(db, asha, chat) -> None: ...
async def test_busy_over_the_minute_costs_no_daily_unit(db, asha, chat) -> None: ...
async def test_during_a_judge_run_feedback_is_busy_and_makes_no_call(db, asha, chat) -> None: ...
async def test_two_presses_at_once_make_one_call(db, asha, slow_chat) -> None:
    results = await asyncio.gather(press(), press(), return_exceptions=True)
    assert slow_chat.await_count == 1 and sum(isinstance(r, PracticeConflict) for r in results) == 1
async def test_a_swapped_away_answer_gets_no_feedback(db, asha, chat) -> None: ...
async def test_after_six_gets_no_feedback(db, asha, chat) -> None: ...
async def test_yesterdays_answer_before_six_marks_yesterday(db, asha, chat) -> None: ...
async def test_another_persons_answer_is_a_404(db, asha, ben, chat) -> None: ...
async def test_the_model_id_comes_from_settings(db, asha, chat) -> None: ...
# tests/test_practice_judge.py
async def test_the_judge_holds_its_key_only_while_it_has_attempts(...) -> None: ...
```

- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_practice_design_feedback_service.py tests/test_practice_judge.py -v`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Same command plus `mypy services/practice/design_feedback.py`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(practice): immediate feedback on a design answer`.

## Task 12: The Overview names the real problem [sonnet]

**Files:** Modify `services/copilot.py`, `tests/test_practice_copilot.py`, `frontend/app/dashboard/page.tsx`, `frontend/components/BriefingFeed.tsx`.

**Interfaces — Produces:** `_today_card` on a design day: title `f"Today's system design: {design['title']}"`, detail `" · ".join((f"About {minutes} min", topic, why))` from the same `" — "` split, actions `[{"label": "Draw and solve", "href": f"/practice/design/{slug}", "style": "primary"}, {"label": "Open Practice", "href": "/practice", "style": "ghost"}]`, `meta: {"kind": "design", "slug": slug}`. DSA card: `meta` gains `"kind": "dsa"`, otherwise unchanged. `_practice_suggestion_card`: the 14-day check gates only the contest card; `_today_card` is offered to everyone.

- [ ] **Step 1: Write the failing tests.** Replace `test_someone_who_never_practised_gets_no_card_at_all` and `test_a_day_that_was_not_marked_done_is_not_practice`, whose rule is deliberately gone:

```python
async def test_someone_who_never_practised_gets_todays_problem(db, asha) -> None: ...
async def test_someone_who_never_practised_gets_no_contest_card(db, asha) -> None: ...  # Saturday 21:00 IST
async def test_a_design_day_links_to_its_page(db, asha) -> None:
    assert card["actions"][0]["href"] == f"/practice/design/{slug}" and card["meta"]["kind"] == "design"
    assert card["title"].startswith("Today's system design: ")
async def test_a_design_day_marked_done_has_no_card(db, asha) -> None: ...
async def test_practice_today_stays_off_the_models_allowlist() -> None:
    assert "practice_today" not in MODEL_VISIBLE_CARD_KINDS
```

- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_practice_copilot.py -v`.
- [ ] **Step 3: Implement.** In `dashboard/page.tsx:287` the tag becomes `practice.kind === 'practice_contest' ? 'Weekly contest' : practice.meta?.kind === 'design' ? 'System design' : 'Daily question'`; apply the same in `BriefingFeed.tsx`, whose label map is per kind. The dashboard's comment above it says "today's question", leave the wording accurate.
- [ ] **Step 4: Run.** `pytest tests/test_practice_copilot.py -v`; from `frontend/`, `npm run lint`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(overview): today's problem names system design, and reaches everyone`.

## Task 13: Write the 40 problems [sonnet]

**Files:** Create `data/practice/system_design/01-<slug>.json` … `40-<slug>.json`, `tests/test_practice_design_problems.py`.

**Rules:** own wording; fictional companies only, with a Bengaluru setting where it fits, as `data/practice/challenges/*.json` do; nothing copied from a book, course or site; a real scenario and not a clone of the usual interview list. Each has `slug, title, tracks, minutes (20–45), topics (1–3 from DESIGN_TOPICS), summary (one sentence), context (2–3 short paragraphs), must_handle (4–6), show_in_answer (3–5)`. `show_in_answer` tells the person what to draw and explain, and never contains a solution. Spread: every `Track` has at least 12 problems counting `general`; every topic in `DESIGN_TOPICS` has at least 2.

- [ ] **Step 1: Write the failing test**

```python
PROBLEMS = load_problems_strict()  # parses every file with parse_problem; any failure fails the test
def test_there_are_about_forty() -> None: assert 38 <= len(PROBLEMS) <= 45
def test_slugs_are_unique_and_match_their_files() -> None: ...
@pytest.mark.parametrize("track", get_args(Track))
def test_every_track_has_at_least_twelve(track) -> None: ...
def test_every_topic_has_at_least_two() -> None: ...
def test_no_problem_names_a_real_company_on_the_list() -> None:
    # a short list of the obvious: Flipkart, Swiggy, Zomato, Uber, Google, Amazon, Netflix, Razorpay
```

- [ ] **Step 2: Run to verify it fails.** `pytest tests/test_practice_design_problems.py -v`.
- [ ] **Step 3: Write the files.**
- [ ] **Step 4: Run.** Same command. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(practice): forty system design problems`.

## Task 14: API types and the graph's rules [haiku]

**Files:** Modify `frontend/lib/api.ts`. Create `frontend/lib/practice-stats.ts`, `frontend/e2e/practice-stats.rules.spec.ts`.

**Interfaces — Produces:**
- Types: `PracticeKind = 'dsa' | 'design'`; `PracticeToday` gains `kind: PracticeKind | null`, `design: { slug: string; title: string; minutes: number; topics: string[]; has_answer: boolean } | null`; `PracticeHistory` days gain `kind`, `contest`, counts gain `current_streak`, plus `solved`, `available`; `OverallBoard` gains `total_ranked`, `top_percent`, `distribution`; `DesignPage`, `DesignFeedback` as Task 10's payload.
- `api.practice.design(slug, day?)`, `api.practice.saveAnswer(slug, body, options?: { keepalive?: boolean })`, `api.practice.feedback(slug)`. `history` default stays; the page passes 53.
- `practice-stats.ts`: `heatLevel(day): 0 | 1 | 2 | 3`; `heatColumns(days)` (Monday-first weeks, padded like `Calendar` in `practice/page.tsx`); `monthMarks(days): Array<{ label: string; leftPercent: number }>`; `isNewToPractice(history): boolean`; `scoreLine(past): number[]` (judged totals, oldest first).

- [ ] **Step 1: Write the failing rules spec** in the style of `e2e/event-map.rules.spec.ts`:

```ts
test('shade follows difficulty, and a contest is darkest', () => {
  expect(heatLevel(day({ done: true, difficulty: 'easy', kind: 'dsa' }))).toBe(1)
  expect(heatLevel(day({ done: true, difficulty: 'medium', kind: 'dsa' }))).toBe(2)
  expect(heatLevel(day({ done: true, difficulty: 'medium', kind: 'design' }))).toBe(2)
  expect(heatLevel(day({ done: true, difficulty: 'hard', kind: 'dsa' }))).toBe(3)
  expect(heatLevel(day({ contest: true }))).toBe(3)
  expect(heatLevel(day({}))).toBe(0)
})
test('columns are Monday-first weeks of seven', ...)
test('a new person has nothing solved and no contest', ...)
test('a person with only a contest entry is not new', ...)
test('the score line is judged totals, oldest first', ...)
```

- [ ] **Step 2: Run to verify it fails.** From `frontend/`: `npx playwright test e2e/practice-stats.rules.spec.ts`.
- [ ] **Step 3: Implement.** Pure, no React.
- [ ] **Step 4: Run.** Same command plus `npx tsc --noEmit`. Expected: PASS.
- [ ] **Step 5: Commit** with `feat(practice): types and rules for the year graph`.

## Task 15: `ContestBoard`, the shared ranking [sonnet]

Named by the approved spec, built before the pages that use it. Replaces `components/practice/BoardPeek.tsx` (delete it).

**Files:** Create `frontend/components/practice/ContestBoard.tsx`. Modify `frontend/app/globals.css`.

**Interfaces — Produces:** `<ContestBoard slug?: string | null, tabs?: boolean />`. With `tabs`, a This week / Overall switch; without, the one board for `slug`. Fetches `api.practice.board` or `api.practice.overall`, tags each read with what it was a read of so a slow answer for a tab the viewer left is never shown (move this logic and `challengeLine`, `overallLine`, `Rows`, `ordinal`, `plural`, `windowLine` from `app/practice/leaderboard/page.tsx`). Podium for ranks 1–3 only with at least 3 rows. The viewer's row is always shown. Provisional pill, `queued`, `held` and "no judged entries yet" carry over.

Checklist — Prep pages, from `frontend/app/practice/leaderboard/page.tsx`, members `/practice`, `/practice/challenges/[slug]`, `/practice/leaderboard`, `/interview` of 4 candidates in the frontend app, read 2026-11-01; a snapshot, and the code wins:
- [ ] rows — `.pr-board-head`, `.pr-board`, `.pr-board-row` with `.pr-rank`, `.pr-name`, `.pr-total`, `.pr-hours`, `data-you` on the viewer's — the reference (level 3)
- [ ] switch — `.status-tabs` with `<button>`, `aria-pressed`, `.selected` — the reference; `Segmented` stays for the page-level tabs only
- [ ] numbers — through `FlapText` — the reference
- [ ] failure — `<p className="form-error" role="alert">` with the server's message — 3 of 3 Practice pages
- [ ] loading — `.loading-state` with `Loader2.spin` — 2 of 2 list reads on these pages
- [ ] empty — `.empty-state` with `h2` and `p` — the reference
- [ ] footer — `.pr-foot` — the reference
- [ ] external link — `EXTERNAL_LINK_PROPS` and `externalHref` — shared piece
- [ ] podium — new position: mockup's `.podium`, renamed `.pr-podium`, on theme tokens — as designed; no family convention
- [ ] colours through tokens, `rgb(var(--hi) / …)` for overlays, both themes
- [ ] not copied from the reference: its page heading, its Past builds select, the word "build"

Steps:
1. Re-read the checklist before writing — do not work from memory.
2. Implement, then `cd frontend && npx tsc --noEmit && npm run lint`. Expected: clean. (`/practice` still imports `BoardPeek` until Task 16; delete it there.)
3. Report the piece ready for its check. The dispatcher sends it to the reviewer of this kind, with `ui-consistency:verifying` and this checklist in its brief; fix what it reports.
4. Report status `done` or `parked — <why>` here. Commit with `feat(practice): a contest ranking the pages can share`.

## Task 16: The `/practice` page [sonnet]

**Files:** Modify `frontend/app/practice/page.tsx`, `frontend/app/globals.css`. Create `frontend/components/practice/{ActivityGraph,SolvedSummary,TopicBars,StandingCard,TodayCard,ContestSection}.tsx`. Delete `frontend/components/practice/BoardPeek.tsx`.

**Shape** (mockup, top to bottom): `PrepTabs`; heading with `Today's problems ↓` jump; `ActivityGraph` with `N days practised in the past year`, longest streak, current streak, Easier→Harder legend, 53 weeks, scrolling inside its card and opened at the newest week; `SolvedSummary` (ring of `solved.total`, tiles Easy/Medium/Hard/System design with `/ available` where known); `TopicBars`; `StandingCard` (contest rank, contests, latest score, score line, Top N%, spread bars, each hidden as spec 4.8 says); `TodayCard` (`id="today"`); `ContestSection` (`id="contest"`, this week's contest, `ContestBoard` with `tabs`, your past contests with "Not entered", LeetCode's own contest line); collapsed Leaderboard settings. `isNewToPractice(history)` moves `TodayCard` above the graph. Ask the page for `history(53)`.

**`TodayCard`:** DSA: as now (`Open on LeetCode ↗`, `Mark done`, `Swap`, the rating). Design: `Draw and solve →` linking to `/practice/design/<slug>`, about-how-long, topics, `today.reason`, Swap; done: `See your answer →` and `Done HH:MM`; a Swap with `design.has_answer` asks first with `window.confirm("Your drawing stays saved, but it can't be finished.")`. Neither list loaded (`today.kind === null`): the existing empty state.

Checklist — Prep pages, from `frontend/app/practice/page.tsx` (named by the request), members `/practice`, `/practice/challenges/[slug]`, `/practice/leaderboard`, `/interview` of 4 candidates in the frontend app, read 2026-11-01; a snapshot, and the code wins:
- [ ] page holder — `<RequireAuth><AppShell>` with `<PrepTabs />` first — 3 of 3 Practice pages (read in those files)
- [ ] heading — `section.page-heading.split-heading`, `.board-kicker`, `h1` with the accent `<span>.</span>`, `p` — the reference
- [ ] sections — `section.pr-section` with `.pr-section-head`, `h2` then `span` — the reference, and the challenge page
- [ ] section heading type — `600 19px/1.2 var(--font-display)`, through `.pr-section-head h2`, never partly overridden
- [ ] big numbers — `FlapText`, `700 … var(--font-label)`, tabular-nums, as `.pr-counts b` — the reference
- [ ] main action — one `.primary-button`; every other action `.pr-ghost` — the reference
- [ ] external links — `EXTERNAL_LINK_PROPS`, and `leetcodeHref` for LeetCode — the reference
- [ ] load — `Promise.allSettled` per section, so one failure leaves the rest — the reference
- [ ] failure — `<p className="form-error" role="alert">` under the section head, the server's message — 3 of 3
- [ ] loading and empty — `.loading-state` with `Loader2.spin`; `.empty-state`, `.muted-copy` — the reference
- [ ] focus after an action — `act(call, refresh, focusId)` keeps focus where it was — the reference
- [ ] times — through `lib/practice-time.ts`; days through the server's IST date — the reference
- [ ] new positions (graph, ring, tiles, bars, line, spread) — the mockup's CSS with a `pr-` prefix, on theme tokens only, `color-mix(in srgb, var(--success) …)` for heat as the mockup has it; `role="grid"` and `aria-label` on the graph as `Calendar` has; both themes; scrolls inside its card at phone width — as designed
- [ ] not copied from the reference: `Calendar`'s fixed 14 px cells, the "days this month" total, the "Past builds" label and the word "build", the mockup's DSA/System design switch

Steps:
1. Re-read the checklist before writing — do not work from memory.
2. Implement. `cd frontend && npx tsc --noEmit && npm run lint`. Expected: clean.
3. Report the page ready for its check. The dispatcher sends it to the reviewer of this kind, with `ui-consistency:verifying` and this checklist in its brief; fix what it reports.
4. Report status `done` or `parked — <why>` here. Commit with `feat(practice): open on progress, with one problem and the contest ranking below`.

## Task 17: Retire the separate leaderboard [sonnet]

**Files:** Replace `frontend/app/practice/leaderboard/page.tsx` with a server redirect. Modify `frontend/components/AppShell.tsx`, `frontend/components/PrepTabs.tsx`, `frontend/app/practice/challenges/[slug]/page.tsx`, `frontend/app/onboarding/page.tsx`, and the user-visible "build" strings in `frontend/app/practice/**` and `services/practice/{judge,challenges,board}.py`, with tests that assert them.

**Interfaces:** `/practice/leaderboard` is `redirect('/practice#contest')` from `next/navigation`. `NAV` loses the Leaderboard item (and the unused `Trophy` import if nothing else uses it); Practice's blurb becomes `'One problem a day, a weekly contest'`. `PREP` keeps Practice and Mock interviews; `current` loses its leaderboard branch and its comment says two pages. The challenge page's `standing` link goes to `/practice#contest`; "See the leaderboard" is replaced by a `pr-section` holding `<ContestBoard slug={slug} />`.

Checklist — the challenge page against Prep pages, from `frontend/app/practice/challenges/[slug]/page.tsx`, read 2026-11-01; a snapshot, and the code wins:
- [ ] page holder and tabs unchanged — `<RequireAuth><AppShell>`, `<PrepTabs />`, now two tabs, a challenge still marking Practice
- [ ] ranking — `section.pr-section`, `.pr-section-head`, `h2` "Ranking", `ContestBoard` without tabs — the shared piece
- [ ] a board that has not opened is a 404 from the API — shows nothing rather than an error line on this page
- [ ] menu — the highlighted item for `/practice/design/*` and `/practice/challenges/*` is Practice — the longest-match rule in `AppShell`
- [ ] copy — "contest" on these pages; the attempt's status words stay
- [ ] not copied: the leaderboard page's heading or its Past builds select

Steps:
1. Re-read the checklist before writing — do not work from memory.
2. Implement. Run `grep -rn "practice/leaderboard" frontend --include=*.ts* --exclude-dir=node_modules` and confirm the only hits are `lib/api.ts`'s API path and the redirect's own route. `pytest tests/test_practice_board.py tests/test_practice_challenges.py tests/test_practice_judge.py -v`; `cd frontend && npx tsc --noEmit && npm run lint`. Expected: clean.
3. Report the page ready for its check. The dispatcher sends it to the reviewer of this kind, with `ui-consistency:verifying` and this checklist in its brief; fix what it reports.
4. Report status `done` or `parked — <why>` here. Commit with `feat(practice): one ranking under the contest, no separate leaderboard`.

## Task 18: The whiteboard page [sonnet]

**Files:** Create `frontend/app/practice/design/[slug]/page.tsx`, `frontend/components/practice/DesignBoard.tsx`. Modify `frontend/package.json`, `frontend/.gitignore`, `frontend/app/globals.css`, `frontend/THIRD_PARTY_NOTICES.md`.

**Interfaces:**
- Consumes: `api.practice.design`, `saveAnswer`, `feedback`.
- Produces: `DesignBoard` default export, loaded only by this route with `dynamic(() => import(...), { ssr: false })`: props `{ elements: unknown[] | null; readOnly: boolean; onChange: (elements: unknown[]) => void; onLoadFailed: () => void }`.
- Install exactly: `npm install --save-exact @excalidraw/excalidraw@0.18.1`.

**Board setup:**
- Import its CSS in `DesignBoard.tsx` only, and set `window.EXCALIDRAW_ASSET_PATH` before it loads, to assets copied into `public/excalidraw/` by a script beside `map-worker` and ignored in `frontend/.gitignore` as `public/map/vendor/` is. Confirm the exact source folder inside the installed package.
- `UIOptions`: no load, save, export, save-as-image, theme toggle or background change; `tools: { image: false }`. Hide the library trigger in scoped CSS. `validateEmbeddable={false}`; refuse a pasted image through `onPaste`.
- `theme` from `document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'`, followed with a `MutationObserver`, as nothing else re-renders on a theme change.
- `viewModeEnabled={readOnly}`.
- **`onChange` passes up only a real change**: compare a version sum of the elements against the value at hydration, because Excalidraw fires on mount. Filter `isDeleted` elements out before passing up.

**Page behaviour:**
- Autosave 5 s after the last change, flushed on `visibilitychange` to hidden and on unmount with `keepalive`. Status line in `.pr-saved`: `Saved HH:MM` or `Not saved. It will try again on your next change.`
- A 422 shows the server's sentence and keeps the work. A 409 turns the page read-only, says `This one closed at 06:00 IST`, and keeps what is on screen. A 429 waits for the next change.
- `Get feedback` saves first, then posts, then shows scores. Disabled while waiting. `feedback_problem === 'limit'`: `You have used today's feedback. Your drawing is saved and the day is counted.` `'busy'`: `Feedback is busy right now. Try again in a minute. Your drawing is saved and the day is counted.` A 409 while one is on its way keeps the waiting state and re-reads after 5 s, at most six times.
- `last_answer_day` shows `See your last answer` linking to `?day=`. A read-only answer says so; `elements === null` says `This drawing was cleared after 180 days.`
- `onLoadFailed`: the problem and reasoning stay, with a `.form-error` saying the whiteboard could not load.
- A line near the board: `Feedback reads your labels and arrows, not freehand drawing.`
- A 404 from the API renders `.empty-state` with a link back to `/practice`.

Checklist — the solve page against Prep pages, from `frontend/app/practice/challenges/[slug]/page.tsx` (a brief and a form on one page), read 2026-11-01; a snapshot, and the code wins:
- [ ] page holder — `<RequireAuth><AppShell>`, `<PrepTabs />` — 3 of 3 Practice pages
- [ ] heading — `section.page-heading`, `.board-kicker` "Practice · system design", `h1` with accent `<span>.</span>` — the reference
- [ ] problem text — `.pr-prose`, `.pr-label`, `.pr-list` — the reference
- [ ] two panes — `.pr-brief`-style grid, problem at left, board at right; one column under 800 px as `.pr-brief` does; the board's height set so its toolbar fits at phone width — the mockup for the widths
- [ ] reasoning — `.pr-field`, `label` with `small` counter, `textarea`, `data-short` past the limit — the reference
- [ ] submit — `.primary-button` "Get feedback", disabled while waiting, `{sending ? 'Reading it…' : 'Get feedback'}` — the reference's save button
- [ ] saved line — `.pr-saved` — the reference
- [ ] scores — `.results-head`, `.results-total`, `.results-dims`, then `.pr-notes` for each criterion — the reference's result; used in place of the mockup's `fb-row`, same content
- [ ] work on next — `.pr-label` plus `.pr-prose` paragraph — the reference
- [ ] failure — `<p className="form-error" role="alert">` — 3 of 3; success `.success-message role="status"`
- [ ] loading and missing — `.loading-state` with `Loader2.spin`; `.empty-state` — the reference
- [ ] times — through `lib/practice-time.ts` — the reference
- [ ] colours through tokens; both themes; the board itself follows `data-theme`
- [ ] not copied from the reference: start and clock state, proof code, appeal, signals, write-up fields

Steps:
1. Re-read the checklist before writing — do not work from memory.
2. Implement. `cd frontend && npm run build`. Expected: success. Compare the `/practice` and `/dashboard` first-load sizes in the build output with the pre-install numbers; neither grows by the whiteboard.
3. Add Excalidraw (MIT) to `THIRD_PARTY_NOTICES.md` in that file's format.
4. Report the page ready for its check. The dispatcher sends it to the reviewer of this kind, with `ui-consistency:verifying` and this checklist in its brief; fix what it reports.
5. Report status `done` or `parked — <why>` here. Commit with `feat(practice): draw and explain a system design problem`.

## Task 19: End-to-end tests and the real check [sonnet]

**Files:** Create `frontend/e2e/practice.spec.ts`.

Follow `e2e/events.spec.ts`: a real account from `./fixtures`, every `/api/v1/practice` call answered from fixed data, clock fixed.

- [ ] **Step 1: Write the tests**

```ts
test('someone with history lands on progress, then today, then the contest', ...)  // DOM order
test('someone new sees today above the graph', ...)
test('a DSA day opens LeetCode in a new tab and marks done', ...)
test('a design day links to its page', ...)
test('the ranking sits under the contest and switches tabs', ...)
test('podium hides under three rows', ...)
test('no leaderboard in the menu or the tabs, and the old URL lands on the contest', ...)
test('the board loading does not save over a drawing', ...)  // no PUT until a change
test('a real change saves after five seconds', ...)
test('feedback shows its scores', ...)
test('feedback unavailable keeps the drawing and says the day is counted', ...)
test('a 409 on save turns the page read-only and keeps the work', ...)
test('a blocked whiteboard chunk leaves the problem and a message', ...)
test('no request leaves for another host from the solve page', ...)  // page.on('request'), allow own origin and API only
test('no sideways page scroll at phone width, in both themes', ...)
```

- [ ] **Step 2: Run.** With the stack up (`make up`, `make migrate`), from `frontend/`: `npx playwright test e2e/practice.spec.ts e2e/practice-stats.rules.spec.ts`. Fix what fails in the code it points at. Expected: all PASS.
- [ ] **Step 3: Real check in the running app.** `preview_start` the dev server. Open today's design problem, draw three labelled boxes with two arrows, write a sentence, reload and confirm it is still there, press Get feedback and read real scores. Toggle the theme. Screenshot Practice and the solve page, both themes. If Groq has no key locally, say so plainly rather than claiming this passed.
- [ ] **Step 4: Commit** with `test(practice): the redesigned pages end to end`.

## Task 20: Docs [haiku]

**Files:** Modify `CLAUDE.md`, `services/practice/steps.py`, `api/routers/practice.py`, `domain/practice_pick.py`.

- [ ] **Step 1:** `CLAUDE.md` Practice section: the page order; one problem a day; Tue/Thu/Sat; the `practice_design*` and `design*` modules; `data/practice/system_design`; feedback (order, budgets, the per-minute setting, the judge key); replace "**The judge … is the only code that calls a model**" with the two callers; "Surfaces" and the frontend pages list; `/practice/leaderboard` is a redirect. In the storage bullet, extend the migration chain to `021_practice_design`.
- [ ] **Step 2:** The route list in the `api/routers/practice.py` docstring, the "only step that calls a model" sentence in `steps.py` (true of steps, say so), and "We never author problems" in `domain/practice_pick.py` (true of DSA, say so).
- [ ] **Step 3: Run the whole check.** `make check` and `make test`. Expected: clean.
- [ ] **Step 4: Commit** with `docs: Practice now opens on progress and includes system design`.

## Final review and closing

- [ ] **Whole-branch review** by a fresh Opus agent against the spec, with `ui-consistency:verifying` comparing `/practice` and the solve page as one family at the end.
- [ ] **Close the plan:** tell the user what remains theirs: building the LeetCode list (spec section 9), and checking the Groq limit.

## Decided

Nothing has been decided against what the order produced.
