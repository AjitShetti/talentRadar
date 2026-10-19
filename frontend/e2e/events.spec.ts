import { Page, Route } from '@playwright/test'
import type {
  Briefing, BriefingCard, CompanyDetail, EventCard, EventContact, EventDetail, EventPage, EventSaveStatus,
} from '../lib/api'
import { expect, test } from './fixtures'

/**
 * The /events page, against a fixed answer from the events API.
 *
 * The account is real (the shared fixture signs up and in), but every call to
 * /api/v1/events is answered here, from the preview's sample data, so the
 * tests do not depend on what a feed happened to list. The clock is fixed at
 * Thu 15 Oct 2026, 20:00 IST, which makes the Reimagining talk (18:30) over and
 * the Swift meetup (Fri 16 Oct, 15:00) still to come.
 */

const NOW = new Date('2026-10-15T14:30:00Z')

const BASE = {
  ends_at: null, is_online: false, venue_name: null, lat: null, lng: null, geo_precision: 'area' as const, is_free: null,
  format: null, topics: [], source: 'luma' as const, url: null, is_cancelled: false, companies: [],
}

const CARDS: Array<Omit<EventCard, 'my_status'>> = [
  {
    ...BASE, id: 'e2', title: 'Reimagining education in the age of AI with Dr. Prathosh A.P. (IISc)',
    starts_at: '2026-10-15T13:00:00Z', ends_at: '2026-10-15T14:00:00Z', area: 'Indiranagar', organiser_name: 'Team Lossfunk',
    format: 'talk', topics: ['ai'], url: 'https://luma.com/reimagining-education',
  },
  {
    ...BASE, id: 'e1', title: 'Swift Bengaluru × Okta', starts_at: '2026-10-16T09:30:00Z', ends_at: '2026-10-16T12:30:00Z',
    area: 'Bellandur', organiser_name: 'Swift Bengaluru', format: 'meetup', topics: ['mobile'], is_free: true,
    url: 'https://luma.com/swift-okta', companies: [{ id: 'c1', name: 'Okta' }],
  },
  {
    ...BASE, id: 'e3', title: 'Namma AI Design Social', starts_at: '2026-10-16T12:30:00Z', ends_at: '2026-10-16T14:30:00Z',
    area: 'Koramangala', organiser_name: 'Jayneil Dalal', format: 'mixer', topics: ['design'], url: 'https://luma.com/namma-ai-design',
  },
]

const PEOPLE: EventContact[] = [
  {
    id: 'p1', event_id: 'e2', name: 'Meera Iyer', title: 'ML engineer', company_name: 'Sarvam AI', company_id: null,
    linkedin_url: 'https://www.linkedin.com/in/meera-iyer-example', x_url: null, github_url: null, email: null,
    note: 'Working on Indic speech evals.', created_at: '2026-10-15T14:00:00Z', linkedin_search_url: null,
  },
  {
    id: 'p2', event_id: 'e2', name: 'Arjun Rao', title: 'Founder', company_name: 'Stealth', company_id: null,
    linkedin_url: null, x_url: null, github_url: null, email: null, note: null, created_at: '2026-10-15T14:05:00Z',
    linkedin_search_url: 'https://www.linkedin.com/search/results/people/?keywords=%22Arjun%20Rao%22%20Stealth',
  },
]

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-headers': 'authorization, content-type',
  'access-control-allow-methods': 'GET, PUT, POST, PATCH, DELETE, OPTIONS',
}

