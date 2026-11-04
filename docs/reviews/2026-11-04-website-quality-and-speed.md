# Website quality and speed review, 2026-11-04

Scope: the Next.js 14 frontend in `frontend/`, plus the backend's lint, type-check and test baseline. Backend capacity and reliability are reviewed separately and are not covered here.
Base: `main` at `dbe4c76`. This report changes no application code.

## TL;DR

1. **The landing page's WebGL background blocks the main thread.** `components/landing/PrismGround.tsx` runs a full-viewport fragment shader on every animation frame, and the loop never stops. Lighthouse mobile on `/` measures **TBT 18.3 s**. With the same build and only `prefers-reduced-motion` turned on, which stops the loop, TBT is **0.9 s**. On `/login` the drop is from 4.7 s to 0.09 s, and the score goes from 59 to 96. The same component is on `/login`, `/signup`, `/forgot-password`, `/reset-password` and `/unsubscribe`. This one change is worth more than everything else in this report combined.
2. **Bundles are in good shape.** The shared JS is 90.2 kB. Every route loads between 90 and 140 kB First Load JS. MapLibre (279 kB gz) and Excalidraw (383 kB gz) load only on the routes that need them. The design route keeps `ssr: false` and loads Excalidraw only there.
3. **Lint never runs.** `frontend/` has no ESLint config, so `npm run lint` and the lint step of `next build` stop at the "How would you like to configure ESLint?" prompt. When I ran the Next rules ad hoc, they found only 2 warnings, so adding the config is cheap.
4. **mypy strict reports 1,413 errors, and CI ignores them** (`mypy . || true`). 190 of the errors are in application code and 1,223 are in `tests/`, almost all of those missing annotations. Ruff is clean. pytest: 6,232 passed, 0 failed, and 70 errors, all of them "connection refused" on Postgres port 5432, because this container has no database.
5. **I could not reach the live site from this run.** The environment's network policy refused `talent-radar-ten.vercel.app` and `talentradar-api.onrender.com`. All Lighthouse numbers below come from the same production build served locally with `next start`, and none are from Vercel. API cold and warm timings were not measured.

Nothing in this report requires a paid plan. Every fix below is a code or config change on the current free tiers.

---

## 1. Measured numbers

### 1.1 Build (`npm ci && npm run build`, Next 14.2.35)

The build passed. `tsc` passed. Lint was skipped because there is no ESLint config (see F4).

| Route | Size | First Load JS |
|---|---:|---:|
| `/` (landing) | 7.88 kB | 116 kB |
| `/company-intel` | 19.9 kB | **140 kB** |
| `/outreach` | 15.6 kB | **140 kB** |
| `/dashboard` | 9.46 kB | 132 kB |
| `/events` | 11.4 kB | 131 kB |
| `/practice` | 7.13 kB | 131 kB |
| `/practice/challenges/[slug]` (ƒ dynamic) | 6.91 kB | 130 kB |
| `/interview` | 9.55 kB | 129 kB |
| `/roles/[id]` (ƒ dynamic) | 3.68 kB | 128 kB |
| `/resume-studio` | 7 kB | 127 kB |
| `/search`, `/settings` | 5.8–5.9 kB | 126 kB |
| `/practice/design/[slug]` (ƒ dynamic) | 5.3 kB | 125 kB |
| `/applications` | 3.86 kB | 124 kB |
| `/agent` | 0.5 kB | 123 kB |
| `/onboarding`, `/login`, `/signup`, `/forgot-password`, `/reset-password`, `/unsubscribe` | 6.4–7.9 kB | 114–116 kB |
| `/check` | 2.1 kB | 110 kB |
| `/r/[slug]` (● SSG/ISR) | 1.67 kB | 101 kB |
| `/hiring/[slug]` (● SSG/ISR) | 0.2 kB | 99.2 kB |
| **Shared by all** | | **90.2 kB** (`fd9d1056` 53.6 kB + `2117` 31.9 kB + 4.7 kB other) |

Lazy chunks, which are not counted in First Load and are fetched only when used (from `.next/react-loadable-manifest.json`, gzip -9):

