---
name: TalentRadar
description: AI-powered career intelligence for the Indian tech job market — read as a ruled operations sheet, not a dashboard of stat cards.
colors:
  ink: "#ECEEF0"
  ink-2: "#C7CBD1"
  ink-3: "#14161A"
  ink-4: "#1C1F24"
  ink-5: "#2A2E35"
  paper: "#131211"
  surface: "#1B1917"
  surface-2: "#211F1C"
  surface-3: "#2B2825"
  ghost: "#3A3631"
  white: "#FFFFFF"
  line: "rgba(255,255,255,.10)"
  line-soft: "rgba(255,255,255,.055)"
  line-strong: "rgba(255,255,255,.18)"
  muted: "#9AA0A6"
  faint: "#868C94"
  accent: "#FFB020"
  accent-dark: "#E69A0E"
  accent-light: "#FFCB66"
  accent-soft: "rgba(255,176,32,.14)"
  accent-border: "rgba(255,176,32,.34)"
  success: "#3ECF8E"
  success-dark: "#7EE7B8"
  success-soft: "rgba(62,207,142,.14)"
  success-border: "rgba(62,207,142,.3)"
  danger: "#F0575A"
  danger-dark: "#FA9294"
  danger-soft: "rgba(240,87,90,.14)"
  danger-border: "rgba(240,87,90,.3)"
  warning: "#FFB020"
  warning-dark: "#FFCB66"
  warning-soft: "rgba(255,176,32,.14)"
  warning-border: "rgba(255,176,32,.3)"
typography:
  display:
    fontFamily: "'Big Shoulders Text', sans-serif"
    fontSize: "32px"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.4px"
  plate:
    fontFamily: "'Big Shoulders Text', sans-serif"
    fontSize: "56px"
    fontWeight: 800
    lineHeight: 0.8
    letterSpacing: "-1.5px"
  headline:
    fontFamily: "'Big Shoulders Text', sans-serif"
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.4px"
  label:
    fontFamily: "'Big Shoulders Text', sans-serif"
    fontSize: "10px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.9px"
    textTransform: "uppercase"
  body:
    fontFamily: "'Public Sans', sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.65
  mono-data:
    fontFamily: "'Fragment Mono', monospace"
    fontSize: "33px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.5px"
  mono-meta:
    fontFamily: "'Fragment Mono', monospace"
    fontSize: "10px"
    fontWeight: 400
    lineHeight: 1.3
    letterSpacing: "0.9px"
motion:
  ease-out: "cubic-bezier(.16,1,.3,1)"
  ease-io: "cubic-bezier(.65,0,.35,1)"
  ease-fast: "cubic-bezier(.4,0,.2,1)"
  t-fast: "170ms"
  t: "300ms"
  t-travel: "420ms"
  t-slow: "640ms"
rounded:
  none: "0"
  xs: "2px"
  sm: "4px"
  md: "7px"
  lg: "9px"
  xl: "12px"
  pill: "999px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "20px"
  lg: "32px"
  xl: "44px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.paper}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "12px 17px"
  button-primary-hover:
    backgroundColor: "{colors.accent-dark}"
  button-outline:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.accent-dark}"
    rounded: "{rounded.md}"
    padding: "11px"
  button-outline-hover:
    backgroundColor: "{colors.accent-soft}"
  nav-item:
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    padding: "10px 12px"
  nav-item-active:
    textColor: "{colors.ink}"
---

# Design System: TalentRadar

## How to read this document

This system is **normative on every route**. There is no second, older box-grid idiom running in
parallel — a bordered, radius-cornered container anywhere is a deliberate exception (a surface that
genuinely floats), not a leftover.

Two things carry different weight here, and the difference matters if you are implementing:

- **Shipped.** The app chrome, Search, Applications, Interview Lab, Resume Studio, Company Intel,
  Settings, Onboarding and the auth split all run this system in `frontend/app/globals.css` today.
