import { Page } from '@playwright/test'
import type { OutreachPack, PackItem } from '../lib/api'
import { addressBadge, mailtoHref } from '../lib/outreach-pack'
import { expect, test } from './fixtures'

/**
 * Today's pack on /outreach, against a fixed answer from the pack API.
 *
 * The account is real (the shared fixture signs up and in), but every call to
 * /api/v1/outreach/pack is answered here, so the tests do not depend on what the
 * ranking picked. Nothing is sent: "I sent it" only records, which is what the
 * posted body is checked for. Every person and company here is fictional.
 */

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-headers': 'authorization, content-type',
  'access-control-allow-methods': 'GET, PUT, POST, PATCH, DELETE, OPTIONS',
}

const PACK_API = /\/api\/v1\/outreach\/pack(?:[/?]|$)/

const item = (over: Partial<PackItem>): PackItem => ({
  id: 'i1', kind: 'first', person: { name: 'Asha Example', title: 'Founder', kind: 'founder' }, channel: 'email',
  address: null, linkedin_url: null, format: 'email', subject: 'Backend roles at Fictional Labs', message: 'Hi Asha,\nA short note & a question?',
  generated: true, reason: null, status: 'new', thread_id: null, ...over,
})

const PACK: OutreachPack = {
  day: '2026-10-26',
  companies: [{
    company_id: 'c1', name: 'Fictional Labs', summary: 'Payments APIs for small shops.', reasons: ['Raised a seed round 9 days ago'],
    roles: [{ job_id: 'j1', title: 'Backend Engineer' }], applied: false,
    items: [
      item({ id: 'i1', address: { value: 'asha.example@fictional.test', basis: 'found', source_host: 'fictional.test', learned_from: null } }),
      item({
        id: 'i2', person: { name: 'Ravi Sample', title: 'Engineering lead', kind: 'engineer' }, subject: 'Hello Ravi', message: 'Hi Ravi.',
        address: { value: 'ravi.sample@fictional.test', basis: 'guessed', source_host: null, learned_from: 'asha.example@fictional.test' },
      }),
    ],
  }],
  follow_ups: [],
  counts: { companies: 1, messages_ready: 2 },
}

const EMPTY: OutreachPack = { day: '2026-10-26', companies: [], follow_ups: [], counts: { companies: 0, messages_ready: 0 } }

/** Answers every pack call; records what was posted. The rest of /outreach is left to the real API. */
async function servePack(page: Page, pack: OutreachPack) {
  const calls: Array<{ method: string; path: string; body: unknown }> = []
  await page.route(PACK_API, async route => {
    const request = route.request()
    const method = request.method()
    if (method === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS })
    const path = new URL(request.url()).pathname.replace('/api/v1/outreach/pack', '') || '/'
    const raw = request.postData()
    calls.push({ method, path, body: raw ? JSON.parse(raw) : null })
    const json = (body: unknown) => route.fulfill({ status: 200, headers: CORS, contentType: 'application/json', body: JSON.stringify(body) })
    const every = [...pack.companies.flatMap(c => c.items), ...pack.follow_ups]
    const sent = path.match(/^\/items\/([^/]+)\/sent$/)
    if (method === 'GET' && path === '/') return json(pack)
    if (sent) {
      const body = JSON.parse(raw ?? '{}') as { message: string; subject: string | null }
      const found = every.find(i => i.id === sent[1])!
      return json({ item: { ...found, status: 'sent', message: body.message, subject: body.subject, thread_id: 't1' } })
    }
    return route.fulfill({ status: 204, headers: CORS })
  })
  return calls
}

test('mailtoHref encodes each field and writes newlines as CRLF', () => {
  expect(mailtoHref('asha.example@fictional.test', 'Roles & a question?', 'Hi Asha,\nA note.')).toBe(
    'mailto:asha.example@fictional.test?subject=Roles%20%26%20a%20question%3F&body=Hi%20Asha%2C%0D%0AA%20note.',
  )
  expect(mailtoHref('a@fictional.test', null, 'x')).toBe('mailto:a@fictional.test?body=x')
})

test('addressBadge says where an address came from', () => {
  expect(addressBadge({ value: 'a@fictional.test', basis: 'found', source_host: 'fictional.test', learned_from: null }))
    .toEqual({ label: 'found', hint: 'on fictional.test' })
  expect(addressBadge({ value: 'b@fictional.test', basis: 'guessed', source_host: null, learned_from: 'a@fictional.test' }))
    .toEqual({ label: 'likely, unverified', hint: 'format learned from a@fictional.test' })
  expect(addressBadge({ value: 'c@fictional.test', basis: 'saved', source_host: null, learned_from: null }).label).toBe('saved')
})

test.describe('today\'s pack', () => {
  test('shows companies with badges', async ({ page, account }) => {
    void account
    await servePack(page, PACK)
    await page.goto('/outreach')

    const today = page.locator('#today')
    await expect(today.getByRole('heading', { name: 'Fictional Labs' })).toBeVisible()
    await expect(today.getByText('Raised a seed round 9 days ago')).toBeVisible()
    await expect(today.locator('.pack-badge', { hasText: 'found' })).toBeVisible()
    await expect(today.locator('.pack-badge', { hasText: 'likely, unverified' })).toBeVisible()
    await expect(today.getByText('format learned from asha.example@fictional.test')).toBeVisible()
    await expect(today.getByRole('link', { name: 'Apply' })).toHaveAttribute('href', '/roles/j1')
  })

  test('Open in email has the encoded mailto', async ({ page, account }) => {
    void account
    await servePack(page, PACK)
    await page.goto('/outreach')

    const asha = page.locator('.pack-item', { hasText: 'Asha Example' })
    await expect(asha.getByRole('link', { name: 'Open in email' })).toHaveAttribute(
      'href',
      'mailto:asha.example@fictional.test?subject=Backend%20roles%20at%20Fictional%20Labs&body=Hi%20Asha%2C%0D%0AA%20short%20note%20%26%20a%20question%3F',
    )
  })

  test('I sent it posts the edited text and the item shows Sent', async ({ page, account }) => {
    void account
    const calls = await servePack(page, PACK)
    await page.goto('/outreach')

    const asha = page.locator('.pack-item', { hasText: 'Asha Example' })
    await asha.getByLabel('Message').fill('Hi Asha, edited by me.')
    await asha.getByRole('button', { name: 'I sent it' }).click()

    await expect(asha.locator('.pack-status')).toHaveText('Sent')
    const post = calls.find(c => c.method === 'POST' && c.path === '/items/i1/sent')
    expect(post?.body).toEqual({ message: 'Hi Asha, edited by me.', subject: 'Backend roles at Fictional Labs' })
  })

  test('Skip hides the item', async ({ page, account }) => {
    void account
    const calls = await servePack(page, PACK)
    await page.goto('/outreach')

    const ravi = page.locator('.pack-item', { hasText: 'Ravi Sample' })
    await ravi.getByRole('button', { name: 'Skip' }).click()

    await expect(page.locator('.pack-item', { hasText: 'Ravi Sample' })).toHaveCount(0)
    expect(calls.some(c => c.method === 'POST' && c.path === '/items/i2/skip')).toBe(true)
  })

  test('empty pack shows the widen copy', async ({ page, account }) => {
    void account
    await servePack(page, EMPTY)
    await page.goto('/outreach')

    await expect(page.locator('#today').getByText('Nothing new matched today. Add skills or target roles to widen it.')).toBeVisible()
  })
})