| Dynamic import | Fetched when | Gzipped |
|---|---|---:|
| `maplibre-gl` (via `lib/maplibre.ts::loadMapLibre`) | a map mounts | **279 kB** (1,044 kB raw) |
| `components/practice/DesignBoard` (Excalidraw) | `/practice/design/[slug]` only, `ssr: false` | **383 kB** (1,295 kB raw) |
| Excalidraw font subsetter (harfbuzz wasm) | only on export or subsetting, which is turned off | 718 kB |
| `@excalidraw/mermaid-to-excalidraw` + mermaid diagrams | only on mermaid paste, which is unused | 191 kB+ |
| `components/EventMap` | `/events` map view | 15 kB |

CSS, gzipped, render-blocking on every route:

| Stylesheet | Gzipped | Where it loads |
|---|---:|---|
| `app/globals.css` (2,776 lines) | **36 kB** | every route, including the landing page and `/r`, `/hiring` |
| `app/landing.css` | 6 kB | `/` |
| `app/public.css` | 2 kB | `/r`, `/hiring`, `/check`, 404 |
| maplibre CSS | 10 kB | `/company-intel` up front (static import); `/events` lazily |
| Excalidraw CSS | 23 kB | the design route, lazily |

Static output: `.next/static` is 11 MB. `public/excalidraw/` holds 14 MB of fonts copied at build. These count toward Vercel's deployment size, but no visitor downloads them unless the whiteboard uses them.

### 1.2 Lighthouse 13.6.0 (local production build, simulated throttling)

Served with `node .next/standalone/server.js` on localhost, which makes TTFB unrealistically low (6–47 ms). Lighthouse throttles the network and CPU in simulation, so LCP and TBT are comparable to what PageSpeed Insights would report for the JS and CSS cost. Headless Chromium draws WebGL in software (SwiftShader), so the shader cost is worse here than on a phone with a GPU. The reduced-motion run controls for that, because it removes only the animation.

| Page | Mode | Score | FCP | LCP | TBT | CLS | Speed Index | Transfer |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `/` | mobile | 59 | 1.97 s | 1.97 s | **18,317 ms** | 0.000 | 8.6 s | 256 KiB |
| `/` | desktop | 60 | 0.62 s | 0.62 s | **13,046 ms** | 0.009 | 10.0 s | 268 KiB |
| `/` | mobile, reduced motion | 75 | 1.98 s | 1.98 s | 918 ms | 0.000 | 4.2 s | |
| `/login` | mobile | 59 | 1.92 s | 2.82 s | **4,703 ms** | 0.000 | 5.7 s | 207 KiB |
| `/login` | desktop | 60 | 0.64 s | 0.76 s | 1,282 ms | 0.004 | 6.6 s | 207 KiB |
| `/login` | mobile, reduced motion | **96** | 1.92 s | 1.92 s | 89 ms | 0.000 | 4.1 s | |
| `/r/<slug>` | both | n/a: 500 locally, see F7 | | | | | | |

Mobile main-thread breakdown on `/`: "Other" (GPU and raster work under requestAnimationFrame) 26.8 s, Script Evaluation 1.6 s, Style & Layout 1.0 s. Time to Interactive was 27.9 s.

Fonts on `/`: two Google Fonts stylesheets (`Inter` + `JetBrains Mono` from `globals.css`, `Instrument Serif` from `landing.css`), then three woff2 files from `fonts.gstatic.com`, 63 kB in total.

### 1.3 Requests per page (Playwright, signed in with a stub token, API unreachable)

Each authenticated page makes its own reads, plus three from the shell: `GET /api/v1/profile/` from `AppShell`, which is cached in localStorage once it succeeds, and `GET /agent/briefing` and `GET /routine` from `DayLine`. All of them start in parallel after mount. On `/dashboard` I counted 7 distinct API reads and no duplicate briefing: the Overview publishes its read (`app/dashboard/page.tsx:350`) and `DayLine` skips its own on the Overview (`components/DayLine.tsx:100`). The day line and the Overview do share one briefing read, as intended.

| Route | API reads on load |
|---|---|
| `/dashboard` | overview, briefing, outreach, outreach/pack, profile, routine (+ /health) |
| `/company-intel` | map, map/for-me, news, `company-intel/?city=`, profile, briefing, routine |
| `/practice` | today, history, challenges, settings, leaderboard, profile, briefing, routine |
| `/search` | **profile twice** (AppShell + `app/search/page.tsx:62`), briefing, routine |

