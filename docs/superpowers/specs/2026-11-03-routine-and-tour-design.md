# Daily routine on the day line, and a first-run tour

Date: 2026-11-03. Status: draft, for approval.

## Why

The day line under the nav shows the time, plus a mark for the rare dated
thing: an interview, a saved event, a contest. On most days it is an empty
line with a clock on it, which tells the user nothing they don't already know.
The product goal ("open TalentRadar and it already says what to apply for,
practise, who to connect with") wants the line to say what this hour is *for*.

New users also land in a product with eight sections and no map of them.
They need to be shown, once, where to find roles and apply, where to connect
with people, and where to prepare.

## What gets built

### 1. A routine the user sets

A routine is a short list of blocks. Each block has:

- a time of day in IST (`HH:MM`);
- a kind, which fixes where it leads (see below);
- a label, prefilled from the kind and editable (60 characters at most).

| kind           | default label                   | opens            |
|----------------|---------------------------------|------------------|
| `apply`        | Find roles and apply            | `/search`        |
| `connect`      | Reach out to people             | `/outreach`      |
| `applications` | Follow up on applications       | `/applications`  |
| `practice`     | Solve today's problem           | `/practice`      |
| `mock`         | Mock interview                  | `/interview`     |
| `events`       | Check events                    | `/events`        |
| `custom`       | (the user's own text, required) | nothing: a tick box, no Go link |

The link is derived from the kind on the client and is never stored or typed by
the user, so there are no user-entered URLs. A routine has at most 8 blocks, and
two blocks at the same minute aren't allowed (the user moves one).

The same routine repeats every day. There are no per-weekday routines (non-goal).

**A starting routine comes from the onboarding goal**, so nobody starts blank:

- *Land a job*: 09:00 apply, 09:45 connect, 18:00 applications.
- *Get interview-ready*: 19:00 mock, 22:00 practice.
- *Both*: 09:00 apply, 09:45 connect, 19:00 mock, 22:00 practice.

Onboarding saves it on submit. A user who finished onboarding before this ships
has no routine: the Overview offers the *Both* routine with "Use this" and
"Edit first".

### 2. Ticking a block off

Each block can be marked done for today. Ticks belong to the IST day, and on a
new IST day every block is open again. There is no history, no streak and no
"you skipped" (non-goal; it can come later on top of this).

### 3. Where it shows

- **The day line** draws every routine block as a mark at its time. A ticked
  block draws as done. A block that has passed without a tick looks the same as
  any past mark (no red, no nagging). A routine mark links to its page like any
  other mark. The routine is read once and shared for the session through the
  existing day-line store, so other pages make no extra request beyond one
  `GET /routine`. Briefing marks (interview, event, contest) stay as they are
  and draw above routine marks when they collide.
- **The Overview** gets a "Your day" section directly under "Up next". It lists
  the routine in time order: time, label, a "Go" link, and a tick box. The
  block that is current (the latest one whose time has passed and isn't
  ticked) is highlighted, and the hero's existing "next on the line" sentence
  ("Practice in 2h 10m") counts routine blocks too. Up next itself is unchanged.
- **Editing** happens inline (changed during the build from a dialog: the list turns
  into a form, as the interview time on `/applications` does), opened by an "Edit" control in the head of
  "Your day" (and by "Edit first" on the starter offer). In it you can add a
  block, remove one, change its time, kind or label, then Save. Block ids are
  made on the client when a block is added (`crypto.randomUUID()`, cut to 12
  characters) and kept through edits, so a block moved from 22:00 to 22:30
  keeps today's tick (*my call*). The same
  dialog is reachable from Settings ("Daily routine"), so it can be found
  without the Overview.

### 4. The first-run tour

A spotlight tour of five steps, shown once to a **new** user, on the first
signed-in page they reach after finishing onboarding. Onboarding lands people
on `/search`, `/practice` or `/dashboard` depending on their goal, so the tour
is mounted by `AppShell` and not by the Overview (*my call*). Users who
finished onboarding before this ships never see it on their own: the migration
stamps `tour_seen_at` for every profile with `onboarding_completed` true. The
user asked for the tour to be "only for the new users". If `GET /routine`
fails, the tour doesn't show (*my call*).

1. **Job hunt → Find roles**: search live openings and apply.
2. **Job hunt → Applications**: every role you applied to, from saved to offer.
3. **Job hunt → Outreach and Companies**: who to reach at each company, and when to follow up.
4. **Prep → Practice and Mock interviews**: one problem a day, a weekly contest, interviews scored answer by answer.
5. **The day line and Your day**: your routine. It says what each hour is for, and you tick it off.

Steps 1 to 4 cut out the nav group button ("Job hunt", "Prep"), not the items
inside its menu, which are hidden until opened; the card names the items
(*my call*: no reaching into `AppShell`'s menu state). On a page other than the
Overview, step 5 cuts out the day line and says "Your day" is on the Overview.

Each step dims the page, cuts out the nav item (or the line), and shows a small
card with one sentence plus Back / Next / Skip. Esc skips, and arrow keys move
between steps. Focus moves into the card. It works at phone width, because the
nav keeps its short labels there. With reduced motion there is no movement
between steps.

Finishing or skipping records that the tour was seen, on the account, so it
never shows again on any device. "Take the tour" in the avatar menu replays it.

## Approaches considered

- **A. Routine and ticks kept in the browser only.** No server change, but it
  doesn't follow the user between laptop and phone, and clearing site data loses it.
- **B. Kept on the account, in the profile (chosen).** One migration and two
  small endpoints. It is the same on every device, and ticks reset by IST day
  with no history kept.
- **C. A calendar with a block table and a row per day per tick.** This gives
  history and streaks the user didn't ask for, and can be layered on B later.

For the tour: built in-house (chosen), matching both themes with no dependency,
over a tour library (quicker to wire up, but brings in its own look to restyle).

The user picked B and the in-house tour.

## Server

- **Migration `023_profile_routine`** (after `022_application_interview_at`)
  adds to `profiles`:
  - and stamps `tour_seen_at = now()` on rows with `onboarding_completed` true;
  - `routine` JSONB, nullable: `{"blocks": [{"id", "at", "kind", "label"}], "done": {"day": "YYYY-MM-DD", "ids": [...]}}`;
  - `tour_seen_at` timestamptz, nullable.

  *My call:* the tour flag is its own column rather than a key inside `routine`,
  because it has nothing to do with the routine. It is still one migration.
- **Pure rules in `domain/routine.py`**: the kinds, the caps (8 blocks, label
  60), validating a block list (time format, kind, unique ids, no two at one
  minute, a custom block needs a label), the IST day, and "ticks for today",
  where a stored `done.day` that isn't today reads as no ticks.
- **`services/routine.py`** reads and writes the profile row (creating it if
  absent, like `/profile/ensure`). **`api/routers/routine.py`** under `/api/v1`,
  signed-in only, thin:
  - `GET /routine` → `{has_routine, blocks, done_today: [ids], day, tour_seen, onboarding_completed}`.
    `blocks` is `[]` when no routine is set, and `has_routine` says whether one
    was ever saved (a saved empty routine is a choice, and the starter offer
    isn't shown again).
  - `PUT /routine` `{blocks}` → validates, replaces the blocks, keeps today's
    ticks only for ids that still exist. 422 on invalid input.
  - `PUT /routine/blocks/{id}/done` `{done: bool}` → sets or clears the tick
    for today's IST day. 404 for an id not in the routine.
  - `POST /routine/tour-seen` → stamps `tour_seen_at` (idempotent).
- No LLM call, no outside request, nothing reaches the copilot's model. The
  briefing is untouched.
- Tests: domain rules, and an API test covering validation, the tick reset
  across an IST midnight, another user's data never visible, and tour-seen.

## Frontend

- `lib/routine.ts`: the kinds table, the goal-to-starter routine, current/next
  block, and turning blocks into day-line marks. Pure, tested in
  `e2e/routine.rules.spec.ts` like `day-line.rules.spec.ts`.
- `lib/api.ts`: `api.routine.get/save/tick/tourSeen`.
- `lib/day-line.ts` / `DayLine.tsx`: the snapshot carries routine marks next to
  briefing marks. A mark gets a `done` flag and a `routine` flag for styling.
  The day line fetches the routine with the same freshness rule as the briefing.
- `components/overview/YourDay.tsx`, `components/RoutineEditor.tsx` (the
  dialog, using `lib/use-dialog.ts`), `components/Tour.tsx`, mounted by the
  Overview. Nav items get `data-tour` attributes to be found.
- Onboarding submit saves the starter routine for the chosen goal after the
  profile save. A failure there doesn't block onboarding.
- The look follows the house rules: no icon library, words for controls, azure
  for what is current, no tinted surfaces, both themes.

## Non-goals and anti-goals

- No push notifications, emails or sounds at a block's time. The line and the
  Overview are the reminder. (Browser notifications would need permission
  prompts and a service worker; that is a later decision.)
- No history, streaks or "missed" scolding.
- No per-weekday routines and no free-form links.
- The tour isn't shown to anyone who hasn't finished onboarding, and never
  twice unless asked for.
- Failure: if `GET /routine` fails, the line and the Overview behave exactly as
  today, with no error banner on the line.

## Left to the builder

The exact styling of a routine mark versus a briefing mark, the tour card's
placement maths, the dialog's field layout, and test file splits.

## Changes made during the build

- The editor is inline, not a dialog, following the project's own in-place edit pattern.
- `GET /routine` also answers `onboarding_completed`, which gates the tour.
- On a phone, the day line's list shows only routine blocks still ahead and not ticked; "Your day" has all of them.
