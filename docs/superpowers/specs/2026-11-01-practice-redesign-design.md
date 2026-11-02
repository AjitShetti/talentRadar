# Practice redesign: progress first, one problem a day, system design on a whiteboard

Status: draft for approval, 2026-11-01. Approved mockup:
https://claude.ai/artifact/XVPeospJQAb2q6kBDBooFu (version 3).

This changes the Practice design in `docs/superpowers/specs/2026-10-15-practice-design.md`.
Anything not mentioned here stays as that document and `CLAUDE.md` describe it, including the
contest's judge, schedule, rules and appeal.

## 1. Intent and why

TalentRadar's goal is that a person opens one app each day and it already says what to apply
for, what to practise, who to connect with, and what replies came in. Practice is the "what to
practise" part, and today it falls short in four ways:

1. **The daily question never appears.** `data/practice/leetcode_free.json` has never existed
   (not on disk, not in git history), so `GET /practice/today` reports `catalogue: "empty"`.
2. **Practice reads as DSA only.** Real development work exists only as a 24 to 48 hour build,
   low on the page. There is no daily practice of design thinking.
3. **Progress is hidden.** The calendar sits below the fold and shows 12 weeks.
4. **The leaderboard feels like somewhere else.** It is its own page and nav item. The preview
   on `/practice` sits near the bottom and shows overall scores only.

The user pointed at LeetCode's profile page (activity graph, solved ring, rating, top percent)
and LeetCode's contest page (ranking directly below the contests) as the feel they want.

## 2. What the user decided

- **Page order:** progress first (activity graph, solved by difficulty and topic, where you
  stand). Scrolling down shows today's problem, then the weekly contest and its ranking.
- **One problem a day.** The person is given one problem and does not choose between two.
- **Three system design days and four DSA days a week, for everyone.** The experience-based mix
  I proposed was not taken. Which days are which is my call, see 4.1.
- **The weekly development contest stays as it is:** one a week, 24 or 48 hours, judged by the
  existing LLM judge.
- **Pressing today's problem goes to where it is solved:** LeetCode for DSA, a page in
  TalentRadar to draw and explain for system design.
- **Whiteboard: Excalidraw.** Checked on npm on 2026-11-01: `@excalidraw/excalidraw` 0.18.1 is
  MIT, with peer dependencies that accept React 18. `tldraw`, the main alternative, is licensed
  "SEE LICENSE IN LICENSE.md" rather than under a plain open-source licence, so the user's
  "use a free one" rules it out.
- **Feedback is immediate:** pressing the button returns a result in the same request, with no
  queue.
- The mockup was approved as shown, including its current streak (see 12).

## 3. Goals, non-goals, anti-goals

**Goals**

- A signed-in person with any history sees their progress on arrival and is one click from
  today's problem. A person with none sees today's problem first (4.7).
- Every day has a problem whenever at least one problem list is loaded.
- A system design problem can be drawn, explained and given feedback within one sitting.
- The ranking sits under the contest, and there is no separate Leaderboard page or nav item.
- The Overview's Practice card names the actual problem of the day, whichever kind it is.

**Non-goals**

- Reading replies from email. It is part of the product goal but is a separate project.
- Importing or syncing LeetCode history. robots.txt disallows the only machine-readable source,
  so the graph counts only what is done in TalentRadar. A person's 431 LeetCode solves do not
  appear.
- Verifying a DSA solve. "Done" stays self-reported.
- A second problem in a day, choosing the kind, or changing the mix per person.
- Ranking daily practice. Rank comes only from judged contests, because daily "done" is
  self-reported.
- Sharing drawings, a public profile, or reading the drawing as an image.
- Changing the contest's judge, schedule, briefs, rules or appeal.

**Anti-goals** (a failure even if it technically works)

- A daily decision is put in front of the person.
- A made-up, default or template score when the model fails or a limit is reached.
- A drawing lost at midnight or to a failed model call.
- Any request to leetcode.com from the server, CI or a fixture capture.
- A drawing sent anywhere except our API and, as text, the model.
- The rest of the app slowed by the whiteboard's size.

## 4. Behaviour

### 4.1 Which kind of problem a day has

