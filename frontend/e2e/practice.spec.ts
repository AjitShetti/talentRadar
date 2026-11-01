import { Page, Route } from '@playwright/test'
import type { ChallengeBoard, ChallengesOverview, DesignFeedback, DesignPage, OverallBoard, PracticeDayCell, PracticeHistory, PracticeToday } from '../lib/api'
import { expect, goTo, test } from './fixtures'

/**
 * /practice and /practice/design, against fixed answers from the practice API.
 *
 * The account is real (the shared fixture signs up and in), but every call to
 * /api/v1/practice is answered here. The clock is fixed at Sunday 1 Nov 2026,
 * 15:30 IST.
 */

const NOW = new Date('2026-11-01T10:00:00Z')
const TODAY = '2026-11-01'
const SLUG = 'live-delivery-eta'
const PRACTICE_API = /\/api\/v1\/practice(?:[/?]|$)/
const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-headers': 'authorization, content-type',
  'access-control-allow-methods': 'GET, PUT, POST, PATCH, DELETE, OPTIONS',
}

const CONTEST = { starts_at: '2026-11-08T02:30:00Z', ends_at: '2026-11-08T04:00:00Z', show: true, live: false, url: 'https://leetcode.com/contest/' }
const TODAY_BASE = {
  day: TODAY, catalogue: 'ok', catalogue_size: 2000, track: 'backend', track_label: 'Backend', relaxed: [],
  reason: 'You have not practised this for a while.', done_at: null, feel: null, swaps_left: 1,
  leetcode_daily_url: 'https://leetcode.com/problemset/', contest: CONTEST,
} as const

const DSA_DAY: PracticeToday = {
  ...TODAY_BASE, kind: 'dsa', design: null,
  question: { id: 42, slug: 'trapping-rain-water', title: 'Trapping Rain Water', difficulty: 'hard', topics: ['two-pointers'], url: 'https://leetcode.com/problems/trapping-rain-water/' },
}
const DESIGN_DAY: PracticeToday = {
  ...TODAY_BASE, kind: 'design', question: null,
  design: { slug: SLUG, title: 'Live delivery ETA', minutes: 30, topics: ['streams', 'caching'], has_answer: false },
}

function days(done: Partial<PracticeDayCell>[] = []): PracticeDayCell[] {
  const end = Date.parse(`${TODAY}T00:00:00Z`)
  return Array.from({ length: 53 * 7 }, (_, i) => {
    const day = new Date(end - (53 * 7 - 1 - i) * 86_400_000).toISOString().slice(0, 10)
    return { day, done: false, difficulty: null, topic: null, kind: null, contest: false, ...done.find(cell => cell.day === day) }
  })
}

const NEW_HISTORY: PracticeHistory = {
  days: days(), counts: { this_month: 0, last_30: 0, longest_run: 0, current_streak: 0 }, topics: [],
  solved: { easy: 0, medium: 0, hard: 0, design: 0, total: 0 }, available: { easy: null, medium: null, hard: null, design: 40 },
}
const HISTORY: PracticeHistory = {
  days: days([
    { day: '2026-10-31', done: true, kind: 'design', topic: 'streams' },
    { day: '2026-10-30', done: true, kind: 'dsa', difficulty: 'hard', topic: 'two-pointers' },
  ]),
  counts: { this_month: 2, last_30: 2, longest_run: 2, current_streak: 2 },
  topics: [{ topic: 'two-pointers', count: 1 }],
  solved: { easy: 0, medium: 0, hard: 1, design: 1, total: 2 },
  available: { easy: 500, medium: 900, hard: 300, design: 40 },
}

const OVERVIEW: ChallengesOverview = {
  current: {
    slug: 'relay', opens_at: '2026-10-30T12:30:00Z', closes_at: '2026-11-06T12:30:00Z', last_start_at: '2026-11-05T12:30:00Z', attempt: null,
    brief: { slug: 'relay', title: 'A webhook relay', track: 'backend', hours: 24, summary: 'Take webhooks in and deliver them.', context: [], must_have: [], nice_to_have: [], constraints: [], deliverables: [], judge_focus: [], bengaluru_context: '' },
  },
  next_opens_at: '2026-11-06T12:30:00Z',
  past: [{ slug: 'queue', title: 'A small queue', track: 'backend', opens_at: '2026-10-23T12:30:00Z', status: null, total: null, held: false }],
}