### 1.4 API cold and warm timings

**Not measured.** The proxy refused both hosts (`connect_rejected`). To allow them, add `talentradar-api.onrender.com` and `talent-radar-ten.vercel.app` under Network access → Allowed domains in the cloud environment's settings (https://code.claude.com/docs/en/cloud-environments#network-access), then re-run section 2 of the brief. A ~40 s Render cold start is a hosting fact, not a frontend bug, and is left out of every finding below.

### 1.5 Code quality tools

| Tool | Result |
|---|---|
| `npm run lint` | **Does not run.** It is interactive because there is no `.eslintrc*` in `frontend/` (exit 1) |
| `next/core-web-vitals` rules, run ad hoc | 0 errors, 2 warnings: `react-hooks/exhaustive-deps` at `app/interview/page.tsx:309` and `app/settings/page.tsx:79` |
| `npx tsc --noEmit` | 0 errors |
| `ruff check .` | 0 errors |
| `mypy .` (strict) | **1,413 errors in 126 files**. 1,223 are in `tests/` (1,014 total are `no-untyped-def`). The 190 in application code are led by `type-arg` 59, `no-untyped-def` 35, `arg-type` 22, `no-any-return` 19 and `unused-ignore` 11 |
| mypy worst files (app code) | `storage/repository.py` 23, `storage/models.py` 17, `api/routers/resumes.py` 13, `domain/entities.py` 12, `services/llm.py` 7, `api/routers/search.py` 7 |
| mypy worst files (tests) | `tests/test_resume_matcher.py` 166, `tests/test_event_time_ical.py` 136, `tests/test_ingestion.py` 131 |
| `pytest -m "not slow"` | **6,232 passed, 4 skipped, 0 failed, 70 errors.** All 70 errors are `ConnectionRefusedError 127.0.0.1:5432`, in the `test_company_map_*` and `test_posting_company` modules that need Postgres, which CI provides. Run time 82 s with `-n 4` |

---

## 2. Findings, ranked

Each gain is an estimate unless it says "measured".

### F1. The landing and auth pages run an endless full-screen WebGL shader (high)

- **Where:** `components/landing/PrismGround.tsx:155-163` (the `tick` loop), the shader at `:25-70`, and `fit()` at `:143-147` (render size = section size × min(dpr, 1.5)). It is used on `app/page.tsx`, `app/login`, `app/signup`, `app/forgot-password`, `app/reset-password` and `app/unsubscribe`.
- **Impact:** The fragment shader evaluates per-pixel noise, `pow` and `sin` for every pixel on every frame, and it never stops: `breath` and `shimmer` keep `T` changing forever. The landing page mounts two instances (`app/page.tsx:96` and `:240`), each with its own WebGL context. An `IntersectionObserver` pauses whichever one is off screen. Measured on mobile: TBT 18.3 s on `/` and 4.7 s on `/login`. Those are the pages a new visitor sees first, and Google's lab data for them comes from the same kind of headless run. On real phones this costs battery and makes input lag, especially on low-end Android, the main audience for an India-focused site.
- **Fix (free):** (a) Draw one frame on mount. Start the loop only after `requestIdleCallback` or once the page is idle, so the first paint and hydration are not competing with it. (b) Stop the loop once the intro has settled (`T >= SETTLED_AT`) and leave the last frame on screen. If the shimmer has to stay, cap it at about 20–30 fps by skipping frames in `tick`. (c) Render at a fraction of the CSS size (for example `dpr = 0.5`) and let CSS scale the canvas up. The ribs are soft gradients plus grain, so the lower resolution will not be visible. (d) On the auth pages (the `across` variant), draw a single still frame.
- **Gain (measured proxy):** turning off the animation gives `/` mobile TBT 18.3 s → 0.9 s (score 59 → 75), and `/login` 4.7 s → 0.09 s (59 → 96). Stopping after the intro should get close to those numbers.

### F2. `LandingMotion` forces a layout reflow on every frame (medium)