A pure rule in `domain/practice_schedule.py`, on the IST day: **Tuesday, Thursday and Saturday
are system design, every other day is DSA.** My call: this spaces them evenly and puts one on
the weekend when there is more time.

- The kind is the same for everyone on a given date.
- Swap (still one a day) replaces the problem with another of the same kind.
- **A list being empty never leaves the day blank.** A DSA day with no DSA catalogue shows a
  system design problem. A design day with no design problems shows a DSA question. With neither,
  the existing "not loaded yet" state shows. This matters immediately, because the DSA catalogue
  depends on a manual step (section 9).

### 4.2 DSA day

As today: the pick from `domain/practice_pick.py`, "Solve on LeetCode ↗", "Mark done", Swap and
the one-tap rating. Design rows must not affect it: `_load` in `services/practice/daily.py` builds
the picker's `history` from `kind = 'dsa'` rows only.

### 4.3 System design day

The card shows title, topics, about-how-long and a reason line. "Draw and solve →" goes to
`/practice/design/[slug]`.

**Picking** (pure, new `domain/practice_design.py`, deterministic in `(user_id, day, swaps)` as
`pick_question` is):

- From problems whose `tracks` include the person's track or `general`.
- Weighted towards topics the person has not practised or practised longest ago, using the same
  neglect rule as DSA.
