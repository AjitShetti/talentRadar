import { Page } from '@playwright/test'
import { expect, goTo, test } from './fixtures'

/**
 * Pages keep their state when the user moves between them.
 *
 * Every scenario here navigates away while a request is still in flight —
 * that is the case that used to lose work: the request finished on a page that
 * no longer existed, and its result went nowhere.
 */

const SEARCH_INPUT = 'e.g. Senior product design roles in fintech'

async function openResumeName(page: Page) {
  // Wait for the editor itself: the auth gate and the loader both come and go
  // first, and clicking the header too early toggles a panel that was open.
  const header = page.getByText('Personal details')
  await expect(header).toBeVisible({ timeout: 60_000 })
  const name = page.getByLabel('Full name')
  if (!(await name.isVisible())) await header.click()
  await expect(name).toBeVisible()
  return name
}

test('search results survive a trip to the resume studio, and resume edits survive the trip back', async ({ page, account, errors }) => {
  void account; void errors
  await page.goto('/search')
  const input = page.getByPlaceholder(SEARCH_INPUT)
  await input.fill('python developer')
  await page.getByRole('button', { name: 'Search roles' }).click()
  await expect(page.getByRole('button', { name: 'Searching…' })).toBeVisible()

  // Leave immediately, while the search is still out.
  await goTo(page, 'Resume studio')
  const name = await openResumeName(page)
  await name.fill('Priya Raman')
  await page.getByLabel('Headline').fill('Backend engineer')

  // And leave the resume inside its autosave pause.
  await goTo(page, 'Find roles')
  await expect(page.getByPlaceholder(SEARCH_INPUT)).toHaveValue('python developer')
  // Either still running (shown as such) or finished — never a blank board.
  await expect(page.getByText('Start with a conversation.')).toBeHidden()
  await expect(page.getByRole('button', { name: 'Search roles' })).toBeVisible({ timeout: 150_000 })
  const cards = page.locator('.job-card')
  const found = await cards.count()
  if (found === 0) await expect(page.getByText('No roles found yet.')).toBeVisible()
  console.log(`search returned ${found} roles`)

  await goTo(page, 'Resume studio')
  const nameAgain = await openResumeName(page)
  await expect(nameAgain).toHaveValue('Priya Raman')
  await expect(page.getByLabel('Headline')).toHaveValue('Backend engineer')

  // Back again: the results are still on the board without searching again.
  await goTo(page, 'Find roles')
  await expect(page.getByPlaceholder(SEARCH_INPUT)).toHaveValue('python developer')
  await expect(cards).toHaveCount(found)
})

test('a resume edit made just before leaving reaches the server', async ({ page, account, errors }) => {
  void account; void errors
  await page.goto('/resume-studio')
  const name = await openResumeName(page)
  await name.fill('Arjun Mehta')
  // Inside the 800 ms autosave pause.
  await goTo(page, 'Applications')
  await expect(page.getByRole('heading', { name: /Keep every opportunity moving/ })).toBeVisible()

  // A cold load reads from the server only once the draft is saved.
  await page.goto('/resume-studio')
  await expect(page.getByTestId('resume-save-status')).toHaveText('All changes saved', { timeout: 30_000 })
  await page.evaluate(() => sessionStorage.clear())
  await page.reload()
  await expect(await openResumeName(page)).toHaveValue('Arjun Mehta')
})

