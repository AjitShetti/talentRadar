---
name: TalentRadar
description: AI-powered career intelligence for the Indian tech job market — read as a live departures board, not a dashboard of stat cards.
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
rounded:
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

## Scope of this document — read this first

**The board system now runs app-wide.** It started on the shared app chrome (`AppShell`/top nav), the Overview/dashboard, `BriefingFeed`, `CopilotWorkspace` and the shared `.job-card` row; every remaining route has since been structurally ported to it: Search (filter strip, tips rail, results), Applications (tracker rows + status rail), Interview Lab (start, session, voice stage, history), Resume Studio (editor shell, section rows, live ATS), Company Intel (directory rows + toolbar), Settings, Onboarding, and the Login/Signup split.

This document is therefore **normative everywhere**. There is no longer a second, older box-grid idiom running in parallel — a bordered, radius-cornered container on any route is now a deliberate exception (a genuinely floating surface), not a leftover.

The exceptions, and the reason each one keeps a box:
- **`.cp-chat`** — the copilot conversation is a distinct working surface inside a page, not a row in a list.
- **`.ci-drawer`** (+ its backdrop) — an overlay sliding over the board.
- **`.search-form` / `.ci-searchbar`** — the search bar floats above the results it filters.
- **`.editor-pdf-frame`** — the compiled resume is a *document* sitting on the board, so it keeps a frame and the ambient lift.
- **Accent-soft callouts** (`.question-card`, `.action-card`, `.insight-focus`, `.score-feedback`, `.resume-status`, `.form-error`) — semantic banners, sized to their message.
- **Inputs, chips and pills** — a 1px `--line` outline is still the right affordance for something you type into or toggle.

## Overview

**Creative North Star: "The Departures Board"**

TalentRadar's Overview reads like a live station board, not a dashboard of stat cards: a graphite instrument face, ruled hairline rows instead of bordered boxes, and a single amber lamp color reserved for whatever needs the user's attention right now. The board's job is to make "what's stale, what's moving, what to do next" legible at a glance, the way a commuter reads a departures board instead of scanning a grid of tiles.

The palette is warm-neutral graphite and brushed steel — deliberately not the cool blue-slate that reads as generic "AI dark mode" — with a warm off-white ink for text and a single indicator-lamp amber spent sparingly. Big Shoulders Text (condensed, uppercase-leaning) sets row labels and headings; Fragment Mono sets tabular/live data (scores, counts, dates); Public Sans carries reading copy. Structure comes from hairline rules (`--line`, `--line-soft`, `--line-strong`), not borders-around-boxes.

**Key Characteristics:**
- Ranked hairline rows replace bordered-card grids on the board surfaces — status reads through position and a lamp dot, not through a boxed container.
- One accent color, amber, is reserved for status/action; it is not used decoratively.
- Big Shoulders Text (labels/headings) + Fragment Mono (data) + Public Sans (copy) — a three-role type system, no serif, no system-display face.
- FlapText — a character-cascade reveal — is the board's one authored motion for live numbers.
- Flat-ink surfaces are lifted with tinted shadow + inset highlight, not gloss or hard offset shadows.

## Colors

Warm-neutral graphite instrument palette; the accent is a single indicator-lamp amber spent only on what needs the user, not as decoration.

### Primary
- **Indicator Amber** (`#FFB020`, `--accent`): the one accent. Live-counter emphasis (FlapText numbers, lit ticks), primary buttons, active-lamp status dots, focus rings (as `accent-soft`/`accent-border`), hover states on interactive rows/links.
- **Amber Dark** (`#E69A0E`, `--accent-dark`): hover state for primary buttons; link/action text color on dark surfaces (higher contrast than full accent for small text).
- **Amber Light** (`#FFCB66`, `--accent-light`): accent text on the dark `focus-panel` surface where full amber would be too saturated against near-black.