- Not done before, while any such problem is left. "Done" means a `practice_days` row with
  `kind = 'design'` and `done_at` set; a problem swapped away or abandoned can come back. Once all
  are done, the one done longest ago comes back, and the reason line says so ("You did this 14 weeks
  ago. See what you would change.").
- At three a week, 40 problems last about 13 weeks if a person sees all of them, so repeats are a
  normal case that needs a clear reason line.
- **A repeat starts on a blank board,** because redrawing from memory is the practice. The solve
  page links to "See your last answer", which opens the older one read-only. My call.

### 4.4 The problems

Checked-in JSON, `data/practice/system_design/NN-<slug>.json`, loaded and validated once per
process in the same way as `services/practice/challenges.py::load_briefs`.

Fields: `slug`, `title`, `tracks` (list of `Track` values), `minutes`, `topics` (list of slugs),
`summary`, `context` (paragraphs), `must_handle` (list), `show_in_answer` (list). A test validates
every file. Rendering matches mockup screen 2.

**About 40 problems at launch, written by us.** My call on the number. They must be our own
wording and use fictional companies, with a Bengaluru setting where it fits, like the contest
briefs. Nothing is copied from books, courses or other sites. The "do not author problems"
decision of 2026-10-14 was about DSA.

### 4.5 The solve page

Route `/practice/design/[slug]`, inside `RequireAuth` and `AppShell`. Left: the problem. Right:
Excalidraw, the reasoning box and "Get feedback". Below: the feedback once it exists.

**Who can open what.** The page opens for today's problem or any problem the person has an answer
for. Anything else is a 404, and another user's answer is a 404 too.

**Which answer a URL means.** A person can hold several answers to one slug, one per day.

- `GET /practice/design/{slug}?day=YYYY-MM-DD` returns the problem and that day's answer.
- Without `day` it returns the open answer if there is one, else the newest answer, read-only.
- `PUT` and `POST` take no day and only ever address the open answer. The open answer is today's
  when the slug is today's problem; else yesterday's when the slug was yesterday's problem and it is
  before 06:00 IST; else there is none and they answer 409. Today wins if a slug is on both days.
- Past answers are reached in two places only: "See your last answer" on a repeat, and "See your
  answer" on Today's card once the day is done. There is no list of them. My call.

**Saving.** The first save inserts the day's `practice_days` row, which pins the problem to that day
(picks are otherwise worked out on read, and could shift overnight). Autosave after 5 seconds without
a change, and on leaving the page, to `PUT /practice/design/{slug}/answer`. At most ten saves a minute,
429 above that, using the existing `PracticeRateLimited`. A failed save keeps the drawing on screen and
says so; the page retries on the next change.

**Swapping after drawing.** Swap stays available until the day is done, and warns when a drawing
exists: "Your drawing stays saved, but it can't be finished." A swapped-away answer is read-only, gets
no feedback (409) and never counts as done, so the one-a-day rule holds. My call.

**Limits, enforced by the server with a 422 in plain words:** drawing at most 200 KB and 500
elements, reasoning at most 4,000 characters. `files` is never stored, and image elements are
refused and turned off in the whiteboard's UI.

**Excalidraw setup.**

- Loaded with `next/dynamic` and `ssr: false`, with its CSS, only on this route.
- Turn off the image tool, the export and load actions, and anything that opens a third-party
  site or sends the drawing elsewhere. Render our own minimal menu.
- Theme follows `data-theme` on `<html>`, and changes when the viewer changes theme.
- Fonts and assets are served from our own `public/`, not a CDN.
- A short line near the board: "Feedback reads your labels and arrows, not freehand drawing."

**Past midnight.** An answer is credited to the day its problem belonged to, and stays editable
and submittable until 06:00 IST the next morning, so someone working at midnight neither loses
work nor loses the day. After that it is read-only. My call; the constant lives in
`domain/practice_schedule.py`.

### 4.6 Feedback

`POST /practice/design/{slug}/feedback`. This is the second place that calls a model for Practice,
and the first on a person's request. No read path calls a model.

Order, with no database transaction open during the model call:

1. Load the person's open answer (4.5). 404 if they have none for this slug, 409 if it is no longer
   open (past 06:00, or swapped away).
2. **Require something to read:** at least 3 labelled shapes and at least 40 characters of
   reasoning. Otherwise 422 saying which is missing, and the day is not marked done.
3. **Mark the day done** (`practice_days` with `kind = 'design'`), and commit. The practice happened
   whatever the model does next.
4. If a stored result has the same input hash, model and prompt version, return it with no call.
5. Take a per-answer cache lock, `tr:lock:design-feedback:<user>:<answer>`, as the daily pack does. A
   request that finds the lock taken answers 409 and the page keeps waiting and re-reads.
6. Claim budgets using `services/llm_budget.claim`, which fails open, the per-minute one first so a
   `busy` answer costs the person nothing from their three:
   - `DESIGN_FEEDBACK_PER_MINUTE = 1` across everyone, a setting so it can be raised without a deploy,
     using a one-minute key. See below.
   - `PER_USER_DAILY_DESIGN_FEEDBACK = 3` per IST day.
   - `DAILY_DESIGN_FEEDBACK_BUDGET = 150` per UTC day (`tr:budget:design-feedback:<day>`), a module
     constant like every other daily budget in the project.
7. Call `services.llm._chat`: `settings.groq_model`, temperature 0, JSON mode, reasoning effort
   medium, `max_tokens` 1,500. Input is held to 3,000 tokens by `estimate_tokens`, cutting the
   reasoning and then the scene description, so a call reserves at most 4,500.
8. Parse strictly, as `parse_verdict` does. Store the result with its hash, model and
   `DESIGN_PROMPT_VERSION`.

**Failure** (over a budget, model error, rate limit, unreadable reply, too-large request): store no
score and answer 200 with `feedback: null` and `feedback_problem` set to `limit` or `busy`. The page
keeps the drawing, says the day is counted, and offers to try again. There is no background retry.

**Not hurting the contest judge.** Feedback uses the judge's model, and the judge is built around 8,000
tokens a minute. A judge call that gets rate-limited backs off for hours, so a press of Get feedback
must not cause one. The real limit on the paid plan is on the Groq console and I could not read it, so
the defaults are safe at 8,000 and can be raised (section 9):

- At most one feedback call a minute across everyone. Over that, `busy`.
- While the `judge` step has attempts to call, it holds a cache key (`tr:practice:judging`, with a TTL
  so a crash cannot leave it set) and feedback answers `busy`. A run is at most about five minutes, every
  two hours, and only when something is queued.
- Nothing changes in the judge except setting that key.

**What the model reads** (pure, `describe_scene` in `domain/practice_design.py`). The drawing is
turned into text: each non-deleted rectangle, ellipse or diamond with its bound label; each arrow as
`A -> B` from its `startBinding` and `endBinding`, with its label; free-standing text as notes; and a
count of unconnected arrows and freehand strokes, which are not read. The model never sees an image.

**Treat all of it as hostile,** as the judge does. Reuse `flat_line`, `clean_block`, `fence`, the
nonce approach, `estimate_tokens` and `input_sha256` from `domain/practice_judge.py`, and make public
or move what is private there rather than copying it. Text aimed at the grader does not hold anything
back, because this score is private and never ranked; the system prompt treats it as evidence.

**Rubric:** six integers 0 to 10, equally weighted: requirements, components, data flow, scale,
failure handling, trade-offs. `total = round(sum / 60 * 100)`. Plus one note per criterion and one
`work_on_next` paragraph. Bump `DESIGN_PROMPT_VERSION` on any change to the prompt or input layout.

**Re-reading.** A person may edit and press again within the budget. The newest result replaces the
old one. The score feeds no board.

### 4.7 The activity graph, solved and topics

`GET /practice/history` grows; `weeks` allows up to 53 and the page asks for 53.

- `days[]` gains `kind` (`dsa` or `design`, null when not practised) and `contest` (true on the IST
  day of a saved contest submission).
- **Square colour:** easy lightest; medium and system design middle; hard and a contest entry darkest.
  Today is outlined. A contest entry colours its square but does not count as solved or extend a
  streak.
- `counts` gains `current_streak`: consecutive done days ending yesterday, plus today if done, so a
  streak is not shown as lost before today is over. `longest_run` is what the page labels "Longest
  streak", and both read all done rows.
- New `solved`: `{easy, medium, hard, design, total}` counts **distinct problems** among all done rows,
  as LeetCode does, with `available` `{easy, medium, hard, design}` from the loaded lists. A list that is
  not loaded gives `null`, and the tile shows the count alone. Counting rows would read "170 / 40" for a
  regular user.
- The graph's headline counts days, so it reads "N days practised in the past year" rather than the
  mockup's "N solved", and the two numbers do not appear to disagree.
- **A total must never fall.** `_prune_days` deletes only rows with `done_at` null; done rows are kept. They
  are small, and otherwise the ring would shrink after 400 days.
- `topics` counts all done rows, DSA by drawn topic. System design is one row on the page, as in the
  mockup, using `solved.design`. The "least practised" line comes from the same rows and is left out until
  a topic has been practised.

**Someone with nothing to show yet** (no done day and no contest entry) sees today's problem first and
the empty graph below it, because an empty year above the fold gives them nothing to do. After their first
done day, the page takes the order the user asked for. My call.

### 4.8 Where you stand

Rank comes only from the existing overall board (best four totals of the last twelve contests).

`GET /practice/leaderboard` gains `total_ranked`, `top_percent` and `distribution`, built from the same
cached entries so it adds no query:

- `top_percent` is `ceil(rank / total_ranked * 100)`, `null` without a rank or with fewer than 10
  people ranked, because "Top 50%" of two people means nothing.
- `distribution` is 20 bands of 20 points across 0 to 400, also `null` below 10.

The score line comes from `GET /practice/challenges` (`past[].total`, oldest first, judged only).

**Empty states.** No judged contest: "Enter a weekly contest to get a rank" with no chart. Under 10
ranked: rank and score line, without percent or bars.

### 4.9 Weekly contest and ranking

The section matches the mockup: this week's contest, then the ranking with a This week / Overall switch
and top-three podium, beside the person's contests.

- A shared `ContestBoard` component replaces `BoardPeek`. It reads the existing board endpoints. Fewer
  than 3 rows drops the podium. The viewer's row is always shown when it exists.
- Provisional and "no judged entries yet" states carry over from the current leaderboard page.
- Past contests list `overview.past` (last twelve), showing "Not entered" where there is no attempt.
- The contest page, `/practice/challenges/[slug]`, shows the same `ContestBoard` for its contest, so a
  past contest's ranking remains reachable.
- **Copy:** on Practice pages "build" becomes "contest". LeetCode's own contest stays as the small line
  in the mockup, called "LeetCode's own weekly contest". The `/practice/challenges` URL does not change.

**Removing the separate page.**

- `/practice/leaderboard` redirects to `/practice#contest`.
- Remove the entries in `AppShell.tsx` `NAV` and `PrepTabs.tsx`, and every link listed by
  `grep "practice/leaderboard" frontend`.
- Leaderboard settings stay in the collapsed block at the bottom of `/practice`.

### 4.10 The Overview

`services/copilot.py::_today_card` handles both kinds. A design day has the title "Today's system design:
<title>", detail built from `reason`, and its primary action going to `/practice/design/<slug>`. `meta` gains
`kind`. `buildActions` in `frontend/app/dashboard/page.tsx` and `BriefingFeed.tsx` label it "System design" rather
than "Daily question". `practice_today` stays off the model's allowlist.

**Today's problem now reaches everyone.** `_practice_suggestion_card` shows a card only to someone who
practised in the last 14 days, so a new person never sees one, which contradicts "it already has what to
practise". `_today_card` drops that condition, and the card can still be dismissed. The LeetCode contest
reminder keeps it, since a contest elsewhere is a nudge rather than today's plan. My call.

## 5. Data

Migration `021_practice_design` (revises `020_outreach_pack`), `IF NOT EXISTS` like `019`, with
`storage/models.py` to match so the drift test passes:

- `practice_days` gains `kind VARCHAR(8) NOT NULL DEFAULT 'dsa'`. Existing rows are DSA. A design row
  stores its problem slug in `question_slug` and its drawn topic in `topic`, and `difficulty` holds
  `medium` because the column is `NOT NULL`. That value is never read for a design row.
- New `practice_design_answers`: `id`, `user_id` (FK, cascade), `day`, `question_slug`, `scene` JSONB
  (elements only), `reasoning` TEXT, `submitted_at`, `scores` JSONB, `notes` JSONB, `total` SMALLINT,
  `input_hash`, `model`, `prompt_version`, `feedback_at`, `created_at`, `updated_at`. Unique on
  `(user_id, day, question_slug)`, so a swapped-away answer is kept.

**Retention**, in the existing Practice prune (`challenges` step): `scene` and `reasoning` are cleared after
180 days, which covers one cycle of repeats, and an answer row is deleted after 400 days. Done `practice_days`
rows are kept (4.7). My call, to respect the 0.5 GB database.

**API summary.** `GET /practice/today` gains `kind`, and `design` (`{slug, title, minutes, topics}`, null on a
DSA day). `question` stays for DSA, so existing readers keep working. New: `GET /practice/design/{slug}` (the
problem and an answer, 4.5), `PUT /practice/design/{slug}/answer`, `POST /practice/design/{slug}/feedback`.
The router stays thin. New service `services/practice/design.py`.

`mark_done` is refused with a 409 pointing to the solve page whenever the day's problem is a design problem,
including a DSA day showing one because the DSA list is empty, because done comes from asking for feedback
(4.6 step 3). `undo_done` works on both kinds.

## 6. Constraints

- **Cost:** free tiers except LLM fees, which the user approved on 2026-10-26. Excalidraw is free.
- **Size:** the whiteboard loads only on its own route. Check the `/practice` and `/dashboard` bundles
  do not grow by it.
- **LeetCode:** the server, CI and fixtures never request leetcode.com. Links are built from catalogue slugs.
- **Time:** Practice days are IST throughout, displayed via `frontend/lib/practice-time.ts`.
- **Style:** the tokens and controls in `frontend/app/globals.css`, both themes, no hard-coded overlay
  colours, and `rgb(var(--hi) / .08)` as `CLAUDE.md` requires. The heat colours in the mockup already follow this.
- **Phone width:** no sideways page scroll; the graph scrolls inside its card, opened at the newest week.
- **Accessibility:** keyboard-reachable controls and labelled charts, as the mockup's `aria-label`s show.

## 7. Approaches considered

| | Whiteboard | Feedback | Trade-off |
|---|---|---|---|
| **A, chosen by the user** | Excalidraw | Seconds, on the button | Keeps the person in the moment. New code, plus a daily limit on model calls |
| B | Excalidraw | Within about 2 hours, on the contest judge's schedule | Least new code and predictable cost, but they have moved on when it arrives |
| C | Our own boxes and arrows | Seconds | Lighter page, but no pen or freehand, and we would maintain a whiteboard |

## 8. Tests

Follow the existing Practice test files (`tests/test_practice_*.py`, SQLite with the migration drift test).

- **Pure:** `kind_for_day` across a week and across the IST midnight; `pick_design` determinism, no repeats
  until exhausted, the repeat reason; `describe_scene` on labelled shapes, bound and unbound arrows, deleted
  elements, freehand, hostile text; `current_streak` with and without today done; distinct-problem counts with a
  repeat; the fallback when a list is empty.
- **Services and API:** an answer's limits; saves rate-limited; another user's answer is a 404; 06:00 cut-off; which
  answer a URL means, including a repeat and a slug on two days; a swapped-away answer is read-only and uncredited;
  feedback's order (done is committed before the call; no score stored on model failure; same-input reuse makes no
  call; over-budget answers without calling; `busy` over the per-minute limit and during a judge run); `mark_done`
  refused when the day's problem is a design problem; DSA picker ignores design rows; done rows survive the prune.
