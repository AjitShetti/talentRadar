import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end tests against a running stack.
 *
 *   make up && make migrate          # API on :8000
 *   npx playwright test              # starts `next dev` on :3000 unless one is running
 *
 * :3000 because it is the origin the API's CORS_ORIGINS allows by default; any
 * other port fails every request with "Failed to fetch".
 * E2E_BASE_URL / E2E_API_URL point the suite somewhere else.
 */
const PORT = 3000
const baseURL = process.env.E2E_BASE_URL || `http://localhost:${PORT}`

export default defineConfig({
  testDir: './e2e',
  // Searches and interview turns hit live scrapers and an LLM.
  timeout: 180_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    permissions: [],
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: `npx next dev -p ${PORT}`,
    url: baseURL,
    reuseExistingServer: true,
    timeout: 180_000,
    env: { NEXT_PUBLIC_API_URL: process.env.E2E_API_URL || 'http://localhost:8000' },
  },
})
