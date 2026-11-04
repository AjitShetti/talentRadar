# Website quality and speed review: 2026-11-04

Scope: the Next.js 14 App Router frontend in `frontend/`, plus the repository-wide code-quality gates. Backend capacity and reliability are covered by a separate review.
Base: `main` at `dbe4c76`. No application code was changed for this review.

## How it was measured, and what could not be

| Check | Status |
|---|---|
| `npm ci && npm run build` (Next 14.2.35) | Done. The build passed. |
| Chunk and CSS sizes (gzip -9 on `.next/static`) | Done |
| Lighthouse 12, mobile and desktop presets, on `/`, `/login` and `/r/[slug]` | Done **locally** against `next start`, with a mock API on :8000 serving one public role. See the caveats below. |
| Bytes and API calls per authed route (Playwright, mocked API, signed-in token) | Done |
| `npm run lint`, `npx tsc --noEmit` | Done |
| `ruff check .`, `mypy .` (strict), `pytest -m "not slow"` | Done, without Postgres |
| **Live site** (`talent-radar-ten.vercel.app`) and **API cold vs. warm timing** (`talentradar-api.onrender.com`) | **Not done.** This cloud environment's network policy refused both hosts (`connect_rejected`). To unblock it, add the two hosts under Network access → Allowed domains in the environment settings, or run the commands under "Re-run on the live site" from any machine. |

Caveats for the local Lighthouse numbers:

- They come from `next start` on localhost: no Vercel CDN, no Brotli, no HTTP/2 edge. Lighthouse's simulated throttling still models a slow 4G link and a 4× CPU slowdown on mobile.
- Headless Chromium here has no GPU, so WebGL runs in software (SwiftShader) on the CPU. That inflates TBT on any page that animates `PrismGround`: the landing page and the auth pages. I re-ran those pages with WebGL disabled to separate the shader's cost from everything else.
- Treat the absolute numbers as indicative only. The *relative* findings (what blocks render, what is unused, what loads where) hold on the live site.

---

## 1. Measured numbers

### 1.1 Build: First Load JS per route

Shared by all routes: **90.2 kB**. That is `fd9d1056` (React DOM, 53.6 kB) + `2117` (Next runtime, 31.9 kB) + 4.7 kB of other chunks.

| Route | Page JS | First Load JS | Render |
|---|---:|---:|---|
| `/company-intel` | 19.9 kB | **140 kB** | static |
| `/outreach` | 15.6 kB | **140 kB** | static |
| `/dashboard` | 9.46 kB | 132 kB | static |
| `/events` | 11.4 kB | 131 kB | static |
| `/practice` | 7.13 kB | 131 kB | static |
| `/practice/challenges/[slug]` | 6.91 kB | 130 kB | **ƒ dynamic** |
| `/interview` | 9.55 kB | 129 kB | static |
| `/roles/[id]` | 3.68 kB | 128 kB | **ƒ dynamic** |
| `/resume-studio` | 7 kB | 127 kB | static |
| `/search`, `/settings` | 5.8–5.9 kB | 126 kB | static |
| `/practice/design/[slug]` | 5.3 kB | 125 kB (+ Excalidraw, lazy) | **ƒ dynamic** |
| `/applications` | 3.86 kB | 124 kB | static |
| `/agent` | 0.5 kB | 123 kB | static |
| `/` (landing) | 7.88 kB | 116 kB | static |
| `/onboarding`, `/login`, `/signup`, `/reset-password`, `/forgot-password`, `/unsubscribe` | 6.4–7.9 kB | 114–116 kB | static |
| `/check` | 2.1 kB | 110 kB | static |
| `/r/[slug]` | 1.67 kB | **101 kB** | ● ISR (6 h) |
| `/hiring/[slug]` | 0.2 kB | **99.2 kB** | ● ISR (6 h) |

First Load JS is healthy everywhere: every route is at or under 140 kB. The weight is in what loads *after* first load, and in CSS.

### 1.2 Lazy chunks, the real heavy hitters