### Neutral
- **Board Paper** (`#131211`, `--paper`): the app's base background — warm-near-black, not blue-black.
- **Surface** (`#1B1917`, `--surface`) / **Surface 2** (`#211F1C`) / **Surface 3** (`#2B2825`): stacked panel/input backgrounds, each one step lighter for nested depth (card → input → chip).
- **Ink** (`#ECEEF0`, `--ink`): primary text — warm-cool off-white, never pure white.
- **Ink 2 / Muted / Faint** (`#C7CBD1` / `#9AA0A6` / `#868C94`): secondary text, metadata, timestamps, in decreasing order of emphasis.
- **Hairline / Hairline Soft / Hairline Strong** (`rgba(255,255,255,.10/.055/.18)`, `--line` / `--line-soft` / `--line-strong`): the row-divider and section-divider vocabulary — `-strong` for structural boundaries (board header, briefing top rule), `-soft` for row-to-row rhythm within a list, base `-line` for input/card borders.

### Semantic
- **Success** (`#3ECF8E`) / **Danger** (`#F0575A`) / **Warning** (`#FFB020`, same value as accent): status-only, never decorative. Warning shares the accent hue intentionally — on this board, "needs you" and "amber" are the same signal.

### Named Rules
**The One Lamp Rule.** Amber (`--accent`) is reserved for what needs the user: live data, primary actions, active/lit state, and status. It is never used as a decorative fill or a repeated brand flourish across a screen — its rarity is what makes the "needs you" signal legible.

**The Hairline-Not-Border Rule.** On the board surfaces (nav, Overview, BriefingFeed, CopilotWorkspace, job-card rows), structure comes from `border-top`/`border-bottom` hairlines between ranked rows, not from `border` boxes around containers. A bordered, radius-cornered box (`.card-form`, `.editor-panel`, `.ci-card`) is the old pattern, still correct on the routes that haven't been rebuilt — do not mix the two idioms on one surface.

## Typography

**Display/Label Font:** Big Shoulders Text (condensed sans, weights 500–800), with a sans-serif fallback.
**Body Font:** Public Sans (weights 400–700), with a sans-serif fallback.
**Data/Mono Font:** Fragment Mono, with a monospace fallback.

**Character:** Condensed, uppercase-leaning Big Shoulders Text gives row labels and headings a station-signage density; Fragment Mono gives every live number (scores, counts, dates, timestamps) a tabular, instrument-panel precision (`font-variant-numeric: tabular-nums` is applied wherever a number can change); Public Sans stays plain and legible for reading copy, so the two display faces don't have to carry paragraphs.

### Hierarchy
- **Display** (700, 32px, -0.4px tracking): the board header `h1` (`.board-header h1`) — one per page, greeting/date-ledger context.
- **Headline** (600, 15–19px, -0.2 to -0.4px tracking): panel and card titles (`.panel-heading h2`, `.cp-card h3`, `.ci-drawer-title h2`).
- **Data (mono)** (700, 25–33px, tabular-nums): the big live numbers — `.big-number`, `.ats-score-badge`, `.cp-stats strong`, `.market-stat strong`, `.tracker-total strong`. These are the only elements FlapText wraps.
- **Body** (400, 12–13px, 1.6–1.7 line-height): reading copy — card descriptions, briefing detail text, chat bubbles.
- **Label** (600, 9–11px, uppercase, 0.5–1.1px tracking): row-kind tags, section eyebrow-style meta (`.cp-card-kind`, `.metric-top span`, `.ci-fact span`) — set in Big Shoulders Text at small sizes or Fragment Mono when it's tabular metadata (timestamps, tier codes).

### Named Rules
**The Mono-Means-Live Rule.** Fragment Mono is reserved for values that are data — counts, scores, dates, codes — not for arbitrary emphasis. If a number can change between renders, it's a Fragment Mono / `tabular-nums` candidate; if it's prose, it's Public Sans.

## Layout

