import { expect, test } from '@playwright/test'

/**
 * The cold-start strip, against a faked /health — no API needed. Every other
 * API call is stubbed out so the page never reaches a real backend.
 */
test.beforeEach(async ({ page }) => {
  await page.route(/\/api\//, route => route.fulfill({ status: 401, json: { detail: 'stub' } }))
})

test('a warm API shows nothing', async ({ page }) => {
  await page.route('**/health', route => route.fulfill({ json: { status: 'healthy' } }))
  await page.goto('/login')
  await page.waitForTimeout(3500)
  await expect(page.locator('.api-wake')).toHaveCount(0)
})

test('a cold API shows an elapsed clock, then confirms and gets out of the way', async ({ page }) => {
  let release!: () => void
  const released = new Promise<void>(resolve => { release = resolve })
  await page.route('**/health', async route => { await released; await route.fulfill({ json: { status: 'healthy' } }) })
  await page.goto('/login')

  const strip = page.locator('.api-wake')
  await expect(strip).toHaveAttribute('data-phase', 'waking', { timeout: 6000 })
  await expect(strip).toContainText('Starting the server')
  await expect(strip.locator('.api-wake-clock')).toHaveText(/^0:0[2-9]$/)

  release()
  await expect(strip).toHaveAttribute('data-phase', 'ready')
  await expect(strip).toHaveCount(0, { timeout: 5000 })
})

test('an API that never answers says so and offers a retry', async ({ page }) => {
  await page.clock.install()
  let alive = false
  await page.route('**/health', route => alive ? route.fulfill({ json: { status: 'healthy' } }) : route.fulfill({ status: 502, body: '' }))
  await page.goto('/login')

  // Walk past the give-up point in steps so each retry's timers get to fire.
  for (let i = 0; i < 30; i++) await page.clock.runFor(5000)
  const strip = page.locator('.api-wake')
  await expect(strip).toHaveAttribute('data-phase', 'down')
  await expect(strip).toContainText('isn’t answering')

  alive = true
  await strip.getByRole('button', { name: 'Try again' }).click()
  await expect(strip).toHaveAttribute('data-phase', 'ready')
  await page.clock.runFor(3000)
  await expect(strip).toHaveCount(0)
})