- **Where:** `components/landing/LandingMotion.tsx:113-132`. `updateProgress()` calls `getBoundingClientRect()` on each `[data-progress]` element and writes `--p` right after each read, then `frame()` reads `hero`/`closer` rects again. It also calls `createLinearGradient` once per rib per frame.
- **Impact:** Read, write, read, write in one frame forces a synchronous layout for every tracked element, every frame, for as long as the visitor scrolls past the hero. This is most of the 918 ms TBT and the 1.0 s of Style & Layout that remain with reduced motion on.
- **Fix (free):** Do all the reads first, then all the writes. Better still, replace the per-frame measurement with an `IntersectionObserver` that gives each element its threshold progress, or CSS `animation-timeline: view()` where the browser supports it, falling back to the current values. Build the rib gradients once and move them with `ctx.translate`.
- **Gain:** roughly 0.3–0.6 s off landing TBT on mid-range mobile, and smoother scrolling.

### F3. Google Fonts load through a CSS `@import` chain (medium)

- **Where:** `app/globals.css:1` (Inter + JetBrains Mono) and `app/landing.css:1` (Instrument Serif).
- **Impact:** The browser finds the font stylesheet only after it has downloaded and parsed the 36 kB app CSS. That costs two extra connections (`fonts.googleapis.com`, then `fonts.gstatic.com`) on the critical path, and the requests run one after another. Inter is requested with the full `opsz` and `wght 400..700` axes, even though `--font-text` lists SF Pro and the system font first, so Apple devices fetch the CSS for a face they will never draw.
- **Fix (free):** Switch to `next/font/google` in `app/layout.tsx` and `app/page.tsx`. It self-hosts the files on Vercel at build time, preloads them, subsets to Latin and sets `size-adjust` fallbacks. Expose each family as a CSS variable and point `--font-*` at it. Drop the two `@import` lines.
- **Gain:** about 2 RTTs off first render on a cold mobile connection, roughly 150–400 ms of FCP on Indian 4G. It also removes a third-party origin.

### F4. ESLint is not configured, so lint never runs locally, in `next build` or in CI (medium, quality)

- **Where:** `frontend/` has no `.eslintrc*`. `package.json` defines `"lint": "next lint"`, which goes interactive.
- **Impact:** The React hooks rules and the Next rules (`no-img-element`, `no-sync-scripts`, and so on) are never enforced. The two missing-dependency warnings above have gone unnoticed. Today the debt is small, so this is the cheapest moment to turn lint on.
- **Fix (free):** Add `frontend/.eslintrc.json` containing `{"extends": "next/core-web-vitals"}`, fix or justify the two warnings, and add `npm run lint` to the frontend CI job.
- **Gain:** a guard against regressions. About 15 minutes of work.

### F5. `/company-intel` downloads MapLibre and tiles even when it opens on the List (medium)

- **Where:** `app/company-intel/page.tsx:335-336` mounts `<CompanyMap>` inside a `hidden={view !== 'map'}` section, and `components/CompanyMap.tsx:196` calls `loadMapLibre()` on mount. `CompanyMap` is also imported statically (`app/company-intel/page.tsx:6`), which puts its code and maplibre's 10 kB of CSS into the route's first load. That is part of why this route has the highest First Load JS, 140 kB.
- **Impact:** The page opens on the List whenever fewer than 40% of employers are on the map (`MAP_FIRST_SHARE = 0.4`, `:24`). For those visits, 279 kB gz of MapLibre, the worker, the style, the sprites, glyphs and vector tiles from OpenFreeMap are all fetched and a WebGL context is created, and none of it is shown.
- **Fix (free):** Mount `CompanyMap` the first time `view === 'map'`, then keep it mounted (`const [mapWanted, setMapWanted] = useState(false)`). Load it through `next/dynamic` with `ssr: false`, the same way `/events` loads `EventMap` (`app/events/page.tsx:30`).
- **Gain:** about 290 kB gz and 20–40 tile requests saved on List-first visits, plus about 10–15 kB off the route's First Load JS and CSS.

### F6. Root `globals.css` (36 kB gz) blocks rendering on the landing and public pages (medium)