const row = (rank: number, name: string) => ({ rank, name, total: 90 - rank, hours_used: 20 })
const BOARD: ChallengeBoard = { provisional: false, total_entries: 4, queued: 0, held: 0, rows: [row(1, 'builder-aaaa'), row(2, 'builder-bbbb'), row(3, 'builder-cccc'), row(4, 'builder-dddd')], me: null }
const OVERALL: OverallBoard = { window_challenges: 12, rows: [{ rank: 1, name: 'builder-over', score: 300, entries: 4 }], me: null, total_ranked: 1, top_percent: null, distribution: null }

const PROBLEM: DesignPage['problem'] = {
  slug: SLUG, title: 'Live delivery ETA', minutes: 30, topics: ['streams'], summary: 'Show customers a live arrival time.',
  context: ['Riders send their location every few seconds.'], must_handle: ['50,000 riders at dinner time'], show_in_answer: ['The main components'],
}
const DRAWING = [{
  id: 'box', type: 'rectangle', x: 10, y: 10, width: 200, height: 100, angle: 0, strokeColor: '#1e1e1e', backgroundColor: 'transparent', fillStyle: 'solid',
  strokeWidth: 2, strokeStyle: 'solid', roughness: 1, opacity: 100, groupIds: [], frameId: null, roundness: null, seed: 1, version: 3, versionNonce: 1,
  isDeleted: false, boundElements: null, updated: 1, link: null, locked: false,
}]
const OPEN_PAGE: DesignPage = { problem: PROBLEM, day: TODAY, answer: null, editable: true, done: false, last_answer_day: null, feedback: null, feedback_problem: null }
const FEEDBACK: DesignFeedback = {
  scores: { requirements: 8, components: 7, data_flow: 8, scale: 5, failure_handling: 4, tradeoffs: 6 },
  notes: { requirements: 'Named them.', components: 'Clear.', data_flow: 'Follows.', scale: 'Thin.', failure_handling: 'Nothing on a silent phone.', tradeoffs: 'One.' },
  work_on_next: 'Say what happens when a rider goes offline.', total: 63, at: '2026-11-01T10:01:00Z',
}

type Serve = {
  today: PracticeToday
  history: PracticeHistory
  overview: ChallengesOverview
  board: ChallengeBoard
  overall: OverallBoard
  design: DesignPage
  onSave: (route: Route) => Promise<void> | void
  onFeedback: (route: Route) => Promise<void> | void
}

const json = (route: Route, status: number, body: unknown) => route.fulfill({ status, headers: CORS, contentType: 'application/json', body: JSON.stringify(body) })

/** Answers every /api/v1/practice call; PUTs and POSTs on a design problem are returned for the test to read. */
async function servePractice(page: Page, overrides: Partial<Serve> = {}) {
  const serve: Serve = {
    today: DSA_DAY, history: HISTORY, overview: OVERVIEW, board: BOARD, overall: OVERALL, design: OPEN_PAGE,
    onSave: route => json(route, 200, { day: TODAY, saved_at: NOW.toISOString() }),
    onFeedback: route => json(route, 200, { ...OPEN_PAGE, feedback: FEEDBACK }),
    ...overrides,
  }
  const calls = { saves: [] as Array<{ elements: unknown[]; reasoning: string }>, feedback: 0, done: 0 }
  await page.route(PRACTICE_API, async route => {
    const request = route.request()
    const { pathname } = new URL(request.url())
    const path = pathname.replace('/api/v1/practice', '')
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS })
    if (path === '/today') return json(route, 200, serve.today)
    if (path.endsWith('/done')) {
      calls.done += 1
      return json(route, 200, { ...serve.today, done_at: NOW.toISOString() })
    }
    if (path.startsWith('/history')) return json(route, 200, serve.history)
    if (path === '/challenges') return json(route, 200, serve.overview)
    if (/^\/challenges\/[^/]+\/leaderboard$/.test(path)) return json(route, 200, serve.board)
    if (path === '/leaderboard') return json(route, 200, serve.overall)
    if (path === '/settings') return json(route, 200, { display_name: null, show_repo: false, contest_reminder: true, handle: 'builder-abcd' })
    if (path.endsWith('/answer')) {
      calls.saves.push(JSON.parse(request.postData() ?? '{}'))
      return serve.onSave(route)
    }
    if (path.endsWith('/feedback')) {
      calls.feedback += 1
      return serve.onFeedback(route)
    }
    if (path.startsWith('/design/')) return json(route, 200, serve.design)
    return json(route, 404, { detail: `Not served: ${path}` })
  })
  return calls
}