| Chunk | Raw | gzip | What it is | When it loads |
|---|---:|---:|---|---|
| `120a652c` | 1,819 kB | 740 kB | Excalidraw font subsetting (woff2/wasm) | Not loaded when the board opens; export is off. |
| `ca4dcb09` | 592 kB | 149 kB | maplibre-gl | Only when a map is shown (`loadMapLibre()` in an effect) |
| `02ff8e9f` + `eb30a234` + `902` | 1,137 kB | 354 kB | Excalidraw core | When `/practice/design/[slug]` renders the board |
| `e40706f5`, `4869`, `4604` | 1,271 kB | 327 kB | mermaid (Excalidraw's mermaid-to-excalidraw) | Not loaded when the board opens |
| `d3ac728e`, `26d302b2` | 469 kB | 97 kB | KaTeX (from mermaid) | Not loaded when the board opens |

Measured with Playwright, signed in, against the mocked API:

| Route | JS (uncompressed) | CSS (uncompressed) | API calls on load |
|---|---:|---:|---|
| `/dashboard` | 700 kB / 20 files | 200 kB | 7: `/health`, `/dashboard/overview`, `/agent/briefing` (**once**), `/outreach`, `/outreach/pack`, `/profile/`, `/routine` |
| `/practice/design/[slug]` | **1,873 kB / 29 files** | **341 kB** | 5 |
| `/company-intel` | 700 kB | 282 kB (+ maplibre CSS, render-blocking) | 8 |
| `/events`, `/search`, `/outreach` | 700 kB | 200 kB | 5–7 |

Confirmed:

- Excalidraw is `ssr: false`, imported by its route only, and its fonts come from `/excalidraw/`.
- maplibre is a dynamic import in both map components.
- The day line and the Overview share one briefing read. `/agent/briefing` is requested once on `/dashboard`.
- `/r/[slug]` and `/hiring/[slug]` are ISR (`revalidate = 21600`, served as `x-nextjs-cache: HIT`, `s-maxage=21600`) and make no client fetch for content.
- The map tiles come from OpenFreeMap (free, no key).

### 1.3 CSS

| File | Raw | gzip | Loaded on |
|---|---:|---:|---|
| `globals.css` → `73b25d46.css` | **205 kB** (source 231 kB, 881 class selectors) | **36.2 kB** | **every route**, render-blocking |
| `public.css` → `76df235e.css` | 6.7 kB | 1.7 kB | every route, because the root `not-found.tsx` imports `PublicShell` |
| `landing.css` → `e8e4d00e.css` | 23 kB | 5.9 kB | `/` |
| maplibre + company map → `8933483d.css` | 83 kB | 10.5 kB | `/company-intel` (render-blocking, even in List view) |
| Excalidraw → `00828950.css` | 145 kB | 22.7 kB | the design route only |

Lighthouse finds **35 of 36 kB (gz) of `globals.css` unused** on `/`, `/login` and `/r/[slug]`.

### 1.4 Lighthouse (local, see the caveats)

| Page | Preset | Score | FCP | LCP | TBT | CLS | Transfer |
|---|---|---:|---:|---:|---:|---:|---:|
| `/` | mobile | 48 | 3.06 s | 3.88 s | **8,221 ms** | 0.000 | 256 KB / 22 req |
| `/` | desktop | 64 | 1.05 s | 1.05 s | **6,664 ms** | 0.009 | 268 KB / 24 req |
| `/` (WebGL off) | mobile | 77 | 3.20 s | 3.35 s | 81 ms | **0.119** | — |
| `/login` | mobile | 62 | 1.91 s | 2.66 s | **4,382 ms** | 0.000 | 207 KB / 21 req |
| `/login` | desktop | 99 | 0.57 s | 0.69 s | 31 ms | 0.004 | 207 KB / 21 req |
| `/r/[slug]` | mobile | 94 | 2.08 s | 2.68 s | 85 ms | 0.000 | 216 KB / 24 req |
| `/r/[slug]` | desktop | 100 | 0.55 s | 0.67 s | 0 ms | 0.000 | 224 KB / 26 req |

Notes on the table:

- Landing TBT is almost all "Other" main-thread time (42.8 s over the trace), attributed to `app/page-*.js`: the `PrismGround` WebGL loop running in software GL. With WebGL disabled, TBT falls to 81 ms. On a real phone the shader runs on the GPU, so TBT will be far lower than 8 s. It still keeps a full-screen fragment shader drawing every frame, which costs GPU time and battery on mid-range Android.
- The CLS of 0.119 with WebGL off was attributed to `.lp-fcard-frame` in the hero cards when JetBrains Mono swapped in (`font-display: swap`, no metric-matched fallback). With WebGL on, the same page measured CLS 0.000–0.009, so the shift depends on font timing and can reach real users.
- Render-blocking resources on mobile: Google Fonts CSS (177–878 ms each), `globals.css` (454–605 ms), and `public.css` on `/r` (305 ms). Lighthouse estimates the savings at **1.4 s on `/`**, 0.9 s on `/login` and 1.0 s on `/r`.
- With WebGL off, the `/login` run hit a Lighthouse LCP error (NaN). That row is omitted.

### 1.5 Code-quality gates

| Gate | Result |
|---|---|
| `npx tsc --noEmit` (frontend) | **0 errors** |
| `npm run lint` | **Does not run.** There is no ESLint config, so `next lint` opens the interactive "How would you like to configure ESLint?" prompt and exits 1. |
| ESLint with `next/core-web-vitals` (temporary config, not committed) | 0 errors, **2 warnings** (`react-hooks/exhaustive-deps`): `app/interview/page.tsx:309`, `app/settings/page.tsx:79` |
| `ruff check .` (0.15.20 from `uv.lock`) | **0 findings** |
| `mypy .` (strict) | **1,410 errors in 125 files**: 1,223 in `tests/`, 187 in app code. Top codes: `no-untyped-def` 1,014, `type-arg` 95, `attr-defined` 61, `no-untyped-call` 51, `arg-type` 43. Worst app files: `storage/repository.py` 23, `storage/models.py` 17, `api/routers/resumes.py` 13, `domain/entities.py` 12, `services/llm.py` 7, `api/routers/search.py` 7. Also 11 `unused-ignore`, and 5 unused `[[tool.mypy.overrides]]` sections (`apache_airflow`, `ragas`, `scrapling`, `slowapi`, `tavily`). |
| `pytest -m "not slow"` (4 workers, CI's dummy env vars) | **6,232 passed, 4 skipped, 0 failed, 70 errors in 11 m 55 s.** All 70 errors are Postgres `connect` failures, because no database ran here: 7 files, `tests/test_company_map_*.py` and `tests/test_posting_company.py`. CI provides pgvector, so these are environmental, not regressions. |
| Install footprint | `uv sync --extra dev` builds a **6.0 GB** venv, with torch and the CUDA wheels pulled in through the dev extra (via `ragas`/`datasets` and the `sentence-transformers` path). ruff and mypy live in a separate `lint` extra, so `--extra dev` alone has neither. |

---

## 2. Findings, ranked

The estimated gains are for mobile on a mid-range phone unless a finding says otherwise. Every fix stays on the current free tiers.

### F1. Google Fonts are loaded through CSS `@import`, a three-hop render-blocking chain (high)

- **Where:** `frontend/app/globals.css:1` (Inter + JetBrains Mono) and `frontend/app/landing.css:1` (Instrument Serif).
- **Impact:** The browser must download the 36 kB stylesheet, then discover and fetch `fonts.googleapis.com` CSS, then fetch from `fonts.gstatic.com`. That means two extra origins (DNS, TLS) before text styles settle. Lighthouse puts the render-blocking cost at 0.18–0.88 s per font stylesheet. The JetBrains Mono swap is also what shifted the landing hero cards (CLS 0.119 in one run).
- **Free fix:** Use `next/font/google` in `app/layout.tsx` and `app/page.tsx`. It self-hosts the files on Vercel at build time, preloads them, inlines `@font-face`, and generates a metric-matched `size-adjust` fallback. Expose each font as a CSS variable, so `--font-mono: ..., var(--font-jetbrains)` stays the token. Subset to `latin`.
- **Estimated gain:** FCP and LCP −0.5 to −1.0 s on mobile on `/`, `/login` and `/r`. Removes the font-swap CLS. Zero cost: the fonts ship as static assets from Vercel.

### F2. One 231 kB `globals.css` blocks render on every route, including the landing page and the public `/r` and `/hiring` pages (high)

- **Where:** `frontend/app/layout.tsx:4` (`import './globals.css'`), 2,776 lines and 881 class selectors.
- **Impact:** 36 kB gz on the critical path for every first visit, with 97% of it unused on `/`, `/login` and `/r` (Lighthouse: 35/36 kB). The public pages are what strangers from search results see first, and they need only the tokens plus `public.css`.
- **Free fix, in order of effort:**
  1. Split the feature blocks (`cm-*` company map, `ev-*` events, `ov2-*` overview, `pr-*` practice, outreach, resume studio, interview) into files imported by the page or component that uses them, the way `landing.css` and `public.css` already work. Next then emits per-route CSS. Keep tokens, reset, the shell, `.day-title` and the shared controls global.
  2. Move public and landing routes into a route group with their own layout that does not import the app styles, e.g. `app/(public)/layout.tsx` and `app/(app)/layout.tsx`.
  3. Delete the dead selectors. About 178 of the 881 class names in `globals.css` appear nowhere in `app/`, `components/` or `lib/`. Some of those are Excalidraw overrides (`HelpDialog`, `dropdown-menu*`) or prefixes built at runtime (`action-`). Clear leftovers include `dayboard*`, `db-row`/`db-lamp`/`db-time`/`db-what`, `dashboard-grid`, `agent-grid`, `bottom-grid` and `big-number`. Verify each with a grep before deleting.
- **Estimated gain:** −25 to −30 kB gz render-blocking on public and landing routes, worth −300 to −600 ms FCP on slow 4G. Smaller CSSOM and style recalc on every page.

### F3. The landing-page prism runs two full-screen WebGL loops forever (high on low-end mobile; inflated here by software GL)

- **Where:** `frontend/components/landing/PrismGround.tsx:161-163` (the `requestAnimationFrame` loop), mounted twice in `frontend/app/page.tsx:96` and `:240`, and once on each auth page (`app/login/page.tsx:25`, `signup`, `forgot-password`, `reset-password`, `unsubscribe`).
- **Impact:** Local TBT is 8.2 s (mobile) and 6.6 s (desktop) on `/`, and 4.4 s on `/login` mobile. With WebGL off, TBT is 81 ms. On real GPUs the main-thread cost is much smaller, but every frame still shades about 1.5× DPR × the viewport, and the loop never settles while the section is visible. On low-end Android that means jank on scroll, fan and battery drain, and slower input response (INP) on the login form. The code already pauses off screen, in hidden tabs and for reduced motion, which is good.
- **Free fix:**
  - Let the arch "settle": draw the intro for about 2–3 s, then stop the loop, or throttle it to about 15–20 fps by skipping frames on a timestamp.
  - Use a lower backbuffer DPR on coarse pointers (`matchMedia('(pointer: coarse)')` → DPR 1).
  - Skip WebGL entirely, keeping the CSS gradient that already exists as the fallback, when `navigator.connection?.saveData` is set or `navigator.hardwareConcurrency <= 4`.
  - On the landing page, start the second (CTA) instance only when it nears the viewport, which the IntersectionObserver already allows. Today it creates a second GL context on mount.
- **Estimated gain:** Most of the measured TBT on `/` and the auth pages, and INP on `/login` on real low-end phones. **Confirm with a real-device trace before and after**, because headless numbers overstate this one.

### F4. `/practice/design/[slug]`: the Excalidraw download waits for the problem fetch (medium)

- **Where:** `frontend/app/practice/design/[slug]/page.tsx:44-48` (the `dynamic()` factory) and `:181` (`if (!page) return …Loading the problem…`), before `<DesignBoard>` at `:219`.
- **Impact:** A serial waterfall. Excalidraw's about 354 kB gz (1.14 MB raw) of chunks and 23 kB gz of CSS start downloading only after `GET /practice/design/{slug}` returns. If the API is waking from sleep, the board appears only after both the cold start and the chunk download. This is the heaviest route at 1.87 MB of JS uncompressed.
- **Free fix:** Start the chunk on mount in parallel. Hoist the factory into `const loadBoard = () => import('@/components/practice/DesignBoard')`, call `void loadBoard()` in a `useEffect` of `DesignView` (or at module scope behind `typeof window !== 'undefined'`), and pass the same function to `dynamic()`. Keep `ssr: false` and the route-only import.
- **Estimated gain:** Time to a usable board drops by the chunk download time, about 0.6–1.5 s on 4G (more on 3G), and overlaps it entirely with an API wake.

### F5. Three client pages are rendered by a serverless function on every request (medium; Vercel quota)

- **Where:** `frontend/app/practice/design/[slug]/page.tsx:50` (reads `params` and `searchParams` props), `frontend/app/practice/challenges/[slug]/page.tsx:127` and `frontend/app/roles/[id]/page.tsx:26`. All are `'use client'` pages with a dynamic segment and no `generateStaticParams`, so the build marks them `ƒ` and serves them as `Cache-Control: private, no-store`.
- **Impact:** Each visit spends a Vercel function invocation and adds the function's TTFB (including its cold start) just to render a "Loading…" shell, since all the data is fetched in the browser. It also counts against the Hobby plan's function-invocation and duration allowances. This is free today, but it is a quota you could exhaust, which is why it is flagged.
- **Free fix:** Read the segment with `useParams()` and the query with `useSearchParams()` (inside `<Suspense>`). Then make `page.tsx` a thin server file that exports `generateStaticParams() { return [] }` with `dynamicParams = true` and renders the client component. Each path is rendered once and cached, the same as `/r/[slug]`.
- **Estimated gain:** −100 to −500 ms TTFB per visit (and more on a function cold start), and no function invocations for these routes.

### F6. Frontend lint never runs, and no CI job builds or type-checks the frontend (medium, code quality)

- **Where:**
  - `frontend/package.json:13` (`"lint": "next lint"`) with no `.eslintrc*` in `frontend/`.
  - `.github/workflows/deploy.yml`: its `lint` job (`:17`) is Python-only, and the frontend has no job at all.
  - `.github/workflows/deploy.yml:43`: `mypy . || true`, so mypy can never fail CI.
- **Impact:** `npm run lint` is interactive and exits 1, so it can't gate anything. The frontend's only check is Vercel's own `next build`, which runs ESLint only when a config exists. Type and hook mistakes reach production unchecked. Meanwhile mypy strict reports 1,410 errors and is decoration in CI.
- **Free fix:**
  - Commit `frontend/.eslintrc.json` with `{"extends": "next/core-web-vitals"}`. It is already clean apart from 2 warnings: fix `app/interview/page.tsx:309` and `app/settings/page.tsx:79`.
  - Add a `frontend` job to `deploy.yml`: `npm ci`, `npx tsc --noEmit`, `npm run lint`, `npm run build`. It is free on GitHub Actions for a public repository. For a private repository it uses the 2,000 free minutes a month, roughly 2–3 minutes per run with `actions/setup-node` npm caching.
  - Make mypy gate the app code first. Add `exclude = ["tests/"]` (or a per-module `disallow_untyped_defs = false` override for `tests.*`), fix the 187 app errors starting with `storage/repository.py` and `storage/models.py`, then drop the `|| true`. Remove the 5 unused override sections and the 11 unused `# type: ignore`s.
- **Estimated gain:** Regressions get caught before deploy. No runtime gain.

### F7. Every authenticated request pays a CORS preflight, cached for only 10 minutes (medium)

- **Where:**
  - `frontend/lib/api.ts:256` sets `Content-Type: application/json` on every request, including GETs with no body.
  - `api/main.py:217-225` sets up `CORSMiddleware` without `max_age`. Starlette's default is 600 s.
- **Impact:** `Authorization` already forces a preflight on authed calls. Each distinct URL (including query strings: directory filters, pagination, `?day=`) costs an extra `OPTIONS` round trip to Singapore before the real request. From India that is about 60–150 ms, and the cache entry lapses after 10 minutes. The Overview makes 7 calls on load.
- **Free fix:**
  - Set `max_age=7200` on the middleware. 7200 s is Chromium's cap; Firefox allows 24 h.
  - Stop setting `Content-Type` when there is no body. It doesn't remove the preflight on authed calls, but it does on `/health` and the public reads.
  - A larger option: proxy `/api/*` through a same-origin rewrite in `next.config.js`, which removes CORS entirely. That routes API traffic through Vercel's Hobby bandwidth and edge-request allowance, so measure the volume first.
- **Estimated gain:** About one round trip saved on most repeat requests after the first, roughly 60–150 ms per request on a filter-heavy page like Company Intel.

### F8. The root layout ships the API client to every route, and `public.css` too (low)

- **Where:**
  - `frontend/components/ApiWarmup.tsx:5` imports `API_URL` from `@/lib/api`. `lib/api.ts` pulls in `lib/day-line`, `lib/persistent-state` and the whole endpoint table, giving chunk `9116` (29.6 kB raw, 8.9 kB gz) in `/layout`. So it loads on `/` and `/login`, and the `/r` page loads it through the layout too.
  - `frontend/app/not-found.tsx:3` imports `PublicShell` (→ `app/public.css`), which puts `public.css` in the root CSS for every route.
- **Free fix:** Move `API_URL` into a tiny `lib/config.ts` that both `api.ts` and `ApiWarmup` import. Give `not-found.tsx` its own small markup, or import `public.css` only from the public pages.
- **Estimated gain:** −9 kB gz JS on public and landing first loads, and −1.7 kB gz render-blocking CSS on every route.

### F9. The maplibre CSS blocks render on `/company-intel` even in List view (low)

- **Where:** `frontend/components/CompanyMap.tsx:6` (`import 'maplibre-gl/dist/maplibre-gl.css'`), statically imported by `frontend/app/company-intel/page.tsx:6`.
- **Impact:** 10.5 kB gz of render-blocking CSS on the route. The JS is already lazy. `/events` does this better: its map is `next/dynamic` with `ssr: false` (`app/events/page.tsx:29`), so its CSS arrives with the chunk.
- **Free fix:** Load `CompanyMap` with `next/dynamic({ ssr: false })` in the same way, reusing the `MapChunkFailed` fallback pattern from the events page.
- **Estimated gain:** About 100–200 ms FCP on `/company-intel` on 4G, and a 20 kB smaller page chunk. The map component code moves out of the 140 kB first load.

### F10. Duplicate reads on first visits to Settings and Search (low)

- **Where:**
  - `frontend/app/settings/page.tsx:57` (`api.routine.get()`) runs alongside `components/DayLine.tsx:115` (same read, when the store isn't fresh).
  - `frontend/app/settings/page.tsx:65` and `app/search/page.tsx:62` (`api.profile.get()`) run alongside `components/AppShell.tsx:264` (same read when the name isn't cached).
- **Impact:** One or two redundant requests on a cold session. They are cheap individually, but on a waking instance each one queues behind the cold start.
- **Free fix:** Have Settings read and publish through the `lib/day-line` routine store (`routineIsFresh` / `publishRoutine`). Add a single in-flight memo for `profile.get()` in `lib/api.ts`, keyed by token and short-lived, the same idea as `wakeApi`'s shared promise.
- **Estimated gain:** 1–2 fewer requests per cold visit.

### F11. Duplicated logic and oversized components (low, maintainability)

- **IST offset and IST-day logic are defined six times:**
  - `lib/routine.ts:49`
  - `lib/interview-time.ts:7`
  - `lib/outreach.ts:36` and `:74`
  - `lib/day-line.ts:14-19`
  - `lib/event-time.ts:9-24`
  - `lib/outreach-met.ts:60`, plus the literal `+05:30` at `app/events/page.tsx:1078`

  Fix: one `lib/ist.ts` (`IST_OFFSET_MS`, `istDay`, `istMinutes`, `istDayKey`, `IST_SUFFIX`) imported everywhere. The `e2e/*.rules.spec.ts` suites already pin the behaviour.
- **The two map components, `components/CompanyMap.tsx` (432 lines) and `components/EventMap.tsx` (386 lines), duplicate their load timeout, tile-error give-up and layer wiring** (`LOAD_TIMEOUT_MS`, `TILE_ERRORS_BEFORE_GIVING_UP`, `PIN_LAYERS`/`LIT_LAYERS`/`HIT_LAYERS`). Fix: move a `useMapLibre(container, {onUnavailable})` hook into `lib/maplibre.ts`.
- **The five auth pages repeat the same aside, prism and brand markup inline on one line each** (`app/login/page.tsx:25`, `signup:23`, `forgot-password:19`, `reset-password:24`, `unsubscribe:27`). Fix: one `AuthLayout` component.
- **Oversized client pages:**
  - `app/company-intel/page.tsx`: 1,263 lines, 63 kB, 36 top-level components
  - `app/events/page.tsx`: 1,109 lines, 53 kB
  - `app/interview/page.tsx`: 746 lines
  - `components/outreach/ThreadDrawer.tsx`: 668 lines
  - `app/resume-studio/page.tsx`: 621 lines
  - `app/dashboard/page.tsx`: 610 lines

  Splitting the drawer, filters and list into `components/<feature>/*` makes them testable and lets the drawers load with `next/dynamic` on first open.
- **`lib/api.ts` is 565 lines and 61 kB**, most of it type declarations on very long lines. Fix: split the types by domain (`lib/api/types/*.ts`). No bundle change, since types erase, but review diffs become readable.

### F12. Dead code and dead assets (low)

- `components/overview/Charts.tsx:11`, `:29`, `:49`: `StackBar`, `Ring` and `Sparkline` are exported and used nowhere. Only `ScoreBars` is imported (`app/dashboard/page.tsx:11`).
- These exports are never imported elsewhere; check each one before deleting, since some are used inside their own file:
  - `lib/theme.ts`: `isChoice`, `applyChoice`
  - `lib/event-map.ts`: `inBounds`, `sharedVenueNote`, `describePin`, `pinNote`, `stackOffset`, `spreadStacks`
  - `lib/maplibre.ts`: `WORKER_URL`, `installWorker`
  - `lib/voice.ts`: `SILENCE_HANG_MS`
  - `lib/api-warmup.ts`: `SLOW_MS`
  - `lib/use-dialog.ts`: `FOCUSABLE`
  - `lib/tour.ts`: `PINNED_BELOW`
- `frontend/public/hero.jpg` (456 kB), `screenshot-resume.jpg` (492 kB) and `screenshot-search.jpg` (406 kB): 1.35 MB that nothing references, including `app/manifest.ts`. They are never downloaded by visitors, but they ship in every deploy and the Docker image.
- About 150 or more unreferenced class selectors in `globals.css` (see F2).

### F13. Robustness of the public pages when the API is asleep (low)

- **Where:** `frontend/lib/public-api.ts:44-47` (`publicRole` rethrows non-404 errors) and `app/r/[slug]/page.tsx:22` (`dynamicParams = true`).
- **Impact:** The build pre-renders whatever `publicRoleList(200)` returns, which is an empty list if the API is asleep at build time. Any slug not yet cached is then rendered on first request. If the API is cold or down at that moment, the visitor gets the error page instead of the role. Pages already cached are safe, because a failed revalidation keeps serving the stale copy.
- **Free fix:**
  - Wake the API in the Vercel build: a `prebuild` `curl --retry` to `/health` with about 60 s of patience, so `generateStaticParams` sees real data.
  - Add `app/r/[slug]/error.tsx` that says the role could not be loaded right now and offers a retry, rather than the generic error page.
- **Estimated gain:** Fewer failed first visits from search engines right after a deploy.

### F14. Things that may cost money or quota (flag only; none needs a paid plan today)

- **`ApiWarmup` pings `/health` from every page view, including public `/r` and `/hiring` pages and crawler visits that run JavaScript** (`components/ApiWarmup.tsx:28`). It keeps the Render instance awake as long as traffic arrives, together with `.github/workflows/keepalive.yml` (`7 */3 * * *`). Render's free plan has a monthly instance-hour allowance per workspace. One always-on service fits it; a second free service in the same workspace would not. Keep the pinging to app pages if a second service is ever added.
- **Vercel Hobby limits** (function invocations, edge requests, bandwidth) are what F5 and the optional rewrite in F7 touch. F5's fix *reduces* usage; measure the rewrite before adopting it.
- **PostHog** (`lib/analytics.ts`) is free up to its monthly event allowance. The only event on public traffic is `public_role_view`, so keep it at one event per view.
- The **6 GB dev install** (CUDA wheels via the `dev` extra) makes any future CI test job slow, and on a private repository burns Actions minutes. Move `ragas` and `datasets` into their own `eval` extra so `dev` stays small.

---

## 3. Quick wins (each under an hour, no behaviour change)

1. **Fonts:** switch to `next/font/google` (F1). This is the largest single FCP/LCP gain on the pages strangers see.
2. **Lint:** add `frontend/.eslintrc.json` → `{"extends":"next/core-web-vitals"}`, fix the 2 hook warnings, and add a `frontend` CI job running `tsc --noEmit && npm run lint && npm run build` (F6).
3. **Excalidraw preload:** start the import on mount of `DesignView` (F4).
4. **CORS:** set `max_age=7200` on `CORSMiddleware`, and send `Content-Type` only with a body (F7).
5. **`not-found.tsx`:** stop it importing `PublicShell`, and move `API_URL` out of `lib/api.ts` (F8).
6. **`CompanyMap`:** load it through `next/dynamic({ ssr: false })` (F9).
7. **Prism:** stop or throttle the loop after the intro and cap DPR at 1 on coarse pointers (F3).
8. **Dead code:** delete `StackBar`, `Ring`, `Sparkline` and the three unused JPEGs (F12).
9. **mypy:** remove the five unused overrides in `pyproject.toml` and the 11 unused ignores (F6).

## 4. Re-run on the live site

This environment could not reach the production hosts. From any machine:

```bash
# Frontend, three pages × two presets
for u in / /login /r/<a-real-slug>; do
  npx lighthouse "https://talent-radar-ten.vercel.app$u" --only-categories=performance --output=json --output-path="lh-mobile$(echo $u|tr / _).json"
  npx lighthouse "https://talent-radar-ten.vercel.app$u" --preset=desktop --only-categories=performance --output=json --output-path="lh-desktop$(echo $u|tr / _).json"
done

# API: cold (after >15 min idle) and then warm, measured separately
curl -s -o /dev/null -w 'cold  ttfb=%{time_starttransfer}s total=%{time_total}s\n' --max-time 120 https://talentradar-api.onrender.com/health
for i in 1 2 3; do curl -s -o /dev/null -w 'warm  ttfb=%{time_starttransfer}s\n' https://talentradar-api.onrender.com/health; done
curl -s -o /dev/null -w 'roles ttfb=%{time_starttransfer}s\n' 'https://talentradar-api.onrender.com/api/v1/public/roles?limit=50'
```

The cold start (about 40 s on Render free) is a hosting property, and `ApiWarmup` already explains it in the UI. Don't count it against the frontend.