- **Specified, not yet built.** The **Overview day board** described below replaces the previous
  Overview iteration (`.dayboard` ledger table, `.metric-grid`, `.dashboard-grid`/`.bottom-grid`
  panels, and the in-page copilot panel), which is what `frontend/app/dashboard/page.tsx` still
  renders. Build against this document and the reference mockup, not against the current page.

**Reference mockup for the Overview** (the review surface — feedback arrives as comments on it, and
it is republished to the same URL rather than re-created):
https://claude.ai/code/artifact/231a47a6-4eb5-46c6-ba15-92b28f7d4e2e

Boxes are kept only where a surface floats over something else:
- **`.cp-chat`** — the copilot conversation is a working surface inside a page, not a row in a list.
- **`.ci-drawer`** and the Overview's **copilot drawer** — overlays sliding over the board.
- **`.search-form` / `.ci-searchbar`** — a search bar floats above the results it filters.
- **`.editor-pdf-frame`** — the compiled resume is a *document* on the board, so it keeps a frame.
- **Accent-soft callouts** (`.question-card`, `.action-card`, `.insight-focus`, `.score-feedback`,
  `.resume-status`, `.form-error`) — semantic banners, sized to their message.
- **Inputs, chips and pills** — a 1px `--line` outline is the right affordance for something you
  type into or toggle.

## North Star: the ruled operations sheet

TalentRadar is read the way a controller reads a printed operations sheet: a warm graphite ground,
structure from hairline rules that run edge to edge, and a single amber lamp reserved for whatever
needs the user right now. Nothing is a card. A row's importance is carried by its position, its
type scale and whether the lamp is lit — never by giving it a border and a drop shadow.

The palette is warm-neutral graphite and brushed steel — deliberately not the cool blue-slate that
reads as generic "AI dark mode" — with a warm off-white ink and one indicator amber spent sparingly.
Big Shoulders Text (condensed) sets labels, headings and plate numerals; Fragment Mono sets live and
tabular data; Public Sans carries reading copy.

**Key characteristics**
- Full-bleed hairline rules separate sections; content sits in an inset column inside them.
- Ranked hairline rows replace card grids everywhere — status reads through position and a lamp.
- One accent, amber, for status and action only. Never a decorative fill.
- A three-role type system: Big Shoulders (labels/headings/plates), Fragment Mono (data),
  Public Sans (prose). No serif, no system-display face.
- Motion is one named curve family plus one signature (FlapText). Nothing is animated for decoration.

## Colors

### Primary
- **Indicator Amber** (`#FFB020`, `--accent`): the one accent. Live-counter emphasis, primary
  buttons, active lamps, the travelling focus marker, lit ticks, hover on interactive rows.
- **Amber Dark** (`#E69A0E`, `--accent-dark`): hover for primary buttons; action text on dark
  surfaces where full amber is too light for small type.
- **Amber Light** (`#FFCB66`, `--accent-light`): accent text and lit ticks on the darkest surfaces.

### Neutral
- **Board Paper** (`#131211`, `--paper`): the base — warm near-black, never blue-black.
- **Surface / 2 / 3** (`#1B1917` / `#211F1C` / `#2B2825`): stacked panel, input and chip grounds.
- **Ghost** (`#3A3631`, `--ghost`): the unlit plate numeral. Dark enough to read as structure
  rather than content, light enough to count as a numeral.
- **Ink / Ink 2 / Muted / Faint** (`#ECEEF0` / `#C7CBD1` / `#9AA0A6` / `#868C94`): text in
  decreasing emphasis. Ink is warm-cool off-white, never pure white.
- **Hairline / Soft / Strong** (`rgba(255,255,255,.10/.055/.18)`): the divider vocabulary —
  `-strong` for structural boundaries, `-soft` for row-to-row rhythm, base for input and chip edges.

### Semantic
**Success** (`#3ECF8E`) / **Danger** (`#F0575A`) / **Warning** (`#FFB020`): status only, never
decorative. Warning shares the accent hue intentionally — here, "needs you" and "amber" are one signal.

### The One Lamp Rule
Amber is reserved for what needs the user: live data, primary actions, active state, status. It is
never a decorative fill or a repeated brand flourish. Its rarity is what makes it legible.