The board surfaces (Overview, BriefingFeed, CopilotWorkspace, nav) use a single centered content column (`.content-wrap`, max-width 1260px, 43px/42px/50px padding) rather than a masonry or card-grid layout. Within that column:
- The header is a **ruled ledger strip** (`.board-header`): a bottom hairline (`border-bottom: 1px solid var(--line-strong)`), date-then-title-then-subhead stack on the left, one primary action on the right.
- The metric row (`.metric-grid`) is a **flex row of counters divided by vertical hairlines** (`border-left: 1px solid var(--line-soft)` on each sibling after the first), not a grid of bordered cards.
- Below that, `.dashboard-grid` (1.3fr/1fr, 44px gap) and `.bottom-grid` (1.3fr/1fr, 20px gap) split the page into ranked panels; adjacent panels in a row get a vertical hairline divider, not a gap-only separation.
- Within any panel, rows are stacked with `border-top: 1px solid var(--line-soft)` between siblings and no divider before the first child — the recurring `:first-child{border-top:0}` pattern used by `.role-row`, `.task`, `.cp-card`, `.quick-actions a`, `.job-card`.

Responsive collapse (900px / 1050px / 700px breakpoints) stacks the multi-column grids to one column and turns off the vertical hairline dividers in favor of horizontal ones — the row-based rhythm persists at every width, it just re-orients.

The other routes use the same two moves at their own scale. A **two-column split gets a vertical hairline**, not a gap: `.results-layout` (tips rail | results), `.editor-shell` (sections | PDF preview), `.agent-grid`/`.studio-grid` — the second column carries `border-left: 1px solid var(--line-soft)` and 44px of padding, collapsing to a stacked single column with the rule removed. A **list of comparable things is a ranked row list**: `.ci-grid` (was a card grid, now hairline rows), `.application-table`, `.history-list`, `.editor-section-list`, `.mode-picker`, `.search-tips` buttons, `.vt-turn` — each with the `:first-child{border-top:0}` reset.

## Elevation & Depth

Flat-with-hairlines, with a light, deliberately non-glossy lift layer (the "REDESIGN LAYER" in `globals.css`) reserved for the few surfaces that genuinely float: a tinted inset top highlight plus a soft ambient drop shadow (`box-shadow: inset 0 1px 0 rgba(255,255,255,.03), 0 10px 24px rgba(0,0,0,.35)`) on `.cp-chat` and `.editor-pdf-frame`, a heavier version on `.focus-panel` and the `.ci-drawer`, and the search bars. Every ranked-row surface — metric grid, briefing rows, job cards, tracker rows, directory rows, editor sections, interview history — stays flat and relies on hairlines, not shadow, for structure. **If a surface does not float over something else, it does not get a shadow.**

### Shadow Vocabulary
- **Ambient card lift** (`inset 0 1px 0 rgba(255,255,255,.03), 0 10px 24px rgba(0,0,0,.35)`): boxed panels/cards that need to sit above the page ground.
- **Accent glow** (`0 6px 16px rgba(255,176,32,.2)`): under `.primary-button`, the only colored shadow in the system — reinforces the one-lamp accent.
- **Drawer/overlay shadow** (`-16px 0 44px rgba(0,0,0,.5)`): the Company Intel detail drawer sliding over the page.

### Named Rules
**The Matte-Metal Rule.** Buttons and interactive rows get a firm tactile press (`translateY(1px)` on `:active`) and a tinted resting shadow, never a glossy sweep or hard offset shadow — this board is brushed steel, not glass or neobrutalist paper.

## Shapes

Small, consistent corner radii scale with a container's role: 4px on tight inline chrome (icon buttons, brand mark), 6–7px on buttons/inputs/filter chips, 9–12px on panels/cards/drawers-in-miniature, and full-pill (`999px`/`20px`) on status chips and pills (`.match-pill`, `.ci-chip`, `.editor-status-pill`). Borders are 1px hairlines at `--line` (never heavier), used for input/chip/card outlines; structural separation inside ranked lists uses `border-top`/`border-bottom` hairlines instead of a surrounding border. There is no hard-offset/neobrutalist shadow anywhere in the system, and no sharp, radius-0 geometry — every corner is at least softly rounded.