/** Answers every /api/v1/events call; saves are remembered so a reload of the list agrees with what was clicked. */
async function serveEvents(page: Page) {
  const saved = new Map<string, EventSaveStatus>([['e2', 'going']])
  const calls: string[] = []
  const card = (id: string): EventCard => ({ ...CARDS.find(c => c.id === id)!, my_status: saved.get(id) ?? null })

  // A regular expression, not '**/api/v1/events**': a glob's `**` crosses `/` only between slashes, so that one misses /events/<id>.
  await page.route(/\/api\/v1\/events(?:[/?]|$)/, async route => {
    const request = route.request()
    const method = request.method()
    if (method === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS })
    const path = new URL(request.url()).pathname.replace('/api/v1/events', '')
    calls.push(`${method} ${path || '/'}`)
    const json = (body: unknown) => route.fulfill({ status: 200, headers: CORS, contentType: 'application/json', body: JSON.stringify(body) })

    if (method === 'GET' && path === '') {
      const query = new URL(request.url()).searchParams
      calls.push(`LIST ${query.toString()}`)
      // A company's own events, as the company drawer asks for them.
      const company = query.get('company_id')
      const listed = company ? CARDS.filter(c => c.companies.some(ref => ref.id === company)) : CARDS
      const body: EventPage = {
        total: listed.length,
        events: listed.map(c => card(c.id)),
        counts: { upcoming: CARDS.length, this_week: CARDS.length, going: Array.from(saved.values()).filter(s => s === 'going').length },
        sources: [
          { source: 'luma', last_read_at: '2026-10-15T00:01:00Z', status: 'healthy' },
          { source: 'gdg', last_read_at: '2026-10-15T00:02:00Z', status: 'healthy' },
          { source: 'eventbrite', last_read_at: null, status: 'off' },
        ],
      }
      return json(body)
    }

    const save = path.match(/^\/([^/]+)\/save$/)
    if (save && method === 'PUT') {
      const status = (JSON.parse(request.postData() || '{}') as { status: EventSaveStatus }).status
      saved.set(save[1], status)
      return json({ status, note: null })
    }
    if (save && method === 'DELETE') {
      saved.delete(save[1])
      return route.fulfill({ status: 204, headers: CORS })
    }

    const one = path.match(/^\/([^/]+)$/)
    if (one && method === 'GET' && CARDS.some(c => c.id === one[1])) {
      const id = one[1]
      const status = saved.get(id)
      const detail: EventDetail = {
        ...card(id),
        companies: card(id).companies.map(company => ({ ...company, role: 'host' as const })),
        snippet: 'An afternoon of talks, then open mic and chai after.',
        reasons: ['AI', `in ${card(id).area}`],
        hosts: [],
        also_on: [],
        my: status ? { status, note: null } : null,
        people: id === 'e2' ? PEOPLE : [],
      }
      return json(detail)
    }

    return route.fulfill({ status: 404, headers: CORS, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) })
  })
  return calls
}

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(NOW)
})

test('lists events grouped by IST day', async ({ page, account, errors }) => {
  void account; void errors
  await serveEvents(page)
  await page.goto('/events')

  await expect(page.getByRole('heading', { name: 'Where to meet people this week' })).toBeVisible()
  await expect(page.getByText('THU 15 OCT')).toBeVisible()
  await expect(page.getByText('FRI 16 OCT')).toBeVisible()
  await expect(page.locator('.ev-row')).toHaveCount(3)
})

test('opens detail from ?event= and closes on Escape', async ({ page, account, errors }) => {
  void account; void errors
  await serveEvents(page)
  await page.goto('/events?event=e1')

  const dialog = page.getByRole('dialog', { name: 'Swift Bengaluru × Okta' })
  await expect(dialog).toBeVisible()
  const register = dialog.getByRole('link', { name: 'Register on Luma' })
  await expect(register).toBeVisible()
  await expect(register).toHaveAttribute('target', '_blank')
  await expect(register).toHaveAttribute('rel', /noopener/)

  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
})

test('save toggles aria-pressed', async ({ page, account, errors }) => {
  void account; void errors
  const calls = await serveEvents(page)
  await page.goto('/events')

  const row = page.locator('.ev-row').filter({ hasText: 'Swift Bengaluru × Okta' })
  const save = row.getByRole('button', { name: /^Save/ })
  await expect(save).toHaveAttribute('aria-pressed', 'false')

  const put = page.waitForRequest(request => request.method() === 'PUT' && request.url().includes('/e1/save'))
  await save.click()
  await put
  await expect(save).toHaveAttribute('aria-pressed', 'true')
  expect(calls).toContain('PUT /e1/save')
})

test('filters sheet at 375px', async ({ page, account, errors }) => {
  void account; void errors
  await serveEvents(page)
  await page.setViewportSize({ width: 375, height: 800 })
  await page.goto('/events')

  await page.getByRole('button', { name: /^Filters/ }).click()
  const sheet = page.getByRole('dialog', { name: 'Filters' })
  await expect(sheet).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(sheet).toBeHidden()
})

test('Start a thread hidden while outreach disabled', async ({ page, account, errors }) => {
  void account; void errors
  await serveEvents(page)
  await page.goto('/events?event=e2')

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByText('Meera Iyer')).toBeVisible()
  await expect(dialog.getByText('Arjun Rao')).toBeVisible()
  await expect(dialog.locator('a[href*="/outreach"]')).toHaveCount(0)
})