### The Hairline-Not-Border Rule
Structure comes from `border-top`/`border-bottom` hairlines between ranked rows, and from full-bleed
section rules — not from `border` boxes around containers.

## Typography

**Display/Label:** Big Shoulders Text (500–800). **Body:** Public Sans (400–700).
**Data:** Fragment Mono. Each with a real fallback stack.

Condensed Big Shoulders gives labels and headings a station-signage density; Fragment Mono gives
every live number instrument-panel precision (`font-variant-numeric: tabular-nums` wherever a number
can change); Public Sans stays plain for reading copy so the display faces never carry paragraphs.

### Hierarchy
- **Plate** (800, 54–66px, -1.5px): the Overview's ranked action numerals. The largest type in the
  system, and the only place Big Shoulders runs above Display scale.
- **Display** (700, 30–44px, -0.4 to -0.6px): a route's `h1`. One per page.
- **Headline** (600–700, 15–28px): section headings and row titles.
- **Data (mono)** (700, 25–33px, tabular-nums): the big live numbers. The only elements FlapText wraps.
- **Body** (400, 12.5–15px, 1.5–1.7): reading copy.
- **Label** (600, 9–11px, uppercase, 0.5–1.2px): row-kind tags and section eyebrows — Big Shoulders
  at small sizes, or Fragment Mono when it is tabular metadata (timestamps, codes, counts).

### The Mono-Means-Live Rule
Fragment Mono is for values that are data — counts, scores, dates, codes — not for arbitrary
emphasis. If a number can change between renders it is a Fragment Mono / `tabular-nums` candidate.

## Motion

The system has a named curve family so per-surface motion stops being improvised:

```
--ease-out:  cubic-bezier(.16,1,.3,1)   /* entrances: decisive, long decay */
--ease-io:   cubic-bezier(.65,0,.35,1)  /* travel between two known states */
--ease-fast: cubic-bezier(.4,0,.2,1)    /* small state changes: color, border */
--t-fast:170ms   --t:300ms   --t-travel:420ms   --t-slow:640ms
```

**Rules.** Animate `transform` and `opacity` only. One thing moves at a time. Every motion is
disabled wholesale under `prefers-reduced-motion: reduce`, and any hover-only affordance is
suppressed below 920px — a touch device cannot un-hover to restore what a hover dimmed.

### Signature: FlapText
Every live number renders through `<FlapText>`, which splits the value into characters and staggers
a ~500ms (45ms per character) 3D rotate-and-fade cascade, keyed by the value so a change replays it.
This is the system's one *recurring* authored motion for numeric content.

### The one stagger exemption
The system runs no per-item stagger on grids or on any list that can grow — an earlier version did,
and it was removed in favour of FlapText. This is exempted **only** for the three Today's Focus
rows, which stagger in once on load at 70ms intervals (200/270/340ms): a fixed-length,
once-per-session entrance on the page's primary content. Do not extend it to the sweep manifest,
the tracker, search results, or any other list.

### The Matte-Metal Rule
Buttons and rows get a firm 1px press (`translateY(1px)`) and, where they float, a soft tinted
shadow. **Never a scale**, never a glossy sweep, never a hard offset shadow. This board is brushed
steel, not glass or neobrutalist paper.

## Layout

### The sheet (Overview)
Sections are full-bleed **bands** separated by a single `--line-soft` rule that runs the entire
width; content sits in an inset `.inner` column (max-width 1160px, `clamp(16px,4vw,40px)` side
padding, 44px block padding). The rule reaching the viewport edge while the content stops short is
what makes the page read as a printed sheet rather than a stack of sections.

### Everywhere else
A single centered content column (`.content-wrap`, max-width 1260px). Within it:
- A **two-column split gets a vertical hairline**, not a gap: `.results-layout`, `.editor-shell`,
  `.studio-grid` — the second column carries `border-left: 1px solid var(--line-soft)` and 44px of
  padding, both dropped when it stacks.