async function openPractice(page: Page) {
  await page.clock.setFixedTime(NOW)
  await page.goto('/practice')
  await expect(page.locator('#contest')).toBeVisible()
}

async function openProblem(page: Page) {
  await page.clock.setFixedTime(NOW)
  await page.goto(`/practice/design/${SLUG}`)
  await expect(page.getByRole('heading', { name: 'Live delivery ETA' })).toBeVisible()
}

const board = (page: Page) => page.locator('.pr-whiteboard .excalidraw')

/** Draws one rectangle on the whiteboard, the way a person does: the tool, then a drag. */
async function drawBox(page: Page) {
  await expect(board(page)).toBeVisible({ timeout: 30_000 })
  await page.locator('[data-testid="toolbar-rectangle"]').click({ force: true })
  const area = await page.locator('.pr-whiteboard').boundingBox()
  if (!area) throw new Error('the whiteboard has no box')
  await page.mouse.move(area.x + area.width / 2, area.y + area.height / 2)
  await page.mouse.down()
  await page.mouse.move(area.x + area.width / 2 + 120, area.y + area.height / 2 + 70, { steps: 5 })
  await page.mouse.up()
}

/** True when `first` comes before `second` in the document. */
const before = (page: Page, first: string, second: string) => page.evaluate(([a, b]) => {
  const one = document.querySelector(a)
  const two = document.querySelector(b)
  return !!one && !!two && !!(one.compareDocumentPosition(two) & Node.DOCUMENT_POSITION_FOLLOWING)
}, [first, second])

const ACTIVITY = '[aria-label="Activity"]'

