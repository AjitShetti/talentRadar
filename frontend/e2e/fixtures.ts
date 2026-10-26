import { expect, test as base, Page } from '@playwright/test'

export const API_URL = process.env.E2E_API_URL || 'http://localhost:8000'

type Account = { email: string; token: string }

/**
 * Every test gets a fresh account, signed in by seeding the token the app
 * reads from localStorage — the login form is not what these tests are about.
 */
export const test = base.extend<{ account: Account; errors: string[] }>({
  account: async ({ page, request }, use) => {
    const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`
    const password = 'e2e-password-123'
    const signup = await request.post(`${API_URL}/api/auth/signup`, { data: { email, password } })
    expect(signup.ok(), await signup.text()).toBeTruthy()
    const login = await request.post(`${API_URL}/api/auth/login`, { data: { email, password } })
    expect(login.ok(), await login.text()).toBeTruthy()
    const { access_token: token } = await login.json() as { access_token: string }
    await page.addInitScript(([t, e]) => {
      localStorage.setItem('talentradar_token', t)
      localStorage.setItem('talentradar_email', e)
    }, [token, email])
    await use({ email, token })
  },
  // Uncaught exceptions on the page fail the test that caused them.
  errors: async ({ page }, use) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await use(errors)
    expect(errors, 'uncaught page errors').toEqual([])
  },
})

export { expect }

/** Pages that sit in a top-nav group menu, by the group's label. */
const IN_GROUP: Record<string, string> = {
  'Find roles': 'Job hunt', Applications: 'Job hunt', Outreach: 'Job hunt', Companies: 'Job hunt',
  Practice: 'Prep', 'Mock interviews': 'Prep', Leaderboard: 'Prep',
}
/** Pages reached from the account menu. */
const IN_ACCOUNT = ['Profile & goals', 'Resume studio']

/** Navigate the way a user does — through the top nav or the account menu, a client-side transition. */
export async function goTo(page: Page, label: string) {
  if (IN_ACCOUNT.includes(label)) {
    await page.locator('.user-trigger').click()
    await page.getByRole('menuitem', { name: label }).click()
    return
  }
  const group = IN_GROUP[label]
  if (group) {
    await page.locator('.topnav-links').getByRole('button', { name: group }).click()
    await page.locator('.nav-menu[data-open="true"]').getByRole('link', { name: label }).click()
    return
  }
  await page.locator('.topnav-links').getByRole('link', { name: label }).click()
}

/** Sign out through the account menu. */
export async function signOutViaMenu(page: Page) {
  await page.locator('.user-trigger').click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
}