test('map falls back to list without WebGL', async ({ page, account, errors }) => {
  void account; void errors
  // A browser with no WebGL: asking a canvas for any drawing context returns null.
  await page.addInitScript(() => {
    ;(HTMLCanvasElement.prototype as unknown as { getContext: () => null }).getContext = () => null
  })
  await serveEvents(page)
  await page.goto('/events')

  const view = page.getByRole('group', { name: 'View' })
  await expect(view.getByRole('button', { name: 'List' })).toHaveAttribute('aria-pressed', 'true')
  await view.getByRole('button', { name: 'Map' }).click()

  await expect(page.getByText('Map unavailable — showing the list.')).toBeVisible()
  await expect(page.locator('.ev-row')).toHaveCount(3)
  await expect(view.getByRole('button', { name: 'List' })).toHaveAttribute('aria-pressed', 'true')
})

// ── The company drawer ───────────────────────────────────────────────────────

const company = (id: string, name: string): CompanyDetail => ({
  id, name, domain: null, website_url: null, logo_url: null, tier: 'product', tier_label: 'Product', industry: 'Software',
  description: null, hq_city: 'Bengaluru', hq_country: 'India', office_cities: ['Bengaluru'], employee_count_range: null,
  founded_year: null, github_org: null, careers_url: null, tech_stack: [], open_roles: 0, summary: `${name} builds software.`,
  summary_source: 'curated', stage: null, sector: null, area: 'Bellandur', geo_precision: 'area', slug: null,
  linkedin_url: null, github: null, tech_stack_curated: [], tech_stack_from_postings: [], culture_summary: null, contacts: [],
  jobs: [], has_profile: true, address: null, x_url: null, last_funding_at: null, employee_count: null, is_bigger: false,
  people: { founders: [], leaders: [], engineers: [] }, linkedin_links: [], news: [],
})

/** The company page's own reads, from a two-company directory. The map is not drawn (see the callers), so it lists none. */
async function serveCompanyIntel(page: Page) {
  const companies = [company('c1', 'Okta'), company('c2', 'Plain Works')]
  await page.route(/\/api\/v1\/company-intel(?:[/?]|$)/, async route => {
    const request = route.request()
    if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS })
    const path = new URL(request.url()).pathname.replace('/api/v1/company-intel', '')
    const json = (body: unknown) => route.fulfill({ status: 200, headers: CORS, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/map') return json({ generated_at: '2026-10-15T00:00:00Z', city: 'Bengaluru', total: 0, pinned: 0, approximate: 0, rows: [] })
    if (path === '/map/for-me') return json({ hiring_for_you: [] })
    if (path === '/news') return json([])
    if (path === '' || path === '/') return json({ companies, total: companies.length, offset: 0, limit: 50, city: 'Bengaluru' })
    const found = companies.find(c => `/${c.id}` === path)
    if (found) return json(found)
    return route.fulfill({ status: 404, headers: CORS, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) })
  })
}

/** The company page in its list view, with no map to draw. */
async function openCompanyList(page: Page) {
  await page.addInitScript(() => {
    ;(HTMLCanvasElement.prototype as unknown as { getContext: () => null }).getContext = () => null
    localStorage.setItem('talentradar:state:companyIntel.view', JSON.stringify('list'))
  })
  await serveCompanyIntel(page)
  await page.goto('/company-intel')
}

/** The distinct list reads the API saw. `next dev` runs effects twice (Strict Mode), so one read can arrive twice. */
const listReads = (calls: string[]) => Array.from(new Set(calls.filter(call => call.startsWith('LIST'))))

test('company drawer shows hosted events', async ({ page, account, errors }) => {
  void account; void errors
  const calls = await serveEvents(page)
  await openCompanyList(page)

  await page.locator('.ci-card').filter({ hasText: 'Okta' }).click()

  const hosted = page.getByTestId('drawer-events')
  await expect(hosted).toContainText('Swift Bengaluru × Okta')
  await expect(hosted.getByRole('heading')).toHaveText('Hosted by Okta')
  const row = hosted.getByRole('link', { name: /Swift Bengaluru × Okta/ })
  await expect(row).toHaveAttribute('href', '/events?event=e1')
  await expect(row).toContainText('Fri 16 Oct · 15:00–18:00 IST · Bellandur')
  expect(listReads(calls)).toEqual(['LIST company_id=c1&limit=3'])
})

test('company drawer hides the events block when the company hosts none', async ({ page, account, errors }) => {
  void account; void errors
  const calls = await serveEvents(page)
  await openCompanyList(page)

  await page.locator('.ci-card').filter({ hasText: 'Plain Works' }).click()

  // The people block is drawn from the same detail the events block sits under; once it is there the events read has been asked.
  await expect(page.getByTestId('drawer-people')).toBeVisible()
  await expect.poll(() => listReads(calls)).toEqual(['LIST company_id=c2&limit=3'])
  await expect(page.getByTestId('drawer-events')).toHaveCount(0)
})