- **Content:** every file in `data/practice/system_design` validates, slugs are unique, and every track has problems.
- **Frontend:** existing lint and build, a Playwright spec with mocked API for the page's states (no history,
  history, DSA day, design day, feedback, feedback unavailable), both themes, phone width. Confirm the
  redirect from `/practice/leaderboard`.
- **Real check before calling it done:** draw in Excalidraw in the running app, save, reload, get real feedback.

## 9. What only the user can do

Build the DSA list, because it needs a person's own browser. On `leetcode.com/problemset/`, scroll the whole
list, save each `graphql` response from DevTools → Network, then run:

```bash
python scripts/build_practice_catalogue.py saved-1.json saved-2.json --out data/practice/leetcode_free.json
```

Commit the result. Until then DSA days show system design problems (4.1).

Check the tokens-per-minute figure for `settings.groq_model` on the Groq console's Limits page. If it is well above
8,000, raise `DESIGN_FEEDBACK_PER_MINUTE` to match (figure ÷ 4,500, rounded down, leaving room for the judge). Until
then, a second person pressing Get feedback in the same minute is told to try again.

## 10. Docs to update with the code

`CLAUDE.md` Practice section (the judge is no longer the only model caller; the page order; the schedule; the migration
chain to `021`), the "No streak" paragraph in `services/practice/daily.py`, the nav blurbs in `AppShell.tsx` and the
onboarding sentence in `frontend/app/onboarding/page.tsx` that mention a leaderboard or a coding question, and
`frontend/THIRD_PARTY_NOTICES.md` for Excalidraw.