- A **list of comparable things is a ranked row list**: `.ci-grid`, `.application-table`,
  `.history-list`, `.editor-section-list`, `.mode-picker`, `.vt-turn` — each with a
  `:first-child{border-top:0}` reset.

Responsive collapse (920px / 560px on the Overview; 900/1050/700px elsewhere) stacks multi-column
grids and swaps vertical hairlines for horizontal ones. The row rhythm persists at every width.

## Elevation & Depth

Flat-with-hairlines. A light, non-glossy lift is reserved for the few surfaces that genuinely float:
`inset 0 1px 0 rgba(255,255,255,.03), 0 10px 24px rgba(0,0,0,.35)` on `.cp-chat` and
`.editor-pdf-frame`, heavier on `.ci-drawer`. **The Overview day board carries no shadow at all** —
every one of its surfaces sits on the sheet.

**If a surface does not float over something else, it does not get a shadow.**

- **Ambient lift**: `inset 0 1px 0 rgba(255,255,255,.03), 0 10px 24px rgba(0,0,0,.35)`.
- **Accent glow**: `0 6px 16px rgba(255,176,32,.2)` under `.primary-button` — the only colored
  shadow in the system, and not used on the Overview.
- **Drawer shadow**: `-16px 0 44px rgba(0,0,0,.5)` for the Company Intel and copilot drawers.

## Shapes

Radii scale with a container's role and stay small: **0 on the Overview's structural rules and
bands**, 2px on the Overview's buttons and the copilot rail button, 4px on tight inline chrome,
6–7px on buttons/inputs/chips elsewhere, 9–12px on the panels and drawers that keep a box, and full
pill only on status chips. Borders are 1px hairlines at `--line`, never heavier.

The Overview is deliberately the sharpest surface in the system: squared plates and edge-to-edge
rules are the sheet metaphor. Do not soften them to match the older routes.

## Components

### Buttons
- **Shape:** 2px on the Overview; 7px elsewhere (`.primary-button`, `.outline-button`).
- **Primary:** amber ground, paper-dark text, 700-weight uppercase label type.
- **Hover / active:** hover brightens toward `--accent-light` (Overview) or darkens to
  `--accent-dark` (elsewhere); active presses 1px. No scale, anywhere, including the copilot rail.
- **Outline/ghost:** 1px hairline border, `--ink-2` text; hover brightens border to
  `--accent-border` and text to amber.
- **Arrow affordance:** any button or text link ending in an arrow slides that arrow 3px right on
  hover. The label does not move.

### Navigation (AppShell)
A solid instrument-panel strip (`--paper`, `border-bottom: 1px solid var(--line-strong)`), not a
floating glass bar; hides on scroll-down and returns on scroll-up. Brand mark is one 24px amber tile
with an inset highlight and a horizontal seam — "one flap tile", the system's physical motif in
miniature. Nav items are uppercase Big Shoulders, muted, brightening to `--ink` with a 2px amber
underline that scales in from the left. Labels hide under 900px; the bar scrolls rather than wraps.

### Masthead (Overview) / Ledger Strip (every other route)
A ruled strip with a bottom `--line-strong` rule: a Fragment Mono context line, the title at Display
scale with an amber full stop (`<span>.</span>`), and a `--muted` subhead.

On the Overview the right side carries a **stamp block only** — date, local time, last sweep, city,
set in Fragment Mono and right-aligned. It carries no button: the copilot moved to the rail, and an
action here would compete with Today's Focus for the page's one primary gesture. On other routes the
right side carries at most one thing: a `.tracker-total` counter wrapped in `<FlapText>`.

Below the Overview's subhead sits the **readout** — a single row of counts divided by vertical
hairlines (`0 interviews today · 0 on the calendar · 0 in flight · 3 swept overnight`), Fragment Mono
values over Big Shoulders small-caps labels. Counts live here, below the greeting and above the
actions, because the page's job is to say what to do, not to report an account balance.