- **Where:** `app/layout.tsx:4` imports `globals.css` for every route. The landing page (`landing.css`, `.lp` tokens) and the public pages (`public.css`) use only a small part of it.
- **Impact:** A stranger arriving on `/`, `/r/<slug>` or `/hiring/<slug>` downloads and parses 205 kB raw of app CSS before the first paint.
- **Fix (free):** Use route groups. Move the app pages under `app/(app)/layout.tsx`, which imports `globals.css`, and the landing and public pages under `app/(site)/layout.tsx`, which imports a small shared `base.css` (tokens, reset, theme). URLs do not change. As a cheaper first step, split out the sections tied to one feature (company map, practice, outreach, events) into CSS imported by that route's page.
- **Gain:** about 30 kB gz less render-blocking CSS on the pages that matter for SEO, roughly 100–250 ms of FCP on slow mobile.

### F7. On-demand ISR pages block on the sleeping API, and fail with a 500 when it is unreachable (medium)

- **Where:** `lib/public-api.ts:30-35` (`read()`: no timeout; non-404 errors are thrown), `app/r/[slug]/page.tsx:24-27`, `app/hiring/[slug]/page.tsx:15` (`generateStaticParams` returns `[]`, so every company page is built on its first request).
- **Impact:** `/r` and `/hiring` are ISR (`revalidate = 21600`, `dynamicParams = true`) with no client fetch, as intended (checked; `RoleViewBeacon` only sends an analytics beacon). For any slug that was not pre-rendered, though (every `/hiring/*` page, and any role newer than the last deploy), the first visitor waits while Vercel's function calls Render. When Render is asleep, that request waits about 40 s for the cold start and may run past the Hobby function time limit. When the read fails, the visitor gets an error page. Locally, with the API unreachable, `/r/<slug>` answered **500**. The "never waits on the API" promise holds only for revalidations. During `next build`, `publicRoleList(200)` also waits on a cold API, with no timeout.
- **Fix (free):** (a) Add `signal: AbortSignal.timeout(8000)` to `read()`. (b) On the first render of a slug, treat a timeout or 5xx as "temporarily unavailable". Render a small static shell with `noindex` and a short `revalidate` (for example 60 s) through `unstable_cache` or a segment-level `revalidate` fallback, so the visitor never sees a 500. (c) Pre-render the top companies in `/hiring` `generateStaticParams` from `publicRoleList`, which already returns `company` names, or from a public companies list. (d) Have the `news.yml` or `reverify.yml` workflow, which already wakes the API, call `GET /r/<slug>` for new slugs so the cold read happens on a cron rather than in front of a visitor.
- **Gain:** removes a possible 40 s wait or a 500 on the first view of every new public page, which are the pages search engines crawl first.

### F8. Three routes are server-rendered on every request without needing to be (low–medium)

- **Where:** `/practice/design/[slug]`, `/practice/challenges/[slug]` and `/roles/[id]` are marked ƒ in the build. They read `params`/`searchParams` in a server `page.tsx` and hand them to client components that fetch on mount anyway.
- **Impact:** Every view runs a Vercel serverless function. That costs a function cold start (about 200–800 ms added to TTFB) and counts toward Hobby's invocation and CPU quota. The server render has nothing to fetch.
- **Fix (free):** Make the shells static. Read the slug and `day` with `useParams()` and `useSearchParams()` inside the client view (wrapped in `<Suspense>`), or add `generateStaticParams` with `dynamicParams = true`. Both give a static or ISR shell.
- **Gain:** about 200–800 ms TTFB on a cold function, and fewer function invocations.

### F9. CORS preflights repeat every 10 minutes (low–medium)

