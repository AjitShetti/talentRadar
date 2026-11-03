# Daily routine and first-run tour — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each user sets a daily routine of timed blocks that show on the day line and on the Overview, where they can tick them off for today. New users get a one-time tour of where to apply, connect and prepare.

**Architecture:** The routine and the "tour seen" stamp live on `profiles` (migration 023). Pure rules go in `domain/routine.py`, I/O in `services/routine.py`, and a thin router sits at `/api/v1/routine`. On the client, pure rules go in `lib/routine.ts`. A second small store in `lib/day-line.ts` holds the routine beside the briefing, and `useDay()` merges routine marks into the line's marks. The Overview gets a "Your day" section with an inline editor, which Settings reuses. `components/Tour.tsx` is mounted by `AppShell`.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, pytest (SQLite in memory); Next.js 14, React, Playwright (rule specs run without a browser).

**Spec:** `docs/superpowers/specs/2026-11-03-routine-and-tour-design.md`

## Global Constraints

- Kinds and links (fixed, never typed by the user): `apply` → `/search`, `connect` → `/outreach`, `applications` → `/applications`, `practice` → `/practice`, `mock` → `/interview`, `events` → `/events`, `custom` → no link.
- Default labels: "Find roles and apply", "Reach out to people", "Follow up on applications", "Solve today's problem", "Mock interview", "Check events". `custom` has none and requires the user's text.
- At most 8 blocks, a label of at most 60 characters, no two blocks at the same minute, times `HH:MM` in IST.
- Starter routines: *apply*: 09:00 apply, 09:45 connect, 18:00 applications. *prepare*: 19:00 mock, 22:00 practice. *both*: 09:00 apply, 09:45 connect, 19:00 mock, 22:00 practice.
- Ticks belong to the IST day (fixed +05:30). No history and no "missed" wording.
- No LLM call, no outside request, nothing added to the copilot's model input, and the briefing is untouched.
- If `GET /routine` fails, the line and the Overview behave as today and no tour shows.
- UI: no icon library, controls named in words, azure (`--accent`) for what is current, no tinted surfaces, both themes. A dimmed backdrop uses `rgb(0 0 0 / calc(.42 * var(--shade)))` (as `.cm-sheet-backdrop`).
- mypy strict; ruff isort first-party grouping.

## Review Focus

1. **A page left open across IST midnight.** Yesterday's ticks must not show as today's. The store keys the routine by IST day, the server's `done.day` check backs it up, and test T3 covers it.
2. **Editing keeps ticks, and removing drops them.** A block moved from 22:00 to 22:30 keeps today's tick, and a removed block's tick is gone. Tested in T2.
3. **Two blocks at one minute, or a custom block with a blank label.** The client says why before saving, and the server answers 422 with the same reason. Tested in T1 and T3.
4. **`GET /routine` fails or is slow.** No "Your day" section, no tour, no error on the line. Checked in T5 and T6 by mocking a failing request in the browser check.
5. **Someone who has not finished onboarding,** or is on `/onboarding`, never sees the tour. A user who finished onboarding before this ships never sees it unasked. Tested in T2 (migration backfill) and T6 (rule).

---

### Task 1: Rules and schema (server)

**Files:**
- Create: `domain/routine.py`, `storage/migrations/versions/023_profile_routine.py`, `tests/test_routine.py`
- Modify: `storage/models.py` (`Profile`), `tests/test_interview_time.py` (`test_022_is_the_only_head` → expect `023_profile_routine`; rename it `test_the_migrations_have_one_head`)

**Interfaces — Produces:**
- `KINDS: tuple[str, ...]`, `DEFAULT_LABELS: dict[str, str]`, `MAX_BLOCKS = 8`, `LABEL_MAX = 60`
- `@dataclass(frozen=True) class Block: id: str; at: str; kind: str; label: str` with `def as_dict(self) -> dict[str, str]`
- `class RoutineInvalid(ValueError)`: its message is the sentence shown to the user.
- `def validate_blocks(raw: list[dict[str, object]]) -> list[Block]`: strips the label and collapses its whitespace; an empty label on a non-custom kind takes the default; the result is sorted by `at`. The id matches `^[A-Za-z0-9_-]{1,36}$` and is unique.
- `def ist_day(now: datetime) -> date`
- `def ticks_today(stored: Mapping[str, object] | None, today: date) -> list[str]`
- `def with_tick(stored: Mapping[str, object] | None, block_id: str, done: bool, today: date) -> dict[str, object]`: returns the new `routine` JSON, never mutating its input.
- `def with_blocks(stored: Mapping[str, object] | None, blocks: list[Block], today: date) -> dict[str, object]`: replaces the blocks and keeps today's ticks only for ids still present.
- Model: `Profile.routine: Mapped[dict | None]` (JSONB) and `Profile.tour_seen_at: Mapped[datetime | None]` (`DateTime(timezone=True)`).