### Focus Spine (`.focus-list`) — the Overview's hero
Three ranked actions hang off a single vertical hairline at `--spine` (112px desktop, 76px below
920px). Each is marked by a plate numeral right-aligned against the spine, with a 16px tick joining
numeral to content. The lead action's numeral is amber at 66px; the others are `--ghost` at 56px.

The lead action carries a left-to-right amber wash that fades to nothing by 62% — it **bleeds out**
rather than terminating at an edge, which is what keeps it from reading as a card.

Numbering here is real: it encodes priority, and the order is what the user is being told. Do not
reuse plate numerals on a list whose order carries no meaning.

### Travelling marker (`.glider`)
**One** 2px amber bar rides the spine between actions — a single element that moves, never three
highlights blinking independently. It rests on 01 (the thing to do first), follows both hover and
`focusin`, and returns to 01 ~90ms after the pointer leaves the list. While the list is engaged,
un-hovered actions drop to `opacity:.42` so the row being read is the only one at full strength.
Suppressed below 920px.

On hover the plate shifts 4px and the body 10px — a slight differential, so the row turns toward the
reader rather than switching on.

### Sweep Manifest (`.role`)
TalentRadar does not pre-match a candidate to a board; it reads postings against the user's resume.
So this surface names what it actually did: postings with sweep timestamps, a plain-English reason
each was kept, a five-tick fit meter, and a footer stating the work
(`Swept 4 boards · 61 postings read · 3 kept`). **Never show a bare "N new matches" count.**

Rows follow the `.job-card` gesture exactly — the whole row slides 10px right on hover, an amber
edge grows at the left, and the lit fit ticks brighten left-to-right at 45ms intervals.

Data comes from `GET /api/v1/dashboard` → `job_matches` (top 3, `services/job_matching.py`) and
`sweep` (the manifest footer, `services/sweep.py`).

### Gauge Row (`.gauges`) — "Where you stand"
Three columns divided by vertical hairlines, each a small-caps label, a Big Shoulders value with a
`--muted` unit, and a **measure**: a hairline with 5 etched ticks and a `--success` fill for the
achieved portion. An empty gauge keeps its ticks and drops the fill, so "nothing yet" is visible as
a shape rather than only as a zero. This replaces `.metric-card` on the Overview.

### Gap Chart (`.chart`)
Skill gaps are a real chart, not a list of bars: one shared axis (0–12) with a faint tick grid,
labels naming values the chart actually reaches, a 2px amber rule per skill and an emphasized
endpoint marker. Bars grow from the axis on load (820ms, 80ms apart); endpoints fade in after.
Below-threshold skills use `--faint` rather than amber, so the lamp still means "act on this".

### Copilot Rail (`.rail`) and Drawer (`.drawer`)
The copilot is **not** a panel on the Overview. It is a fixed 54px right-edge rail — amber sparkle
button, vertically-set "COPILOT" label, green live dot — that opens a 390px drawer over a scrim.
The rail icon rotates 90° while open. Escape closes; focus moves to the input on open and back to
the rail button on close; suggested prompts stagger in 150–300ms after the panel lands and clicking
one loads it into the input. Below 560px the rail becomes a bottom bar and the drawer goes
full-width. The page reserves `padding-right: var(--rail)` so the sheet's rules stop at the rail.

### Signature Component: FlapText
See *Motion*. Wrap every number that can change; set `tabular-nums` on its container.

### Briefing Row (`.cp-card`)
A hairline-divided row opening with a 7px status lamp whose color encodes tone (amber = needs you,
red = warning, green = momentum, light amber = action) with a soft halo. Lamp → kind label →
headline → detail → hover-revealed dismiss/snooze actions. Empty state is a low-key "nothing needs
you" line with one outline CTA, never a fabricated placeholder.

### Job Card Row (`.job-card`)
Hairline-topped row, no box; hover slides it 10px right and tints with `--line-soft`. Company mark →
title/company → meta (location, remote, salary) → skill chips → hairline-topped action row.