// ── The Overview day plan ────────────────────────────────────────────────────

const briefingCard = (kind: string, id: string, title: string, over: Partial<BriefingCard> = {}): BriefingCard => ({
  id, kind, title, detail: `${title} (detail)`, tone: 'action', actions: [], meta: {}, dismissible: true, ...over,
})

const TONIGHT = briefingCard('event_today', 'event_today:e2', 'Tonight: Reimagining education in the age of AI', {
  tone: 'primary', actions: [{ label: 'Open event', href: '/events?event=e2', style: 'primary' }],
})
const SOON = briefingCard('event_soon', 'event_soon:e1', 'Friday: Swift Bengaluru × Okta', {
  actions: [{ label: 'Open event', href: '/events?event=e1', style: 'primary' }],
})
const MET = briefingCard('event_met_prompt', 'event_met:e2', 'Who did you meet at Hack Night?', {
  actions: [{ label: 'Add who you met', href: '/events?event=e2#people', style: 'primary' }],
})
const PRIORITY = briefingCard('priority', 'priority:apply', 'Apply to the Stripe backend role', {
  tone: 'primary', dismissible: false, actions: [{ label: 'Do this now', href: '/search', style: 'primary' }],
})

/** The Overview, with an interview today or not, a finished profile or not, and the briefing's cards. */
async function serveOverview(page: Page, plan: { interviewToday: boolean; onboarded: boolean; cards: BriefingCard[] }) {
  const overview = {
    profile: { full_name: 'Test User', onboarding_completed: plan.onboarded, target_roles: ['Backend Engineer'] },
    analytics: { total_applications: 1 },
    agenda: plan.interviewToday
      ? [{ application_id: 'a1', job_id: null, role: 'Backend Engineer', company: 'Acme', status: 'interview', scheduled_at: '2026-10-15T09:00:00Z', days_away: 0, when_label: 'Today' }]
      : [],
    interviews: null, skills_focus: null, job_matches: [], sweep: null, recent_activity: [],
  }
  const briefing: Briefing = {
    generated_at: '2026-10-15T14:30:00Z', headline: 'A full day.', cards: plan.cards, hidden_count: 0,
    stats: { applied: 1, onboarding_completed: plan.onboarded },
  }
  const answer = (body: unknown) => async (route: Route) => {
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS })
    return route.fulfill({ status: 200, headers: CORS, contentType: 'application/json', body: JSON.stringify(body) })
  }
  await page.route('**/api/v1/dashboard/overview', answer(overview))
  await page.route('**/api/v1/agent/briefing', answer(briefing))
}

const focusTitles = (page: Page) => page.locator('.ov-act h3')

test('overview ranks tonight second', async ({ page, account, errors }) => {
  void account; void errors
  // An unfinished profile used to outrank everything but the interview; a dated evening does not wait.
  await serveOverview(page, { interviewToday: true, onboarded: false, cards: [TONIGHT, PRIORITY] })
  await page.goto('/dashboard')

  await expect(focusTitles(page).nth(1)).toHaveText(/^Tonight:/)
  await expect(focusTitles(page)).toHaveText(['Prep for Acme', /^Tonight:/, 'Finish your profile'])
})

test('the briefing lead skips event cards and tonight is not shown twice', async ({ page, account, errors }) => {
  void account; void errors
  await serveOverview(page, { interviewToday: true, onboarded: true, cards: [TONIGHT, PRIORITY] })
  await page.goto('/dashboard')

  await expect(focusTitles(page)).toHaveText(['Prep for Acme', /^Tonight:/, 'Apply to the Stripe backend role'])
  // Both cards were promoted into Today's focus, so none is left for "Also on your radar".
  await expect(page.locator('.cp-card')).toHaveCount(0)
})

test('this week and who-you-met follow the lead when there is room', async ({ page, account, errors }) => {
  void account; void errors
  await serveOverview(page, { interviewToday: false, onboarded: true, cards: [SOON, MET, PRIORITY] })
  await page.goto('/dashboard')

  await expect(focusTitles(page)).toHaveText(['Apply to the Stripe backend role', /^Friday:/, /^Who did you meet/])
  await expect(page.getByRole('link', { name: /Add who you met/ })).toHaveAttribute('href', '/events?event=e2#people')
})