- [ ] **Step 1: Write the failing tests** in `tests/test_routine.py` (rules section):
  - `test_ist_day_turns_at_ist_midnight`: `ist_day(datetime(2026,11,3,18,29,tzinfo=UTC)) == date(2026,11,3)`; at 18:30 UTC it is `date(2026,11,4)`.
  - `test_validate_sorts_and_fills_default_labels`: blocks given at 22:00 then 09:00 come back 09:00 first, and an `apply` block with `label=" "` gets "Find roles and apply".
  - `test_validate_refuses`: parametrised, each raising `RoutineInvalid`: 9 blocks; `at="9:00"`; `at="24:00"`; kind `"sleep"`; a custom block with a blank label; a label of 61 characters; two blocks at `"09:00"`; a duplicate id; id `"a b"`.
  - `test_ticks_from_another_day_are_none`: `ticks_today({"done": {"day": "2026-11-02", "ids": ["a"]}}, date(2026,11,3)) == []`.
  - `test_with_tick_sets_and_clears_without_mutating`: setting `"a"` and then clearing it, while the original dict is unchanged.
  - `test_with_blocks_keeps_ticks_of_ids_still_present`: ticks `["a","b"]`; new blocks have ids `a` (time moved) and `c`; the result has ticks `["a"]`.
  - `test_023_follows_022_and_backfills_tour_for_the_onboarded`: load the module as `test_022_follows_021…` does. Assert `down_revision == "022_application_interview_at"` and that the text contains `ADD COLUMN IF NOT EXISTS routine JSONB`, `ADD COLUMN IF NOT EXISTS tour_seen_at TIMESTAMPTZ`, `SET tour_seen_at = now()`, `onboarding_completed IS TRUE`, and both `DROP COLUMN IF EXISTS` lines.
- [ ] **Step 2: Run** `pytest tests/test_routine.py tests/test_interview_time.py -v`. Expected: the new tests FAIL on import; the head test FAILs once 023 exists.
- [ ] **Step 3: Implement** `domain/routine.py`, the migration (raw `op.execute` with IF [NOT] EXISTS, in the 022 style, plus a docstring on why the backfill exists: the tour is for new users only), and the two model columns.
- [ ] **Step 4: Run** `pytest tests/test_routine.py tests/test_interview_time.py -v && ruff check domain storage tests && mypy domain/routine.py`. Expected: PASS and clean.
- [ ] **Step 5: Commit** `feat(routine): the rules and the profile columns for a daily routine`

### Task 2: Service and endpoints

**Files:**
- Create: `services/routine.py`, `api/routers/routine.py`
- Modify: `api/main.py` (include `routine.router` with prefix `/api/v1`), `tests/test_routine.py` (API section)