test('an interview stays on its question, and an answer submitted before leaving still lands', async ({ page, account, errors }) => {
  void account; void errors
  await page.goto('/interview')
  await page.getByPlaceholder(/Any skill, tool or role/).fill('Python')
  await page.getByRole('button', { name: /Typed interview/ }).click()
  await page.getByRole('button', { name: /Start interview/ }).click()
  await expect(page.getByRole('heading', { name: /Question 1/ })).toBeVisible({ timeout: 90_000 })
  const firstQuestion = await page.locator('.question-card').innerText()

  // Leave and come back: same session, same question.
  await goTo(page, 'Resume studio')
  await goTo(page, 'Interview lab')
  await expect(page.getByRole('heading', { name: /Question 1/ })).toBeVisible()
  await expect(page.locator('.question-card')).toHaveText(firstQuestion)

  // A half-written answer is kept.
  const answerBox = page.getByPlaceholder(/Think out loud/)
  await answerBox.fill('Lists are mutable and tuples are not')
  await goTo(page, 'Find roles')
  await goTo(page, 'Interview lab')
  await expect(answerBox).toHaveValue('Lists are mutable and tuples are not')

  // Submit, then leave while it is being evaluated.
  await answerBox.fill('Lists are mutable, tuples are immutable and hashable, so tuples can be dict keys. Lists over-allocate to make append amortised O(1).')
  await page.getByRole('button', { name: /Submit answer/ }).click()
  await goTo(page, 'Resume studio')
  await goTo(page, 'Interview lab')

  await expect(page.locator('.question-card')).not.toHaveText(firstQuestion, { timeout: 90_000 })
  await expect(page.getByRole('heading', { name: /Question 2|Follow-up/ })).toBeVisible()
  await expect(page.getByText(/TRANSCRIPT · 3 turns/)).toBeVisible()
  await expect(page.getByRole('button', { name: /Submit answer/ })).toBeEnabled()
})

test('filters, selections and unsaved form edits survive navigation on the other pages', async ({ page, account, errors }) => {
  void account; void errors
  // Applications: the status tab.
  await page.goto('/applications')
  await page.locator('.status-tabs').getByRole('button', { name: 'interview', exact: true }).click()
  await goTo(page, 'Overview')
  await goTo(page, 'Applications')
  await expect(page.locator('.status-tabs').getByRole('button', { name: 'interview', exact: true })).toHaveClass(/selected/)

  // Profile: an edit that has not been saved.
  await goTo(page, 'Profile & goals')
  const headline = page.getByLabel('Professional headline')
  await expect(headline).toBeVisible()
  await headline.fill('Data engineer, streaming systems')
  await goTo(page, 'Find roles')
  await goTo(page, 'Profile & goals')
  await expect(headline).toHaveValue('Data engineer, streaming systems')
  await expect(page.getByText('You have unsaved changes.')).toBeVisible()

  // Company intel: the search term.
  await goTo(page, 'Company intel')
  const companySearch = page.getByPlaceholder(/Search by name, industry/)
  await companySearch.fill('payments')
  await goTo(page, 'Interview lab')
  await goTo(page, 'Company intel')
  await expect(companySearch).toHaveValue('payments')

  // Search filters.
  await goTo(page, 'Find roles')
  await page.getByLabel('Location').fill('Pune')
  await page.locator('.filter-bar select').selectOption({ index: 2 })
  const experience = await page.locator('.filter-bar select').inputValue()
  await goTo(page, 'Applications')
  await goTo(page, 'Find roles')
  await expect(page.getByLabel('Location')).toHaveValue('Pune')
  await expect(page.locator('.filter-bar select')).toHaveValue(experience)
})

test('state is cleared on sign-out so the next account starts clean', async ({ page, account, errors }) => {
  void account; void errors
  await page.goto('/search')
  await page.getByPlaceholder(SEARCH_INPUT).fill('something private')
  await page.locator('.logout-button').click()
  await expect(page).toHaveURL(/\/login/)
  const leftovers = await page.evaluate(() => Object.keys(sessionStorage).filter(k => k.startsWith('talentradar:state:')))
  expect(leftovers).toEqual([])
})

test('every page in the app loads without errors', async ({ page, account, errors }) => {
  void account; void errors
  const pages: Array<[string, RegExp]> = [
    ['/dashboard', /./],
    ['/search', /Find roles that fit your direction/],
    ['/applications', /Keep every opportunity moving/],
    ['/interview', /Practice the conversation before it counts/],
    ['/resume-studio', /Build your resume, section by section/],
    ['/settings', /Set the direction of your search/],
    ['/company-intel', /Every tech employer hiring in/],
    ['/onboarding', /Tell us where you/],
    ['/agent', /./],
  ]
  for (const [path, heading] of pages) {
    const response = await page.goto(path)
    expect(response?.status(), path).toBeLessThan(400)
    await expect(page.locator('h1').first(), path).toHaveText(heading)
    await page.waitForLoadState('networkidle')
    await expect(page.locator('.form-error'), `${path} shows an error`).toHaveCount(0)
  }
})