### Status Rail (`.status-tabs`)
The tracker's filter borrows the nav vocabulary exactly: uppercase Big Shoulders on the page ground,
the selected one brightening to `--ink` and lighting a 2px amber bar that scales in from the left.
Never a row of filled pills — nine filled amber pills would spend the one lamp color nine times.

### Tracker Row (`.application-row`)
Hairline-topped grid row opening with a 7px status lamp: amber = live and needs you, green = offer,
red = rejected/withdrawn, faint = saved. Company mark → role/company → status select → mono date →
actions. Hover slides 10px right.

### Directory Row (`.ci-card`)
One ranked row per employer — logo, name/industry, description, tier code, stack chips, open-role
count. Selection lights an amber left edge (`inset 3px 0 0 var(--accent)`), not an outline, so
"the one you're reading" and "the one that needs you" stay the same signal. `.mode-card` and
`.editor-section-card` use the identical lit-edge treatment.

## The Overview, section by section

Order is fixed, and each section answers one question:

| # | Section | Answers |
|---|---------|---------|
| 1 | Masthead + readout | Who am I, when is this, and is anything on fire? |
| 2 | **Today's focus** | What do I do first? *(largest visual weight on the page)* |
| 3 | Fetched while you were away | What arrived overnight, and why was it kept? |
| 4 | Quick actions | Where do I go if none of the above? |
| 5 | Where you stand | How far along am I? |
| 6 | Close your gaps | What is blocking me, and how do I fix it? |

The Overview is a plan for the day, not an account report. Counts stay, but they sit below the
actions — never above them, and never as the page's opening statement.

## Do's and Don'ts

### Do
- **Do** reserve amber for status and action — live numbers, primary buttons, active lamps, the
  travelling marker. Never a repeated decorative fill.
- **Do** build ranked-list surfaces as hairline-divided rows with a `:first-child{border-top:0}` reset.
- **Do** wrap any number that can change in `<FlapText>` with `tabular-nums` on its container.
- **Do** keep Fragment Mono for data, Big Shoulders for labels/headings/plates, Public Sans for prose.
- **Do** open every route with the ruled strip — and on the Overview, keep its right side a stamp
  block, not a button.
- **Do** give a two-column split a vertical hairline and 44px of padding, and drop both when it stacks.
- **Do** state what the system actually did ("swept 4 boards, read 61 postings, kept 3") in place of
  a bare metric.

### Don't
- **Don't** reintroduce a bordered, radius-cornered container for a list item, form, panel or a
  route's main content — the exceptions in *How to read this document* are the whole list.
- **Don't** put a card grid, a stat-tile row, or an in-page copilot panel back on the Overview.
- **Don't** add a scale transform, a glossy sweep or a hard offset shadow to any button.
- **Don't** extend the focus-row entrance stagger to any other list, or the plate numerals to a list
  whose order means nothing.
- **Don't** show "N new matches" — the product does not pre-match; it reads postings against a resume.
- **Don't** let a hover-only affordance survive to touch widths.
- **Don't** use `.eyebrow` as a kicker above a page title — that belongs to the ledger strip's
  context line. `.eyebrow` is a small-caps label *inside* a section.

## Backing services

- **Overnight sweep** — `services/sweep.py::run_overnight_sweep()`, scheduled in-process by
  APScheduler from `api/main.py` (there is no Celery worker in this stack). It ingests fresh
  postings for the union of every onboarded user's target roles via `ingestion/dispatcher.py`, then
  ranks them per user via `services/job_matching.py`. Neither half can raise: a scraper outage
  degrades the sweep to "re-rank what we already have" rather than skipping the day.
- **Schedule** — `DAILY_MATCH_HOUR`/`DAILY_MATCH_MINUTE`, on the *server* clock. Render runs UTC
  and the product is India-only, so overnight has to be written in UTC: 21:45 UTC is 03:15 IST.
  The `keepalive` workflow is what keeps the free instance resident long enough to fire at all —
  a spun-down instance runs no in-process cron.
- **Manifest** — `services/sweep.py::get_last_sweep()` reads the existing `ingestion_runs` audit log
  (source list, discovery count, finish time); the dashboard returns it as `sweep`.