**Interfaces:**
- Consumes: everything Task 1 produces; `CurrentUserId` from `api.routers.profile`; `parse_uuid` from `services.base`; `AsyncSessionLocal` (imported at module level so tests can monkeypatch it, as `services/applications.py` does).
- Produces (each takes `now: datetime | None = None` for tests):
  - `async def read_routine(*, user_id: str, now: datetime | None = None) -> dict[str, Any]` → `{"has_routine": bool, "blocks": [Block.as_dict()], "done_today": [str], "day": "YYYY-MM-DD", "tour_seen": bool, "onboarding_completed": bool}`. A missing profile gives the empty shape and creates no row. (*Builder's addition to the spec:* `onboarding_completed` gates the tour.)
  - `async def save_routine(*, user_id: str, blocks: list[dict[str, object]], now=None) -> dict[str, Any]` creates the profile row if absent and returns the `read_routine` shape.
  - `async def set_tick(*, user_id: str, block_id: str, done: bool, now=None) -> dict[str, Any]` raises `RoutineBlockNotFound(LookupError)` for an id not in the routine.
  - `async def mark_tour_seen(*, user_id: str, now=None) -> dict[str, Any]` keeps the first stamp (idempotent) and creates the row if absent.
  - Routes: `GET /routine`; `PUT /routine` with body `{"blocks": [...]}` (`RoutineInvalid` → 422 `{"detail": <sentence>}`); `PUT /routine/blocks/{block_id}/done` with body `{"done": bool}` (unknown id → 404); `POST /routine/tour-seen`. Each answers the `read_routine` shape.
  - The `routine` dict is always reassigned and never mutated in place, so SQLAlchemy sees the change.

- [ ] **Step 1: Write the failing API tests.** Use the `client`/`auth` fixtures as in `tests/test_interview_time.py`, with `outreach_db`, and monkeypatch `services.routine.AsyncSessionLocal` and `services.routine._now`. `NOW = datetime(2026,11,3,6,0,tzinfo=UTC)` (11:30 IST).
  - `test_get_with_no_profile_is_empty`: `has_routine False`, `blocks []`, `tour_seen False`, `onboarding_completed False`, `day "2026-11-03"`.
  - `test_put_then_get_round_trips_sorted`
  - `test_put_invalid_is_422_with_the_reason`: two blocks at 09:00 give 422, with a `detail` that mentions the time.
  - `test_saved_empty_routine_has_routine_true`
  - `test_tick_then_untick`, and `test_tick_unknown_block_is_404`
  - `test_ticks_reset_at_ist_midnight`: tick at NOW; with `_now` at `2026-11-03T18:31Z`, `done_today == []`.
  - `test_moving_a_block_keeps_its_tick_and_removing_drops_it`
  - `test_another_users_routine_is_never_read`: user B's GET is empty after A saves, and B ticking A's block id gets 404.
  - `test_tour_seen_is_idempotent`: the stamp is unchanged on the second POST.
  - `test_unauthenticated_is_401`
- [ ] **Step 2: Run** `pytest tests/test_routine.py -v`. Expected: the API tests FAIL with 404 (no route).
- [ ] **Step 3: Implement** the service and the router. The router does validation and delegation only, with Pydantic bodies inline as in `api/routers/profile.py`. Logs carry ids and counts only.
- [ ] **Step 4: Run** `pytest tests/test_routine.py -v && make check`. Expected: PASS and clean.
- [ ] **Step 5: Commit** `feat(routine): read, save and tick the routine, and stamp the tour`

### Task 3: Client rules, API client and store

**Files:**
- Create: `frontend/lib/routine.ts`, `frontend/e2e/routine.rules.spec.ts`
- Modify: `frontend/lib/api.ts` (`api.routine`), `frontend/lib/day-line.ts` (`DayMark` flags plus the routine store)

**Interfaces — Produces:**
- `lib/routine.ts`:
  - `type RoutineKind`, `type RoutineBlock = { id; at; kind; label }`
  - `type Routine = { has_routine: boolean; blocks: RoutineBlock[]; done_today: string[]; day: string; tour_seen: boolean; onboarding_completed: boolean }`
  - `KINDS: Record<RoutineKind, { name: string; label: string; href: string | null }>`, where `name` is the option text in the editor ("Apply", "Connect", "Applications", "Practice", "Mock interview", "Events", "Your own")
  - `MAX_BLOCKS`, `LABEL_MAX`
  - `starterRoutine(goal: 'apply' | 'prepare' | 'both', makeId = newBlockId): RoutineBlock[]`
  - `newBlockId(): string` (`crypto.randomUUID().replace(/-/g, '').slice(0, 12)`)
  - `blockMinutes(at: string): number`
  - `routineProblem(blocks): string | null`: the same rules and sentences as the server
  - `currentBlock(blocks, done, nowMinute): RoutineBlock | null`: the latest block at or before now that isn't ticked
  - `routineMarks(blocks, done): DayMark[]`: id `routine:<id>`, `href` is the kind's href or `/dashboard`, with `routine: true`, `done`
  - `istDayKey(ms): string` (`YYYY-MM-DD` in IST)
- `lib/api.ts`: `api.routine.get()`, `.save(blocks)`, `.tick(id, done)`, `.tourSeen()`. Each returns `Routine`.
- `lib/day-line.ts`:
  - `DayMark` gains optional `routine?: true; done?: boolean`.
  - A separate routine snapshot `{ owner, at, routine }` with `publishRoutine(routine, owner)`, `routineFor(owner, nowMs): Routine | null` (null for another owner, or when `routine.day !== istDayKey(nowMs)`), `routineIsFresh(owner, nowMs)` (5 minutes, as for the briefing), `onRoutine(listener)` and `routineSnapshot()`.
  - `tickLocally(id, done)` for optimistic ticks.
  - `mergeMarks(briefing: DayMark[], routine: DayMark[]): DayMark[]` sorted by `at`, with a briefing mark first at an equal minute.
  - `clearDay()` and `resetArrival()` also clear the routine snapshot. The existing briefing snapshot and its tests are unchanged.

- [ ] **Step 1: Write the failing tests** in `e2e/routine.rules.spec.ts`, in the style of `day-line.rules.spec.ts`:
  - each starter routine's times and kinds, exactly as in Global Constraints;
  - `routineProblem` sentences for a duplicate minute, a blank custom label and 9 blocks, and `null` for a valid list;
  - `currentBlock` skips ticked blocks, and returns null before the first block;
  - `routineMarks` ids, hrefs (custom → `/dashboard`) and `done`;
  - `mergeMarks` order at an equal minute;
  - `routineFor` returns null for another owner, and null for a routine whose `day` is yesterday in IST at `NOW`;
  - `clearDay` empties both snapshots.
- [ ] **Step 2: Run** `cd frontend && npx playwright test e2e/routine.rules.spec.ts e2e/day-line.rules.spec.ts`. Expected: routine FAIL, day-line PASS.
- [ ] **Step 3: Implement** it.
- [ ] **Step 4: Run** the same command and `npx tsc --noEmit && npm run lint`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(routine): client rules, API client and the routine beside the briefing`

### Task 4: Routine on the day line

**Files:** Modify `frontend/components/DayLine.tsx`, `frontend/app/globals.css` (the `.day-mark` block near line 2463)

**Interfaces:** Consumes Task 3. Produces `useRoutine(): Routine | null` (exported from `DayLine.tsx` beside `useDay`). `useDay()` now returns `{ ...day, marks: mergeMarks(day.marks, routineMarks(...)) }`, so the Overview's `NextOnTheLine` and `markForAction` count routine blocks with no change of their own.

- [ ] **Step 1:** In `DayLine`, beside the briefing effect, fetch `api.routine.get()` when signed in and `!routineIsFresh(owner)`, then `publishRoutine`. On failure, publish nothing. The day-change re-read applies the same way.
- [ ] **Step 2:** On `MarkOnLine` and in `.day-list`, set `data-routine` and `data-done`. CSS:
  - a routine mark is `var(--ink)` while ahead, and `var(--faint)` once past, as now;
  - a done mark's stem is `var(--success)` and its label is struck through;
  - a briefing mark keeps `var(--warm-ink)` and its stacking order sits above a routine mark at the same position;
  - the aria-label gets ", done" when ticked.
- [ ] **Step 3: Verify** with the browser check in Task 7 (this task has no rule-level behaviour of its own beyond Task 3).
- [ ] **Step 4: Commit** `feat(day-line): the routine's blocks are on the line`

### Task 5: "Your day" on the Overview, the editor, Settings and onboarding

**Files:**
- Create: `frontend/components/overview/YourDay.tsx`, `frontend/components/RoutineEditor.tsx`
- Modify: `frontend/app/dashboard/page.tsx` (mount `<YourDay />` directly after the Up next section), `frontend/app/settings/page.tsx` (a "Daily routine" card), `frontend/app/onboarding/page.tsx` (seed the routine), `frontend/app/globals.css` (`.yd-*` rules beside `.ov2-*`)

**Interfaces:**
- Consumes `useRoutine`, `publishRoutine`, `tickLocally`, `api.routine`, `starterRoutine`, `currentBlock`, `routineProblem`, `KINDS`.
- Produces `RoutineEditor({ initial: RoutineBlock[]; onSaved: (r: Routine) => void; onCancel?: () => void; saveLabel?: string })`.

*Decision against the spec:* the editor is **inline**, as the interview time on `/applications` is edited (the row turns into a form), not a dialog. That's the project's own pattern for editing a few fields in place. The spec is to be updated to match.

**Checklist:** YourDay and RoutineEditor against an *Overview section* (`ov2-section` on `app/dashboard/page.tsx`: Up next and Your journey) and an *inline edit form* (`application-when` on `app/applications/page.tsx`). Read 2026-11-03.
- [ ] section holder: `<section className="ov2-section" aria-labelledby="ov2-day">`, with `ov2-head` holding an `h2` "Your day" and, on the right, a `text-button` "Edit". This is the Overview's own section pattern: 2 of 2 sections in the file (`ov2-section`, `ov2-head`).
- [ ] rows: an `ol` styled like `.ov2-next` with hairlines (`--line`). Each `li` holds:
  - a `label.toggle-row` with a checkbox (the checkbox pattern used in 2 of 2 files: settings and practice), the time in tabular numerals, and the block's label;
  - a `Link` "Go" as `text-button` when the kind has a link.

  The current block gets `data-current` and is coloured `--accent`. A ticked row's label is `--faint` and struck through.
- [ ] hovering or focusing a row lights its mark (`setDayLit('routine:<id>')`), as the Up next rows do (1 of 1).
- [ ] tick: optimistic `tickLocally`, then `api.routine.tick`; on failure revert and show `p.form-error role="alert"` "Could not save that tick." `form-error` is the shared error piece (used in all forms read).
- [ ] no routine (`has_routine` false): `p.ov2-quiet` "Give each part of the day a job." Below it, a preview of `starterRoutine('both')` as plain rows, then `primary-button` "Use this" (saves it) and `outline-button` "Edit first" (opens the editor with it).
- [ ] routine not loaded or failed: the section is not rendered.
- [ ] editor (inline form, `application-when` mechanism):
  - each block row has an `input type="time"` with `step={300}`, a `select` of `KINDS` names, a label `input` with `maxLength={LABEL_MAX}` and the kind's default as placeholder, and a `text-button` "Remove";
  - under the rows, an `outline-button` "Add a block", disabled at `MAX_BLOCKS`, which adds a block at the next free half hour after the last;
  - then a `primary-button` "Save routine" (shows "Saving…" while busy), a `text-button` "Cancel", and `form-error role="alert"` showing the `routineProblem` sentence or the server's 422 detail.
- [ ] type and colour through tokens only (`--ink`, `--faint`, `--line`, `--accent`, `--success`, `--font-text`). Spacing reuses `.ov2-next` rhythms, with no new literals beyond those `.ov2-*` already uses.
- [ ] not copied from Up next: its numbered `FlapText` column, its large `h3` titles and its arrival animation delays. Your day is a quiet list.

Settings: a `section.card-form` after the profile form with an `h2` "Daily routine", a `muted-copy` line "What each part of your day is for. It shows on the line under the menu.", and `RoutineEditor` without `onCancel`. On save, show `success-message` "Saved." and `publishRoutine`. It loads with `api.routine.get()`.

Onboarding: after `api.profile.save` succeeds, `await api.routine.save(starterRoutine(goal || 'both')).catch(() => {})`, before `router.push`.

- [ ] **Step 1:** Build `RoutineEditor` and `YourDay`, and mount it.
- [ ] **Step 2:** Settings card and onboarding seed.
- [ ] **Step 3: Run** `npx tsc --noEmit && npm run lint`. Expected: clean.
- [ ] **Step 4: Commit** `feat(overview): your day, ticked off, and the routine editor`

### Task 6: The first-run tour

**Files:**
- Create: `frontend/components/Tour.tsx`, `frontend/lib/tour.ts`, and tests appended to `frontend/e2e/routine.rules.spec.ts`
- Modify: `frontend/components/AppShell.tsx` (mount `<Tour />` when signed in; `data-tour="jobs"` on the Job hunt nav button, `data-tour="prep"` on Prep, `data-tour="day"` on the day line `section`; an avatar-menu item "Take the tour" after "Resume studio" that dispatches `TOUR_EVENT`), `frontend/app/globals.css` (`.tour-*`)

**Interfaces — Produces** (`lib/tour.ts`):
- `TOUR_EVENT = 'tr:tour'`
- `TOUR_STEPS: { target: 'jobs' | 'prep' | 'day'; title: string; body: string }[]` (five; copy below)
- `shouldAutoStart(routine: Routine | null, path: string): boolean`: true only when the routine loaded, `onboarding_completed`, `!tour_seen`, and the path isn't `/onboarding`, `/login` or `/signup`.

Copy (new, for the user to read):
1. "Find roles": "Search live openings across the boards, then apply from the role."
2. "Applications": "Every role you go for sits here, from saved to offer. Under Job hunt."
3. "Outreach and Companies": "Who to reach at each company, and when to follow up. Also under Job hunt."
4. "Practice and mock interviews": "One problem a day, a weekly contest, and interviews scored answer by answer."
5. "Your day": "This line is your routine: what each hour is for. Tick blocks off under Your day on the Overview, and change the times whenever you like."

**Checklist:** the tour against the project's modal dialogs (`cm-sheet` on company-intel and events: 2 of 2 use `useDialog` with `modal: true, closeOnEscape: true`, `role="dialog" aria-modal="true" aria-labelledby`).
- [ ] the backdrop is four fixed panels, or one cut-out element with a spread `box-shadow`, dimming everything but the target's rect plus 6px. The dim value is the `.cm-sheet-backdrop` one, and a click on the dim does nothing.
- [ ] the card is a `div role="dialog" aria-modal="true" aria-labelledby="tour-title"` using `useDialog(ref, skip, { modal: true, closeOnEscape: true })`. It holds an `h2#tour-title`, a body `p`, a faint "2 of 5", a `text-button` "Skip", a `text-button` "Back" (absent on step 1), and a `primary-button` "Next" ("Done" on the last step, with `data-autofocus`).
- [ ] ArrowRight and ArrowLeft move between steps. The card sits below the target and is clamped into the viewport with a 16px gutter. Below 560px it is pinned to the bottom of the viewport. The position is re-measured on resize and scroll.
- [ ] if the target is missing (a bleed page has no day line), the card is centred with no cut-out.
- [ ] under reduced motion the cut-out jumps between steps; otherwise it moves with `transition` on `--ease-day`.
- [ ] Done or Skip: close, call `api.routine.tourSeen()` (errors ignored), and update the store's `tour_seen`. A replay from the menu always opens at step 1.

- [ ] **Step 1: Write failing rule tests** for `shouldAutoStart`: true for an onboarded user who hasn't seen the tour on `/search`; false on `/onboarding`, when not onboarded, when already seen, and for a `null` routine.
- [ ] **Step 2: Run** `npx playwright test e2e/routine.rules.spec.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** `lib/tour.ts`, `Tour.tsx` and the AppShell changes.
- [ ] **Step 4: Run** the rules plus `npx tsc --noEmit && npm run lint`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(tour): a first-run tour of where to apply, connect and prepare`

### Task 7: Docs and browser check

**Files:** Modify `CLAUDE.md` (migrations list: `023_profile_routine` after `022`; a short "Routine and tour" paragraph under the day line convention) and the spec (the inline editor decision, and `onboarding_completed` in `GET /routine`).

- [ ] **Step 1:** Update the docs.
- [ ] **Step 2: Browser check** with the stack up and the migration applied:
  - sign up, finish onboarding with "Both", and see the tour on the landing page, all five steps, at desktop width and at 375px;
  - on the Overview, "Your day" shows the four starter blocks and the line shows them;
  - tick one, and its mark shows done;
  - edit the 22:00 block to 22:30, and its tick stays;
  - two blocks at one time show the error;
  - reload: the tour doesn't come back, and "Take the tour" replays it;
  - with `/api/v1/routine` blocked in the browser, the Overview and the line render as before;
  - check both themes.
- [ ] **Step 3: Run** `make test` and the frontend rules. Expected: PASS.
- [ ] **Step 4: Commit** `docs: the routine and the tour`
