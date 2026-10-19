import { Page } from '@playwright/test'
import type { EventCard, EventContact, EventDetail, EventPage, EventSaveStatus } from '../lib/api'
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
      const body: EventPage = {
        total: CARDS.length,
        events: CARDS.map(c => card(c.id)),
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