- **Where:** `api/main.py:216-225`. `CORSMiddleware` has no `max_age`, so it uses Starlette's default of 600 s. `lib/api.ts:255-260` sends `Authorization` and `Content-Type: application/json` on every call, GETs included.
- **Impact:** The app (Vercel) and the API (Render, Singapore) are on different origins, so every authenticated request needs an `OPTIONS` preflight. One page load on `/practice` makes 8 reads, which means 8 preflights after any gap longer than 10 minutes. Each preflight adds one India↔Singapore round trip, roughly 60–120 ms, before its request can leave.
- **Fix (free):** Set `max_age=7200` (Chrome's cap) on `CORSMiddleware`. In `send()`, set `Content-Type` only when there is a body. That does not avoid the preflight, because the `Authorization` header still triggers one, but it is cleaner. A larger option is to proxy `/api/*` through a Vercel rewrite so calls become same-origin. Weigh that carefully: rewrites count against Hobby's edge request quota, and a long cold start through a rewrite can hit its timeout.
- **Gain:** one RTT less on the first request per origin after idle, roughly 60–120 ms per page in the common case.

### F10. Authenticated pages wait for two renders before they fetch (low)

- **Where:** `components/RequireAuth.tsx:22-30`. The token check runs in `useEffect`, so the first client render is "Loading…". Only after the second render do the page's `useEffect` fetches start. Every page under it is a `'use client'` page whose data reads also start in `useEffect`.
- **Impact:** A small extra delay on every navigation. On a cold Render instance it is negligible next to the 40 s wake, which `ApiWarmup` already starts at mount.
- **Fix (free):** Leave the hydration fix as it is. Start the reads at module scope or in a tiny shared store primed as the page chunk evaluates, the same pattern `lib/day-line.ts` uses for the briefing, so the requests are in flight while React finishes rendering.
- **Gain:** about 16–50 ms per navigation. Low priority.

### F11. Duplicated and dead code (low, quality)

- `components/CompanyMap.tsx:62` and `components/EventMap.tsx:31` both define `pinLayers()`. The two are nearly identical: the same `token()` reads, the same `radius`/`hover` helpers and the same solid and ring layers, differing only in ids, source names and the event `fade`. Move a `basePinLayers({ prefix, source, extraPaint })` into `lib/maplibre.ts`.
- `components/overview/Charts.tsx`: `StackBar` (`:11`), `Ring` (`:29`) and `Sparkline` (`:49`) are exported but never imported. Only `ScoreBars` is used (`app/dashboard/page.tsx:11`). Their CSS block at `app/globals.css:2578` is dead too.
- `lib/api.ts:112`: the `ActivityItem` type is never used. `lib/api.ts:104` is a stray doc comment ("A `city` row the server placed near the centre…") left above `isPinned`'s real comment. It describes behaviour that was retired on 2026-11-03.
- `app/layout.tsx:15-34` and `app/public.css:5` describe "Big Shoulders Text / Fragment Mono / Public Sans" and "amber". The CSS now uses SF Pro / Inter / JetBrains Mono and azure plus salmon. These stale design-brief comments ship with the source and mislead readers.
- `/search` reads `GET /profile/` itself (`app/search/page.tsx:62`) while `AppShell` (`components/AppShell.tsx:264`) reads the same thing. Expose the shell's profile through a small store, as with the briefing, or have the search page read the cached name and target roles first.

### F12. Oversized single-file client pages (low, quality)

- `app/company-intel/page.tsx` is 1,263 lines, `app/events/page.tsx` 1,109, `app/interview/page.tsx` 746, `components/outreach/ThreadDrawer.tsx` 668, `app/resume-studio/page.tsx` 621 and `app/dashboard/page.tsx` 610. Each is one `'use client'` module, so the whole file is shipped and hydrated together. `/company-intel` (19.9 kB) and `/outreach` (15.6 kB) are the largest route chunks for this reason.
- **Fix (free):** Split the drawers, sheets and dialogs that open on demand into their own files and `next/dynamic` them: the company drawer, the event drawer and people-met, `ThreadDrawer`, `DraftPanel`. The rules already sit in pure `lib/*.ts`, so this is a mechanical move.
- **Gain:** about 5–10 kB gz off the two heaviest routes' first load, and smaller files to review.

### F13. mypy strict is not a gate, and 190 errors sit in application code (low–medium, quality)

- **Where:** `.github/workflows/deploy.yml:43` (`mypy . || true`). The CLAUDE.md says new code "should carry full type annotations", and nothing enforces it.
- **Impact:** Real type holes hide among the noise in the files with the most errors: `storage/repository.py` (23) and `storage/models.py` (17), which are the persistence layer, and `api/routers/resumes.py` (13). The 11 `unused-ignore` errors are suppressions left behind after the problem they covered was fixed.
- **Fix (free):** Make application code gate first. Add a `[[tool.mypy.overrides]] module = "tests.*"` with `disallow_untyped_defs = false` (most of the 1,223 test errors go away), fix the 190 in application code starting with `storage/`, then drop `|| true`. As an alternative, ratchet: fail CI when the error count goes up.
- **Gain:** type errors caught before deploy. The 11 `unused-ignore` errors take about 10 minutes.

---

## 3. What is already right (verified)

- MapLibre is dynamically imported (`lib/maplibre.ts:61`) and pointed at a self-hosted worker. `/events` loads `EventMap` with `next/dynamic` and has a failure fallback.
- Excalidraw is route-only on `/practice/design/[slug]`, with `ssr: false`, its fonts served from `/excalidraw/` and no CDN (`app/practice/design/[slug]/page.tsx:43-48`). Its mermaid and font-subsetting chunks are never fetched.
- `/r/[slug]` and `/hiring/[slug]` are ISR with no client data fetch, and `ApiWarmup` stays quiet on them.
- One briefing read is shared between the Overview and the day line. Page reads run in parallel, with no serial `await` chains in `lib/api.ts`.
- The basemap is OpenFreeMap (no key, free). Analytics is a `sendBeacon` to PostHog only when a key is set, with no SDK.
- CLS is about 0 on every page measured. `tsc` and `ruff` are clean.

## 4. Cost flags (zero-cost rule)

Nothing in this report needs a paid plan. Points to watch:
- **Vercel Hobby quotas:** the ƒ routes (F8) and any `/api` rewrite (F9) use function invocations and edge requests. F8 reduces that use; F9's rewrite option increases it. Prefer `max_age`.
- **ISR on Hobby:** each revalidation is a function run. At 21,600 s × (≤200 roles + companies) this is well inside the quota. Do not lower `REVALIDATE_SECONDS` sharply.
- **Deployment size:** `public/excalidraw` (14 MB of fonts) is uploaded with every deploy. It is inside Hobby's limits, but if size becomes an issue, copy only the font families the whiteboard actually uses.
- **PostHog:** free tier. Events are sent only with `NEXT_PUBLIC_POSTHOG_KEY`. Keep it without a card so going over the free quota stops collection instead of billing.

## 5. Quick wins (each under an hour, highest gain first)

1. **Stop the PrismGround loop after the intro, and draw a still frame on the auth pages** (F1). Measured potential: mobile TBT 18.3 s → about 0.9 s on `/`, and 4.7 s → 0.09 s on `/login`.
2. **Add `frontend/.eslintrc.json` with `next/core-web-vitals`** and fix the 2 warnings (F4).
3. **Move to `next/font/google`** and delete the two `@import` lines (F3).
4. **Mount `CompanyMap` only when the Map view is chosen, via `next/dynamic`** (F5).
5. **`AbortSignal.timeout` in `lib/public-api.ts::read`** (F7a).
6. **`max_age=7200` on `CORSMiddleware`** (F9).
7. **Delete the dead `Charts` exports, the `ActivityItem` type and the stale layout and API comments** (F11).

## Method and reproducibility

- Frontend: `cd frontend && npm ci && npm run build`. Chunk sizes come from `.next/react-loadable-manifest.json` and `.next/app-build-manifest.json`, measured with `gzip -9`.
- Lighthouse: `npx lighthouse http://127.0.0.1:3100/<path> [--preset=desktop] --only-categories=performance`, served by `node .next/standalone/server.js` (after copying `public/` and `.next/static/` beside it). The reduced-motion runs add `--chrome-flags=--force-prefers-reduced-motion`.
- Requests: Playwright with a stub JWT in `localStorage`, recording every request to the API origin.
- ESLint (ad hoc): `npx eslint --no-eslintrc -c <tmp config extending next/core-web-vitals> --ext .ts,.tsx app components lib`. The temporary config was deleted afterwards.
- Backend: `pip install -e .[dev]`, then `ruff check .`, `mypy .`, and `pytest -m "not slow" -n 4` with `GROQ_API_KEY`, `TAVILY_API_KEY` and `JWT_SECRET_KEY` set as in `deploy.yml` and no Postgres running.
- Live measurements (Lighthouse on Vercel, Render cold and warm timing) need the two hosts allowed in the environment's network policy.
