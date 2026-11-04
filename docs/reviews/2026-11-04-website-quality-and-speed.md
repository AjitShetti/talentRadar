# Website quality and speed audit — 2026-11-04

Scope: the Next.js 14 frontend in `frontend/`, plus the backend's lint, type and test status. Audited at `main` `dbe4c76`. Backend capacity and reliability are covered by a separate review.

Everything recommended here is free: no paid plan, service or add-on. Cost watch-outs are marked **cost**.

## How this was measured, and what could not be

- **Build:** `npm ci && npm run build` in `frontend/` (Next 14.2.35, Node 22). It succeeded.
- **The live site could not be reached.** This audit ran in a sandboxed cloud container, and its egress proxy refused `CONNECT` to both `talent-radar-ten.vercel.app` and `talentradar-api.onrender.com` (HTTP 403 from the proxy, not from Vercel or Render). So **no live Lighthouse numbers and no API cold/warm timings** are in this report. Re-run section 2 from a normal network (commands in the appendix).
- **Local stand-in:** Lighthouse 12.6.1 (headless Chromium, default throttling, mobile and `--preset=desktop`) was run against `next start` on the production build. A stub API on `:8000` served one fictional public role so `/r/[slug]` could render. Caveats:
  - Localhost has no network latency, so FCP and LCP are optimistic compared with Vercel.
  - Headless Chromium has no GPU, so WebGL runs on the CPU (SwiftShader). That makes the landing page's TBT larger than a phone with a GPU would see. Finding 1 still stands on a real phone, for the reasons given there.

## 1. Measured numbers

### Build output (First Load JS, as `next build` reports it)

| Route | Route JS | First Load JS |
|---|---:|---:|
| **Shared by all** | | **90.2 kB** (`fd9d1056…` 53.6 kB, `2117…` 31.9 kB, other 4.7 kB) |
| `/company-intel` | 19.9 kB | **140 kB** |
| `/outreach` | 15.6 kB | **140 kB** |
| `/dashboard` | 9.46 kB | 132 kB |
| `/events` | 11.4 kB | 131 kB |
| `/practice` | 7.13 kB | 131 kB |
| `/practice/challenges/[slug]` | 6.91 kB | 130 kB |
| `/interview` | 9.55 kB | 129 kB |
| `/roles/[id]` | 3.68 kB | 128 kB |
| `/resume-studio` | 7.0 kB | 127 kB |
| `/search`, `/settings` | 5.8–5.9 kB | 126 kB |
| `/practice/design/[slug]` | 5.3 kB | 125 kB (Excalidraw is lazy, see below) |
| `/applications` | 3.86 kB | 124 kB |
| `/agent` | 0.5 kB | 123 kB |
| `/` (landing) | 7.88 kB | 116 kB |
| `/onboarding`, `/login`, `/signup`, `/reset-password`, `/forgot-password`, `/unsubscribe` | 6.4–7.9 kB | 114–116 kB |
| `/check` | 2.1 kB | 110 kB |
| `/r/[slug]` (SSG/ISR) | 1.67 kB | 101 kB |
| `/hiring/[slug]` (SSG/ISR) | 0.2 kB | 99.2 kB |

**JavaScript is lean.** No route is over 140 kB First Load, and the two heavy libraries load only when needed:

| Lazy chunk | Raw | gzip | Loaded by |
|---|---:|---:|---|
| Excalidraw (57 lazy chunks) | ≈2.8 MB | ≈925 kB | `/practice/design/[slug]` only, through `next/dynamic` with `ssr: false` (`app/practice/design/[slug]/page.tsx:44-48`) ✅ |
| harfbuzz/wasm chunk (`120a652c…`, most likely Excalidraw's font subsetter) | 1.82 MB | 740 kB | lazy, on demand only; not in any route's First Load |
| Excalidraw CSS (`008289500c…css`) | 145 kB | 22.7 kB | the design route only |
| MapLibre JS (`ca4dcb09…`) | 592 kB | 149 kB | dynamic `import()` in `lib/maplibre.ts` (`loadMapLibre`) ✅ |

**CSS is the heavy part.** None of it shows in First Load JS:

| Stylesheet | Raw | gzip | Where | Render-blocking |
|---|---:|---:|---|---|
| `globals.css` → `73b25d46…css` | **205 kB** | **36.2 kB** | **every route**, including landing, `/login` and `/r` | yes |
| `company-intel` + maplibre CSS → `8933483d…css` | 83 kB | 10.5 kB | `/company-intel`, loaded eagerly | yes |
| `landing.css` → `e8e4d00e…css` | 23 kB | 5.9 kB | `/` | yes |
| `public.css` → `76df235e…css` | 6.7 kB | 1.7 kB | `/r`, `/hiring` | yes |
| Google Fonts CSS (`@import` in `globals.css:1` and `landing.css:1`) | — | — | every route | yes, and chained (see finding 2) |

HTML documents (gzip): `/` 15.3 kB, `/login` 6.1 kB, `/r/…` 7.2 kB, `/dashboard` 5.5 kB. `/dashboard` is a "Loading…" shell until JS runs and auth resolves.

### Lighthouse (local production build; see caveats above)

| Page | Form | Score | FCP | LCP | TBT | CLS | Transfer |
|---|---|---:|---:|---:|---:|---:|---:|
| `/` landing | mobile | **50** | 3.14 s | 3.71 s | **7,563 ms** | 0.000 | 256 KiB |
| `/` landing | desktop | 68 | 0.62 s | 0.69 s | **8,466 ms** | 0.009 | 268 KiB |
| `/login` | mobile | **60** | 1.98 s | 2.94 s | **5,223 ms** | 0.000 | 207 KiB |
| `/login` | desktop | 99 | 0.54 s | 0.69 s | 32 ms | 0.004 | 207 KiB |
| `/r/[slug]` | mobile | 94 | 2.07 s | 2.72 s | 52 ms | 0.000 | 216 KiB |
| `/r/[slug]` | desktop | 100 | 0.59 s | 0.71 s | 0 ms | 0.000 | 224 KiB |
| `/` landing, reduced motion forced | mobile | 82 | — | 3.53 s | **0 ms** | 0.000 | — |
| `/login`, reduced motion forced | mobile | 91 | — | 2.94 s | 128 ms | 0.000 | — |

- Mobile, every page: Lighthouse flags the Google Fonts CSS (181–919 ms) and `globals.css` (454–606 ms) as render-blocking. It also reports ≈35 KiB of unused CSS on every page.
- Landing mobile: 12.9 s of main-thread time is "Other" (GPU work run on the CPU), all attributed to the chunk that holds `components/landing/PrismGround.tsx`. Forcing `prefers-reduced-motion`, which stops that loop, takes TBT from 7.6 s to 0.

### API timings

Not measured, because the API was unreachable from the audit container (see above). For context, `.github/workflows/keepalive.yml` already pings `/health` every 10 minutes to keep Render awake, and `components/ApiWarmup.tsx` tells the user honestly when the API is cold. A cold start is not counted as a frontend bug here.

### Lint, types and tests

| Check | Result |
|---|---|
| `npm run lint` | **Does not run.** No ESLint config is committed, so `next lint` stops at its interactive "How would you like to configure ESLint?" prompt (exit 1). Linting has never been enforced. |
| `next lint` with a temporary `{"extends":"next/core-web-vitals"}` (not committed) | 0 errors, **2 warnings**: `react-hooks/exhaustive-deps` at `app/settings/page.tsx:79` and `app/interview/page.tsx:309`. 4 `eslint-disable` comments in the source. |
| `npx tsc --noEmit` | **0 errors** ✅ |
| `ruff check .` | **All checks passed** ✅ |
| `ruff format --check .` | 181 files would be reformatted (259 already formatted). `make format` exists, but neither CI nor the hook keeps it applied. |
| `mypy .` (strict, `.[dev,lint]` installed) | **1,411 errors in 125 files.** 1,223 are in `tests/` (mostly `no-untyped-def`). **188 are in application code**: `api` 67, `storage` 42, `ingestion` 25, `services` 16, `domain` 13, `agents` 11, `ml` 6, `scripts` 3. Main kinds there: `type-arg` 59, `no-untyped-def` 35, `arg-type` 22, `no-any-return` 19, `unused-ignore` 11. Worst files: `storage/repository.py` (22), `storage/models.py` (17), `domain/entities.py` (12). CI runs it as `mypy . \|\| true` (`.github/workflows/deploy.yml:43`), so it never fails a build. |
| `pytest -m "not slow"` | **6,232 passed, 4 skipped, 70 errors** in 335 s. All 70 errors are `ConnectionRefusedError 127.0.0.1:5432`: `test_company_map_store.py` (19), `test_company_map_new_sources_refresh.py` (25), `test_company_map_refresh.py` (11), `test_company_map_news_ingest.py` (5), `test_company_map_people.py` (5), `test_posting_company.py` (3), `test_company_map_hiring_list.py` (2). They need a live Postgres but are not marked `slow`/integration. They pass in CI, which has a Postgres service. No assertion failures. Note: without `GROQ_API_KEY` and `JWT_SECRET_KEY` set, the suite cannot even import `conftest.py`. |

## 2. Findings, ranked

Each finding gives the impact, a free fix and an estimated gain. The gains are estimates from the lab numbers above, not guarantees.

### 1. The landing and sign-in prism runs a full-section WebGL shader every frame, forever — High

- **Where:**
  - `components/landing/PrismGround.tsx:86` (context)
  - `PrismGround.tsx:144-148`: the canvas is sized to the whole section at up to 1.5× DPR, including the tail below the first screen.
  - `PrismGround.tsx:155-163`: an rAF loop with no end.
- **Used by:** `app/page.tsx`, `app/login`, `app/signup`, `app/forgot-password`, `app/reset-password`, `app/unsubscribe`.
- **Impact:**
  - Lab TBT is 7.6 s on landing mobile and 5.2 s on `/login` mobile, which caps the Lighthouse score at 50–60.
  - With reduced motion forced, TBT is 0 ms, which pins the cost on this loop.
  - On a real phone with a GPU the main-thread cost is smaller. But the shader still shades every pixel of a canvas that can be taller than the viewport, 60 times a second, while the page is open. That costs battery and heat, competes with input (INP), and on low-end Android it is what makes the sign-in form feel sluggish.
- **Free fix:** do any or all of these:
  1. Stop the loop once the intro settles (`SETTLED_AT`), and redraw only on scroll or resize. The design already says it "keeps slowly widening", so a 10–15 fps tick after settling would keep that.
  2. Size the canvas to the viewport, not the section, and move or clip it with CSS.
  3. Cap DPR at 1, and use 0.75 on `pointer: coarse` or small screens. The ribs are 4 px or wider, so the loss of resolution won't show.
  4. Start the loop after first paint (`requestIdleCallback` or after `load`), so it never competes with LCP or hydration.
  5. On `/login` and `/signup`, draw one still frame. Nobody watches the prism while typing a password.
- **Estimated gain:** TBT from about 5–8 s to under 200 ms in the lab. Lighthouse mobile about +30 (landing) and +30 (login). This is the largest single gain available.

### 2. Fonts load through a chained, render-blocking Google Fonts `@import` — High

- **Where:** `app/globals.css:1` (Inter and JetBrains Mono), `app/landing.css:1` (Instrument Serif).
- **Impact:** the browser has to fetch `globals.css` (36 kB gzip), then the Google CSS from a new origin, then the woff2 files from a third origin (`fonts.gstatic.com`). That is three sequential round trips before text renders in its font. Lighthouse puts 0.2–0.9 s of render-blocking time on mobile on the Google CSS alone, on every page.
- **Free fix:** move to `next/font/google` in `app/layout.tsx` (and in `app/page.tsx` for Instrument Serif). It self-hosts the files on Vercel at build time, preloads them and adds a size-adjusted fallback. Expose each font as a CSS variable and replace the `font-family` names in the CSS. Keep `display: 'swap'`. JetBrains Mono needs only weights 400 and 500.
- **Estimated gain:** about 0.3–0.9 s off FCP and LCP on mobile, on every route. It also removes two third-party connections and the visitor-IP exposure to Google.

### 3. One 205 kB global stylesheet blocks the first paint of every route — High

- **Where:** `app/globals.css` (2,776 lines, 231 kB source, 205 kB built, 36.2 kB gzip), imported by `app/layout.tsx:4`.
- **Impact:**
  - The landing page, `/login` and the public `/r` and `/hiring` pages download and parse the whole application's styles before first paint: the board, Outreach, Events, Practice, the copilot, the Excalidraw overrides and the maplibre overrides.
  - Lighthouse reports ≈35 KiB of unused CSS on each of those pages, and 454–606 ms of render-blocking time on mobile.
  - A rough scan found **171 of 881 class names in `globals.css` that appear nowhere in `app/`, `components/` or `lib/`**, after removing names that belong to Excalidraw or MapLibre. Examples: `action-card`, `dashboard-grid`, `dayboard*`, `db-row`, `agent-grid`, `bottom-grid`, `chip-good`/`chip-bad`, `cp-stats`, `content-wrap`.
- **Free fix:**
  1. Delete the dead rules, after checking each by hand, because some class names are built at runtime.
  2. Split the CSS by feature and import each part from its own route or component, the way `landing.css` and `public.css` already are. Candidates: `outreach.css`, `events.css`, `practice.css`, `company-intel.css`, `copilot.css`.
  3. Keep in `globals.css` only the tokens, both themes, the base styles, `AppShell` and the shared controls.
  4. Move the Excalidraw and `.maplibregl` overrides next to `DesignBoard.tsx` and the map components.
- **Estimated gain:** global CSS from 36 kB to about 10–15 kB gzip. About 0.2–0.4 s less render-blocking on mobile for landing, `/login`, `/r` and `/hiring`.

### 4. `/company-intel` loads the map's CSS and component eagerly — Medium

- **Where:** `app/company-intel/page.tsx:6` (`import CompanyMap from '@/components/CompanyMap'`); `components/CompanyMap.tsx:6` (`import 'maplibre-gl/dist/maplibre-gl.css'`).
- **Impact:**
  - The Events page already gets this right with `next/dynamic` (`app/events/page.tsx:29`). On Company Intel, the 83 kB raw / 10.5 kB gzip stylesheet blocks render, and `CompanyMap` (432 lines) is part of the route's 19.9 kB, the largest route chunk.
  - The page also opens on the List while under 40% of employers are pinned (per CLAUDE.md), so many visits never show the map at all.
- **Free fix:** load `CompanyMap` with `dynamic(() => import('@/components/CompanyMap'), { ssr: false, loading: … })`, the same way `EventMap` is loaded. Its CSS then travels with the chunk.
- **Estimated gain:** about −5 to −8 kB of route JS and 10.5 kB less render-blocking CSS on `/company-intel`. Faster first render on the List view.

### 5. `npm run lint` is broken and the frontend has no CI gate — Medium (quality)

- **Where:** no `frontend/.eslintrc.json`. `.github/workflows/deploy.yml` has only Python jobs.
- **Impact:** lint has never run, so warnings like the two `exhaustive-deps` above build up unseen. A TypeScript or build break on the frontend shows up only on Vercel's deploy.
- **Free fix:**
  1. Commit `frontend/.eslintrc.json` with `{"extends": "next/core-web-vitals"}`. It is clean today apart from the two warnings.
  2. Add a `frontend` job to `deploy.yml`: `npm ci && npm run lint && npx tsc --noEmit && npm run build`.
- **Cost:** GitHub Actions minutes are free on a public repository. On a private one this job would use about 3–4 minutes per push, which fits the free 2,000 minutes a month at the current push rate. Watch it if the repository goes private.
- **Estimated gain:** catches regressions before Vercel does, and makes `/practice/design` keeping `ssr: false` and the route split checkable.

### 6. mypy strict is advertised but not enforced: 188 errors in application code — Medium (quality)

- **Where:** `.github/workflows/deploy.yml:43` (`mypy . || true`). The worst files are `storage/repository.py` (22), `storage/models.py` (17), `domain/entities.py` (12) and `services/llm.py` (7).
- **Impact:** CLAUDE.md says "mypy runs in strict mode", but no build has ever failed on it. In application code there are 22 `arg-type`, 19 `no-any-return` and 8 `union-attr`/`attr-defined` errors. Those are the kinds that hide real `None` bugs.
- **Free fix:**
  1. Fix application code first: 188 errors, many of them mechanical `type-arg` fixes (adding `dict[str, Any]`).
  2. Add a `[[tool.mypy.overrides]] module = "tests.*"` that relaxes `disallow_untyped_defs`. That removes about 1,000 of the test errors in one line.
  3. Then drop `|| true`.
  4. Remove the 5 unused `[[tool.mypy.overrides]]` sections the run reports: `apache_airflow`, `ragas`, `scrapling`, `slowapi` and `tavily`.
- **Estimated gain:** a gate that means something, at no runtime cost.

### 7. A signed-in visitor to `/` loads the whole landing page, then redirects — Medium

- **Where:** `components/landing/SignedInRedirect.tsx` (redirects in `useEffect`), mounted at `app/page.tsx:86`.
- **Impact:** a returning user typing the bare domain (the common case, according to the component's own comment) downloads the 116 kB landing JS, the landing CSS and the fonts, and starts the WebGL prism (finding 1). They see the hero flash, and only after hydration go to `/dashboard`, which then hydrates again.
- **Free fix:** use an inline `<script>` in the landing page's `<head>`, in the same pattern as `THEME_SCRIPT`, that checks the `talentradar_token` key and calls `location.replace('/dashboard')` before first paint. Keep `SignedInRedirect` as the fallback. Avoid the Edge-middleware alternative: it would count against Vercel Hobby's edge request quota for a job a 100-byte script does for free.
- **Estimated gain:** about 0.5–1.5 s faster to the board for every returning user on mobile. No landing download, and no prism start.

### 8. ISR pages: the first build of an unbuilt slug waits on a cold API, with no timeout — Medium

- **Where:** `lib/public-api.ts:31` (`fetch` with no `AbortSignal`); `app/hiring/[slug]/page.tsx:15` (`generateStaticParams() { return [] }`); `app/r/[slug]/page.tsx:24-27`.
- **What already works:** both routes are SSG/ISR (`revalidate = 21600`, `dynamicParams = true`), with no client-side fetch. `RoleViewBeacon` only calls `track()`, which does nothing without a key. Once a page is built it never waits on the API, as CLAUDE.md requires.
- **Impact:**
  - **Every** `/hiring/*` page, and any `/r/*` slug newer than the last deploy, is built on its first request. That request blocks on Render. If the API is asleep, the visitor (often a crawler following `sitemap.xml`) waits through the ≈40 s cold start or gets a function timeout.
  - The same applies to `generateStaticParams` during a Vercel build. If the API is slow, the build waits. If it errors, the list is empty and every role page is built on demand.
- **Free fix:**
  1. Add `signal: AbortSignal.timeout(8000)` to `read()`. A timed-out revalidation then keeps the stale page, which the code already relies on.
  2. Pre-build `/hiring` from the same list (`publicRoleList` already carries `company`).
  3. Optionally, `fetch(API_URL + '/health')` in a `prebuild` step to wake Render before the build reads it.
- **Cost:** keep `REVALIDATE_SECONDS` at 6 h or longer, because ISR writes and function invocations count against Vercel Hobby's free limits.
- **Estimated gain:** no 40 s first loads or timeouts for crawlers on new public pages.

### 9. The app shell remounts on every navigation — Low/Medium

- **Where:** each page wraps itself in `<RequireAuth><AppShell>`, 14 pages in all (for example `app/dashboard/page.tsx:389` and `app/events/page.tsx:128`). There is no shared `layout.tsx` for the signed-in routes.
- **Impact:**
  - On every client-side navigation, the nav, the avatar menu, `DayLine` (a new canvas, `ResizeObserver` and rail engine) and `Tour` unmount and mount again.
  - The auth gate goes back to "Loading…" for a frame.
  - The duplicate reads are mostly avoided already: the name is cached in `localStorage` (`components/AppShell.tsx:259-269`), and the briefing and routine through the day-line store's freshness checks. But the remount costs work, and it adds a flash between pages.
- **Free fix:** move the signed-in pages into a route group, `app/(app)/…`, whose `layout.tsx` renders `<RequireAuth><AppShell>{children}</AppShell></RequireAuth>` once. The URLs do not change. `AppShell narrow` and `bleed` can become a prop read from a small per-route config, or from `usePathname`.
- **Estimated gain:** about 20–60 ms less work per navigation, no loading flash between pages, and simpler page files.

### 10. Very large client components — Low/Medium (quality, and route JS)

- **Where:**
  - `app/company-intel/page.tsx`: 1,263 lines, 63 kB, 32 `useState`, 12 `useEffect`.
  - `app/events/page.tsx`: 1,109 lines, 53 kB, 35 `useState`.
  - `components/outreach/ThreadDrawer.tsx`: 668 lines, 18 API calls.
  - `app/interview/page.tsx`: 746 lines.
  - `app/resume-studio/page.tsx`: 621 lines.
  - `app/dashboard/page.tsx`: 610 lines.
  - `lib/api.ts`: 565 lines and 61 kB. It holds every type and every endpoint, and it is `'use client'`.
- **Impact:** hard to review and test. Every page ships the whole of its own file. The drawers (company detail, event detail, add-person forms) are in the route chunk even when no drawer is opened.
- **Free fix:**
  - Split each page into the list, the drawer and the forms.
  - `dynamic()`-import the drawers, which open only after a click.
  - Move the types out of `lib/api.ts` into `lib/api-types.ts`, without `'use client'`, so server code such as `lib/public-api.ts` can import them without touching a client module.
- **Estimated gain:** about −5 to −10 kB of route JS on `/company-intel`, `/events` and `/outreach`. Much better reviewability.

### 11. Duplicated helpers — Low (quality)

- **IST arithmetic** is defined at least 6 times:
  - `lib/outreach.ts:36,74`
  - `lib/routine.ts:49`
  - `lib/interview-time.ts:7`
  - `lib/day-line.ts:14,19`
  - `lib/event-time.ts:9,24`
  - `lib/practice-time.ts`

  `'+05:30'` is also typed by hand in `lib/outreach-met.ts:60` and `app/events/page.tsx:1078`. Each copy writes the offset differently (`5.5 * 3_600_000`, `330 * 60_000`, `(5 * 60 + 30) * 60 * 1000`), so a change has to be made in six places.
- **`messageOf(err, fallback)`** is copied verbatim into 6 files: `app/outreach/page.tsx:23`, `components/outreach/TodayPack.tsx:13`, `DraftPanel.tsx:43`, `CompanyOutreachBlock.tsx:9`, `MetPersonImport.tsx:29` and `ThreadDrawer.tsx:41`.
- **`capitalise`** is copied into 3 Practice components: `ContestSection.tsx:17`, `TopicBars.tsx:5` and `TodayCard.tsx:11`.
- **`hostOf`** appears twice (`app/company-intel/page.tsx:89`, `components/CopilotWorkspace.tsx:37`), and so does **`initials`** (`app/company-intel/page.tsx:65`, `lib/outreach.ts:179`).
- **The two maps share most of their setup:** `pinLayers` (`components/CompanyMap.tsx:62`, `components/EventMap.tsx:31`), and `clusterSize` (`CompanyMap.tsx:56`, `lib/event-map.ts:251`).
- **`app/interview/page.tsx:150-153`** repeats `refreshHistory` (`:173-176`) instead of calling it.
- **Free fix:**
  - Put the IST helpers in one `lib/ist.ts`.
  - Put `messageOf` and the string helpers in one `lib/text.ts`.
  - Move the shared map code (pin layers, cluster sizing, the source and cluster setup) into `lib/maplibre.ts`.
- **Estimated gain:** about 150–250 fewer lines, and one place to fix each rule.

### 12. Dead assets and stale comments — Low

- **Dead assets:** `public/hero.jpg` (456 kB), `public/screenshot-resume.jpg` (492 kB) and `public/screenshot-search.jpg` (406 kB), 1.35 MB in all, are not referenced anywhere in the repository. They are not downloaded by any page, so there is no speed cost, but they add to every clone and to the Vercel upload. Delete them, or use them in the manifest's `screenshots`.
- **Stale comment:** the long comment in `app/layout.tsx:11-28` describes "Big Shoulders Text, Fragment Mono, Public Sans" and an "amber" accent. The CSS loads Inter, JetBrains Mono and Instrument Serif, and the accent is azure. Correct or remove it so the fonts are not changed against an outdated note.
- **Two `exhaustive-deps` warnings:**
  - `app/settings/page.tsx:79` lists 9 setters as missing. They come from `usePersistentState`, so check that they are stable and then list them.
  - `app/interview/page.tsx:309` is missing `setError`.

### 13. Backend test hygiene — Low (quality)

- **DB-only tests aren't marked:** the 70 tests that need a live Postgres (listed in section 1) carry no marker, so `make test` (`-m "not slow"`) fails on any machine without Docker running. Mark them `@pytest.mark.integration`, or skip them when `5432` is closed.
- **Settings need dummy keys:** `config/settings.py` requires `GROQ_API_KEY` and `JWT_SECRET_KEY` even to import `tests/conftest.py`. Set test defaults in `conftest.py` (`os.environ.setdefault(...)`) so `pytest` runs out of the box.
- **Formatting:** 181 files fail `ruff format --check`. Run `make format` once in its own commit, then add `ruff format --check .` to the `lint` job.
- **Unused image build (CI waste, no cost):** `deploy.yml`'s `build` job pushes an API image to GHCR on every push to `main`, but Render builds from the repository itself (see the comment at `deploy.yml:147-162`). GHCR is free for public packages, so this costs nothing, but it adds a few minutes of CI to every push for an image nothing pulls. Consider removing it.

### What is already right, and should stay that way

- Excalidraw is route-only and `ssr: false` (`app/practice/design/[slug]/page.tsx:44-48`). Its fonts are served from `public/excalidraw/`, never a CDN.
- MapLibre JS is a dynamic import (`lib/maplibre.ts`), the worker is self-hosted, and `EventMap` is `next/dynamic`.
- `/r/[slug]` and `/hiring/[slug]` are ISR with no client fetch. `public-api.ts` runs only on the server.
- The Overview and the day line share one briefing read: `app/dashboard/page.tsx:350` publishes the briefing, and `components/DayLine.tsx:100` skips its own read on the Overview.
- The Overview's four reads run in parallel (`app/dashboard/page.tsx:338-348`), and so do `/practice`'s five (`Promise.allSettled`, `app/practice/page.tsx:54`). No serial request waterfalls were found.
- CLS is 0 to 0.009 on every page measured. `tsc` and `ruff check` are clean.
- `keepalive.yml` pings only `/health`, so it never wakes Neon. Its free-tier reasoning, 750 Render hours and public-repository Actions minutes, is documented and correct.

## 3. Quick wins

Each is under an hour, free, and needs no design decision.

1. **Pause the prism** (finding 1). Stop the loop after `SETTLED_AT`, cap DPR at 1, and draw a still frame on the sign-in pages. **Biggest gain: lab TBT from 5–8 s to under 0.2 s, Lighthouse mobile +30.**
2. **Use `next/font/google`** for Inter, JetBrains Mono and Instrument Serif, and delete the two `@import` lines (finding 2). About −0.3 to −0.9 s mobile FCP and LCP on every page.
3. **Load `CompanyMap` with `dynamic(..., { ssr: false })`**, as `EventMap` is (finding 4).
4. **Commit `frontend/.eslintrc.json`** (`next/core-web-vitals`) and add a frontend CI job with lint, `tsc` and build (finding 5).
5. **Add a timeout to `public-api.ts`'s `fetch`**: `AbortSignal.timeout(8000)` (finding 8).
6. **Add the inline redirect script** for signed-in visitors to `/` (finding 7).
7. **Delete the 1.35 MB of unreferenced images** in `public/`, and fix the stale font comment in `app/layout.tsx` (finding 12).
8. **Relax mypy for `tests.*`** and remove the 5 unused override sections, so the 188 application-code errors become visible (finding 6).

## Appendix: reproduce

```bash
# frontend
cd frontend && npm ci && npm run build           # route table above
npx tsc --noEmit
echo '{"extends":"next/core-web-vitals"}' > .eslintrc.json && npx next lint   # until committed

# live Lighthouse (run from a normal network; the audit container could not reach Vercel)
npx lighthouse https://talent-radar-ten.vercel.app/ --only-categories=performance            # mobile
npx lighthouse https://talent-radar-ten.vercel.app/ --only-categories=performance --preset=desktop
# repeat for /login and one /r/<slug> from /sitemap.xml

# API cold vs warm: the first call after >15 min idle is cold, an immediate second call is warm
curl -o /dev/null -s -w 'cold %{time_total}s\n' https://talentradar-api.onrender.com/health
curl -o /dev/null -s -w 'warm %{time_total}s\n' https://talentradar-api.onrender.com/health

# backend
pip install -e ".[dev,lint]"
ruff check . && ruff format --check .
mypy .
GROQ_API_KEY=test_key JWT_SECRET_KEY=ci_secret_key_at_least_32_characters_long pytest -m "not slow"
```