## Components

### Buttons
- **Shape:** 7px radius (`.primary-button`, `.outline-button`).
- **Primary:** amber background (`--accent`), paper-dark text, 12px/17px padding, 700-weight 12px label type, amber glow shadow (`0 6px 16px rgba(255,176,32,.2)`).
- **Hover / Active:** hover darkens to `--accent-dark`; active presses down 1px (`translateY(1px)`) — no scale, no glossy sweep.
- **Outline/Ghost:** transparent-to-surface background, 1px hairline border, amber-dark text; hover fills with `--accent-soft` and border brightens to `--accent-border`.
- **Text button:** no border/background, amber-dark text, brightens to full amber on hover — used for in-row secondary actions ("Open in search", "Clear plan").

### Navigation (AppShell)
- **Style:** a solid instrument-panel strip (`--paper` background, `border-bottom: 1px solid var(--line-strong)`), not a floating glass bar — it hides on scroll-down and reappears on scroll-up (a few px of slack against jitter), always visible near the top.
- **Brand mark:** a single 24px amber tile with an inset top highlight and a horizontal seam line through its middle — "one flap tile," the system's physical motif in miniature.
- **Nav items:** Big Shoulders Text label, uppercase, 11px, muted by default; active/hover state brightens text to `--ink` and reveals a 2px amber underline that scales in from the left (`scaleX` transform, not a color-only change).
- **Mobile:** labels hide under 900px, icons remain; nav scrolls horizontally with hidden scrollbar rather than wrapping.

### Signature Component: FlapText
The board's one authored motion. Every live/changeable number (dashboard metrics, interview scores, ATS scores, Copilot stats) renders through `<FlapText>`, which splits the value into characters and staggers a short (~500ms, 45ms-per-character) 3D rotate-and-fade-in cascade, keyed by the value so a changed number replays the cascade instead of sitting static. It is disabled entirely under `prefers-reduced-motion`. This is deliberately the *only* recurring authored motion for numeric content — the system does not also run a per-item stagger on every grid (an earlier version did; it was removed in favor of this one signature).

### Briefing Row (`BriefingFeed` / `.cp-card`)
- **Style:** a hairline-divided row, not a card — no border, no background box. Each row opens with a 7px **status lamp** dot (`.cp-lamp`) whose color encodes tone (amber = primary/needs-you, red = warning, green = insight/momentum, light-amber = action) with a soft halo (`box-shadow: 0 0 0 3px [tone]-soft`).
- **Anatomy:** lamp → kind label (Fragment Mono/Big Shoulders, uppercase, e.g. "GOING COLD", "MOMENTUM") inline with the headline → detail copy → action buttons that only appear as a set on hover/focus for the dismiss/snooze tools.
- **Empty state:** a centered, low-key "nothing needs you" state with a single outline CTA — never a fabricated placeholder card.

### Job Card Row (`.job-card`, shared by Overview and Search)
- **Style:** hairline-topped row (no box), hover shifts the whole row 10px right and tints the background with `--line-soft` — a "slide to attend to me" gesture rather than a lift/shadow gesture.
- **Anatomy:** company-mark chip → title/company → meta row (location, remote, salary) → skill chips → hairline-topped action row.

### Ledger Strip (`.page-heading` / `.board-header`)
Every route opens with the same ruled strip: a `.board-kicker` context line (Fragment Mono, 12px, uppercase, `--faint` — the dashboard uses a live date here, other routes name the surface), the title at Display scale with an amber full stop (`<span>.</span>`), and a `--muted` subhead — all left, with a bottom `--line-strong` rule. The right side carries **at most one** thing: a `.primary-button` (Overview) or a `.tracker-total` counter (Applications, Search, Interview Lab, Company Intel) whose number is Fragment Mono at Data scale, wrapped in `<FlapText>`, over a small-caps unit label.