## 11. Left to the builder

File and component names beyond those named here, CSS class names under the `pr-` prefix, exact wording not quoted here,
the system prompt's text, the debounce implementation, and the order of the work. Run `ui-consistency:finding-patterns`
before the first task that changes what the user sees.

## 12. Calls I made, for the user to check

1. **System design on Tuesday, Thursday and Saturday.**
2. **A current streak is added, as in the approved mockup.** The 2026-10-15 design deliberately had none, on the grounds
   that "a current streak exists to be lost". The user's LeetCode reference shows one. If streaks should go, only that one
   number and the "keep your streak" sentence change.
3. **About 40 system design problems at launch,** which last about 13 weeks before repeats.
4. **Three feedback requests per person per day and 150 across everyone per day.**
5. **One feedback call a minute across everyone, none during a judge run,** until the Groq limit is checked (section 9).
6. **Answers stay open until 06:00 IST the next morning.**
7. **Drawings and reasoning cleared after 180 days;** answer scores kept to 400. Done days are kept for good, so
   totals never fall, which changes the old rule of pruning them at 400 days.
8. **An empty list falls back to the other kind** rather than a blank day.
9. **Percent and the spread chart stay hidden until 10 people are ranked.**
10. **A minimum to request feedback:** 3 labelled shapes and 40 characters of reasoning.
11. **DSA "done" stays self-reported, and system design "done" comes from pressing Get feedback.**
12. **A repeated problem starts blank,** with a link to the last answer. There is no list of past answers.
13. **Swapping after drawing keeps the drawing read-only and uncredited.**
14. **"Solved" counts distinct problems, and the graph's headline says "days practised".** This changes one word
    of the mockup.
15. **Someone with no history sees today's problem above the graph,** until their first done day.
16. **Today's problem now appears on the Overview for everyone,** not only those who practised in the last 14 days.
    This changes an existing rule, because of the goal the user stated.