test.describe('Practice', () => {
  test('someone with history lands on progress, then today, then the contest', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page)
    await openPractice(page)
    expect(await before(page, ACTIVITY, '#today')).toBe(true)
    expect(await before(page, '#today', '#contest')).toBe(true)
    await expect(page.getByText('days practised in the past year')).toBeVisible()
    await expect(page.locator('.pr-heat [data-today]')).toHaveCount(1)
    await expect(page.locator('.pr-tile').filter({ hasText: 'System design' })).toContainText('1 / 40')
  })

  test('someone new sees today above the graph', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page, { history: NEW_HISTORY })
    await openPractice(page)
    expect(await before(page, '#today', ACTIVITY)).toBe(true)
    await expect(page.getByText('Enter a weekly contest to get a rank')).toBeVisible()
  })

  test('a DSA day opens LeetCode in a new tab and marks done', async ({ page, account: _account, errors: _errors }) => {
    const calls = await servePractice(page)
    await openPractice(page)
    const open = page.getByRole('link', { name: 'Open on LeetCode ↗' }).first()
    await expect(open).toHaveAttribute('href', DSA_DAY.question!.url)
    await expect(open).toHaveAttribute('target', '_blank')
    await page.getByRole('button', { name: 'Mark done' }).click()
    await expect(page.locator('#today .pr-stamp')).toContainText('Done')
    expect(calls.done).toBe(1)
  })

  test('a design day links to its page', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page, { today: DESIGN_DAY })
    await openPractice(page)
    await expect(page.getByRole('link', { name: 'Draw and solve →' })).toHaveAttribute('href', `/practice/design/${SLUG}`)
    await expect(page.locator('#today')).toContainText('about 30 min')
  })

  test('a swap asks first when a drawing would be left behind', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page, { today: { ...DESIGN_DAY, design: { ...DESIGN_DAY.design!, has_answer: true } } })
    await openPractice(page)
    let asked = ''
    page.once('dialog', dialog => { asked = dialog.message(); return dialog.dismiss() })
    await page.locator('#today').getByRole('button', { name: 'Swap' }).click()
    expect(asked).toBe("Your drawing stays saved, but it can't be finished.")
  })

  test('the ranking sits under the contest and switches tabs', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page)
    await openPractice(page)
    const contest = page.locator('#contest')
    expect(await before(page, '#contest .pr-build', '#contest .pr-contest-board')).toBe(true)
    await expect(contest.getByText('builder-dddd')).toBeVisible()
    await contest.getByRole('button', { name: 'Overall' }).click()
    await expect(contest.getByText('builder-over')).toBeVisible()
    await expect(contest.getByText('builder-dddd')).toHaveCount(0)
    await expect(contest.getByText('Not entered', { exact: false })).toBeVisible()
  })

  test('podium hides under three rows', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page, { board: { ...BOARD, total_entries: 2, rows: BOARD.rows.slice(0, 2) } })
    await openPractice(page)
    await expect(page.locator('#contest').getByText('builder-bbbb')).toBeVisible()
    await expect(page.locator('.pr-podium')).toHaveCount(0)
  })

  test('three rows stand on a podium', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page)
    await openPractice(page)
    await expect(page.locator('.pr-podium .pr-pod')).toHaveCount(3)
    await expect(page.locator('.pr-board-row')).toHaveCount(1)
  })

  test('no leaderboard in the menu or the tabs, and the old URL lands on the contest', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page)
    await openPractice(page)
    await expect(page.locator('.prep-tabs').getByText('Leaderboard')).toHaveCount(0)
    await page.locator('.topnav-links').getByRole('button', { name: 'Prep' }).click()
    const menu = page.locator('.nav-menu[data-open="true"]')
    await expect(menu.getByRole('link', { name: 'Practice' })).toBeVisible()
    await expect(menu.getByText('Leaderboard')).toHaveCount(0)
    await page.goto('/practice/leaderboard')
    await expect(page).toHaveURL(/\/practice#contest$/)
    await expect(page.locator('#contest')).toBeVisible()
  })

  test('a contest brief shows its ranking', async ({ page, account: _account, errors: _errors }) => {
    await page.route(PRACTICE_API, route => route.fallback())
    await servePractice(page)
    await page.route(/\/api\/v1\/practice\/challenges\/relay(?:\/result)?$/, route => json(route, 200, OVERVIEW.current))
    await page.clock.setFixedTime(NOW)
    await page.goto('/practice/challenges/relay')
    await expect(page.locator('.board-kicker')).toContainText('Contest')
    await expect(page.getByRole('heading', { name: 'Ranking' })).toBeVisible()
    await expect(page.locator('.pr-podium .pr-pod')).toHaveCount(3)
    await expect(page.locator('.status-tabs')).toHaveCount(0)
  })

  for (const scheme of ['dark', 'light'] as const) {
    test(`no sideways page scroll at phone width, ${scheme}`, async ({ page, account: _account, errors: _errors }) => {
      await page.setViewportSize({ width: 375, height: 812 })
      await page.emulateMedia({ colorScheme: scheme })
      await servePractice(page)
      for (const open of [openPractice, openProblem]) {
        await open(page)
        const wider = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
        expect(wider).toBeLessThanOrEqual(0)
      }
    })
  }

  test('the graph scrolls inside its card, opened at the newest week', async ({ page, account: _account, errors: _errors }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await servePractice(page)
    await openPractice(page)
    const left = await page.locator('.pr-heat-scroll').evaluate(el => ({ at: el.scrollLeft, most: el.scrollWidth - el.clientWidth }))
    expect(left.most).toBeGreaterThan(0)
    expect(left.at).toBeGreaterThanOrEqual(left.most - 1)
  })
})