### Status Rail (`.status-tabs`)
The tracker's status filter borrows the nav's vocabulary exactly: uppercase Big Shoulders labels on the page ground, the selected one brightening to `--ink` and lighting a 2px amber bar that scales in from the left. Never a row of filled pills — a filled amber pill for each of nine statuses would spend the one lamp color nine times.

### Tracker Row (`.application-row`)
A hairline-topped grid row opening with a 7px **status lamp** (`.app-lamp`) on the same tone vocabulary as the briefing: amber = live and needs you (applied → interview), green = offer, red = rejected/withdrawn, faint = saved. Then company mark → role/company → status select → mono date → row actions. Hovering slides the row 10px right, matching `.job-card`.

### Directory Row (`.ci-card`)
Company Intel was a `repeat(auto-fill, minmax(252px,1fr))` card grid; it is now one ranked row per employer — logo, name/industry, two-line description, tier code, stack chips and open-role count on a single hairline-divided line. Selection lights an amber left edge (`inset 3px 0 0 var(--accent)`) instead of an outline, so "the one you're reading" and "the one that needs you" stay the same signal. `.mode-card` (Interview Lab) and `.editor-section-card` (Resume Studio) use the identical lit-edge treatment for their selected/open state.

### Metric Counter (`.metric-card`)
- **Style:** no card chrome — a flex column separated from siblings by a vertical hairline (horizontal on mobile). Label (small caps Big Shoulders) + icon → big Fragment Mono number (wrapped in FlapText) → supporting copy → optional tick/trend row → link-out.

## Do's and Don'ts

### Do:
- **Do** reserve amber (`--accent`) for status and action on the board surfaces — live numbers, primary buttons, active nav/lamp states. Never use it as a repeated decorative fill.
- **Do** build new ranked-list surfaces (statuses, feeds, rows of comparable items) as hairline-divided rows with a `:first-child{border-top:0}` reset, matching `.role-row`/`.cp-card`/`.job-card`.
- **Do** wrap any number that can change on re-render in `<FlapText>` and set `font-variant-numeric: tabular-nums` on its container.
- **Do** keep Fragment Mono reserved for data/metadata, Big Shoulders Text for labels/headings, and Public Sans for prose — don't blend the two display faces into body copy.
- **Do** open every route with the ruled ledger strip (`.page-heading`, which is the `.board-header` treatment): a `.board-kicker` context line in Fragment Mono small-caps, the title with its amber full stop, a `--muted` subhead, and at most one action or one `.tracker-total` counter on the right.
- **Do** give a two-column split a vertical hairline and 44px of padding on the second column, and drop both when it stacks.

### Don't:
- **Don't** reintroduce a bordered, radius-cornered container for a list item, a form, a panel or a route's main content — the exceptions listed in *Scope* are the whole list, and each earns its box by genuinely floating.
- **Don't** wrap a new bordered, radius-cornered box in amber-and-Big-Shoulders-Text and call it "on system" for a board surface — the defining move is hairline rows, not the color/type refresh.
- **Don't** add a hard-offset/neobrutalist shadow or a decorative glossy sweep to buttons or cards — this world's tactile language is a firm 1px press and a soft tinted lift, nothing louder.
- **Don't** extend the "flap tile" seam motif (currently only on the nav brand mark) to metric numerals or general panel chrome — see the accepted gap below.
- **Don't** use `.eyebrow` as a kicker above a page title — that job belongs to `.board-kicker` inside the ledger strip. `.eyebrow` survives only as a small-caps label *inside* a section (`.search-tips` head, the interview transcript, the resume block in Settings), alongside `.cp-card-kind`.