test.describe('Drawing a design problem', () => {
  test('the board loading does not save over a drawing', async ({ page, account: _account, errors: _errors }) => {
    const calls = await servePractice(page, { design: { ...OPEN_PAGE, answer: { elements: DRAWING, reasoning: 'Kept.', saved_at: NOW.toISOString() } } })
    await openProblem(page)
    await expect(board(page)).toBeVisible({ timeout: 30_000 })
    await page.waitForTimeout(6_500)
    expect(calls.saves).toEqual([])
    await expect(page.locator('#pr-reasoning')).toHaveValue('Kept.')
  })

  test('a real change saves after five seconds', async ({ page, account: _account, errors: _errors }) => {
    const calls = await servePractice(page)
    await openProblem(page)
    await drawBox(page)
    await expect.poll(() => calls.saves.length, { timeout: 9_000 }).toBeGreaterThan(0)
    expect(calls.saves[calls.saves.length - 1].elements).toHaveLength(1)
    await expect(page.locator('.pr-saved')).toContainText('Saved 15:30')
  })

  test('leaving the page saves what was not saved yet', async ({ page, account: _account, errors: _errors }) => {
    const calls = await servePractice(page)
    await openProblem(page)
    await page.locator('#pr-reasoning').fill('A sentence written just before leaving.')
    expect(calls.saves).toEqual([])
    await page.getByRole('link', { name: 'Mock interviews' }).click()
    await expect.poll(() => calls.saves.length).toBeGreaterThan(0)
    expect(calls.saves[0].reasoning).toBe('A sentence written just before leaving.')
  })

  test('a failed save keeps the work and says so, then tries again on the next change', async ({ page, account: _account, errors: _errors }) => {
    let fail = true
    const calls = await servePractice(page, {
      onSave: route => fail ? json(route, 500, { detail: 'Broken.' }) : json(route, 200, { day: TODAY, saved_at: NOW.toISOString() }),
    })
    await openProblem(page)
    await page.locator('#pr-reasoning').fill('First try.')
    await expect(page.locator('.pr-saved')).toContainText('Not saved. It will try again on your next change.', { timeout: 9_000 })
    await expect(page.locator('#pr-reasoning')).toHaveValue('First try.')
    fail = false
    await page.locator('#pr-reasoning').fill('Second try.')
    await expect(page.locator('.pr-saved')).toContainText('Saved 15:30', { timeout: 9_000 })
    expect(calls.saves[calls.saves.length - 1].reasoning).toBe('Second try.')
  })

  test('a 422 on save shows the sentence and keeps the work', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page, { onSave: route => json(route, 422, { detail: 'A drawing can hold at most 500 elements.' }) })
    await openProblem(page)
    await page.locator('#pr-reasoning').fill('Too much.')
    await expect(page.getByRole('alert').filter({ hasText: 'A drawing can hold at most 500 elements.' })).toBeVisible({ timeout: 9_000 })
    await expect(page.locator('#pr-reasoning')).toHaveValue('Too much.')
    await expect(page.locator('#pr-reasoning')).toBeEditable()
  })

  test('feedback shows its scores', async ({ page, account: _account, errors: _errors }) => {
    const calls = await servePractice(page)
    await openProblem(page)
    await page.locator('#pr-reasoning').fill('Location goes into a stream first.')
    await page.getByRole('button', { name: 'Get feedback' }).click()
    await expect(page.getByRole('heading', { name: 'Feedback' })).toBeVisible()
    await expect(page.locator('.results-total')).toContainText('63')
    await expect(page.locator('.results-dims > div')).toHaveCount(6)
    await expect(page.locator('.results-dims .weakest')).toContainText('Failure handling')
    await expect(page.getByText('Say what happens when a rider goes offline.')).toBeVisible()
    expect(calls.saves.length).toBeGreaterThan(0)
    expect(calls.feedback).toBe(1)
  })

  test('feedback unavailable keeps the drawing and says the day is counted', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page, { onFeedback: route => json(route, 200, { ...OPEN_PAGE, feedback_problem: 'limit' }) })
    await openProblem(page)
    await drawBox(page)
    await page.getByRole('button', { name: 'Get feedback' }).click()
    await expect(page.getByRole('alert').filter({ hasText: "You have used today's feedback. Your drawing is saved and the day is counted." })).toBeVisible()
    await expect(board(page)).toBeVisible()
  })

  test('too little drawn shows the sentence', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page, { onFeedback: route => json(route, 422, { detail: 'To get feedback, label at least 3 shapes.' }) })
    await openProblem(page)
    await page.getByRole('button', { name: 'Get feedback' }).click()
    await expect(page.getByRole('alert').filter({ hasText: 'To get feedback, label at least 3 shapes.' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Get feedback' })).toBeEnabled()
  })

  test('a 409 on save turns the page read-only and keeps the work', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page, { onSave: route => json(route, 409, { detail: 'This answer can no longer be changed.' }) })
    await openProblem(page)
    await page.locator('#pr-reasoning').fill('Written after the close.')
    await expect(page.getByRole('alert').filter({ hasText: 'This one closed at 06:00 IST' })).toBeVisible({ timeout: 9_000 })
    await expect(page.locator('#pr-reasoning')).toHaveValue('Written after the close.')
    await expect(page.locator('#pr-reasoning')).not.toBeEditable()
    await expect(page.getByRole('button', { name: 'Get feedback' })).toHaveCount(0)
  })

  test('an older answer is read-only and a cleared one says so', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page, { design: { ...OPEN_PAGE, editable: false, answer: { elements: null, reasoning: 'From long ago.', saved_at: NOW.toISOString() } } })
    await openProblem(page)
    await expect(page.getByText('This drawing was cleared after 180 days.')).toBeVisible()
    await expect(page.getByText('This answer is read-only.')).toBeVisible()
    await expect(page.locator('#pr-reasoning')).not.toBeEditable()
  })

  test('a repeat links to the last answer', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page, { design: { ...OPEN_PAGE, last_answer_day: '2026-08-01' } })
    await openProblem(page)
    await expect(page.getByRole('link', { name: 'See your last answer' })).toHaveAttribute('href', `/practice/design/${SLUG}?day=2026-08-01`)
  })

  test('a problem that does not exist says so and links back', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page)
    await page.route(/\/api\/v1\/practice\/design\/nope$/, route => json(route, 404, { detail: 'We could not find that problem.' }))
    await page.goto('/practice/design/nope')
    await expect(page.locator('.empty-state')).toContainText('We could not find that problem.')
    await expect(page.locator('.empty-state').getByRole('link', { name: 'Back to Practice' })).toHaveAttribute('href', '/practice')
  })

  test('a blocked whiteboard chunk leaves the problem and a message', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page)
    await page.route(/DesignBoard/, route => route.abort())
    await openProblem(page)
    await expect(page.getByRole('alert').filter({ hasText: 'The whiteboard could not load.' })).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText('50,000 riders at dinner time')).toBeVisible()
    await expect(page.locator('#pr-reasoning')).toBeEditable()
  })

  test('no request leaves for another host from the solve page', async ({ page, baseURL, account: _account, errors: _errors }) => {
    // globals.css asks Google for Inter on every page, whiteboard or not; nothing else may go out.
    const allowed = [new URL(baseURL!).origin, new URL(process.env.E2E_API_URL || 'http://localhost:8000').origin, 'https://fonts.googleapis.com', 'https://fonts.gstatic.com']
    const elsewhere: string[] = []
    page.on('request', request => {
      const url = new URL(request.url())
      if (url.protocol.startsWith('http') && !allowed.includes(url.origin)) elsewhere.push(url.origin)
    })
    await servePractice(page)
    await openProblem(page)
    await drawBox(page)
    await page.waitForTimeout(2_000)
    expect([...new Set(elsewhere)]).toEqual([])
  })

  test('the board follows the theme, and nothing on it opens another site', async ({ page, account: _account, errors: _errors }) => {
    await servePractice(page)
    await openProblem(page)
    await expect(board(page)).toBeVisible({ timeout: 30_000 })
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark' })
    await expect(board(page)).toHaveClass(/theme--dark/)
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light' })
    await expect(board(page)).not.toHaveClass(/theme--dark/)
    await expect(page.locator('.pr-whiteboard .default-sidebar-trigger')).toBeHidden()
    await expect(page.locator('.pr-whiteboard .help-icon')).toBeHidden()
    await expect(page.locator('.pr-whiteboard a[href^="http"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="toolbar-image"]')).toHaveCount(0)
  })
})

test('the app reaches Practice through its menu', async ({ page, account: _account, errors: _errors }) => {
  await servePractice(page)
  await page.goto('/dashboard')
  await goTo(page, 'Practice')
  await expect(page).toHaveURL(/\/practice$/)
})
