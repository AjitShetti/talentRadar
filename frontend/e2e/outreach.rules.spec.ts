import { expect, test } from '@playwright/test'
import {
  canMarkSent, followUpWait, isStaleError, MAX_FOLLOW_UPS, NEXT_STEP_DONE, NEXT_STEP_MAX, nextStepPatch, openSourceNote, personTone,
} from '../lib/outreach'
import type { IssueSnapshot, OutreachPerson } from '../lib/api'

/**
 * The outreach drawer's display rules, run on `lib/outreach.ts` itself.
 *
 * The repo has no unit-test runner and Playwright transpiles TypeScript, so this spec
 * imports the pure helpers directly and uses no `page`: it needs no browser and no API.
 * What it pins is what the drawer must never get wrong: a lamp that disagrees with the
 * Due list, a follow-up button offered before the follow-up is due, an Open source
 * section that says nothing, and a next step that cannot be finished.
 */

// 14:30 IST on 15 Oct 2026, written the way the API writes a UTC instant.
const DUE_ON = '2026-10-15T09:00:00+00:00'

const person = (over: Partial<OutreachPerson> = {}): OutreachPerson => ({
  id: 'p1', contact_id: null, name: 'Asha Verma', title: null, kind: 'founder', state: 'waiting', verified: false,
  user_saved: false, source_url: null, source_host: null, channels: [], first_sent_at: '2026-10-10T06:00:00+00:00',
  last_outbound_at: '2026-10-10T06:00:00+00:00', follow_ups_sent: 0, next_follow_up_at: DUE_ON, replied_at: null, draft: null,
  ...over,
})

test('the lamp turns amber on the same IST day the Due list does', () => {
  const waiting = person()
  // 23:59 IST on the 14th: the follow-up is dated tomorrow.
  expect(personTone(waiting, new Date('2026-10-14T18:29:00Z'))).toBe('waiting')
  // 00:00 IST on the 15th: due, though the minute it was dated (14:30 IST) is hours away.
  expect(personTone(waiting, new Date('2026-10-14T18:30:00Z'))).toBe('due')
  expect(personTone(waiting, new Date('2026-10-15T03:30:00Z'))).toBe('due')
  expect(personTone(waiting, new Date('2026-10-17T03:30:00Z'))).toBe('due')
})

test('the lamp ignores the timezone of the browser and reads a date with no zone as UTC', () => {
  const naive = person({ next_follow_up_at: '2026-10-15T09:00:00' })
  expect(personTone(naive, new Date('2026-10-14T18:30:00Z'))).toBe('due')
  expect(personTone(naive, new Date('2026-10-14T18:29:59Z'))).toBe('waiting')
})

test('only a waiting person with a date can be due', () => {
  const now = new Date('2026-10-20T06:00:00Z')
  expect(personTone(person({ state: 'planned', next_follow_up_at: null }), now)).toBe('idle')
  expect(personTone(person({ state: 'replied', next_follow_up_at: null, replied_at: '2026-10-12T06:00:00+00:00' }), now)).toBe('replied')
  expect(personTone(person({ state: 'dropped' }), now)).toBe('idle')
  expect(personTone(person({ next_follow_up_at: null }), now)).toBe('waiting')
})

test('before a follow-up is due the row says when, in place of the draft button', () => {
  const early = person()
  expect(followUpWait(early)).toBe('Follow-up due 15 Oct')
  expect(followUpWait(person({ follow_ups_sent: 1, next_follow_up_at: '2026-10-22T06:00:00+00:00' }))).toBe('Follow-up due 22 Oct')

  // Once the server offers the draft there is nothing to wait for.
  expect(followUpWait(person({ draft: 'follow_up' }))).toBeNull()
  // Nobody to wait on: not written to, replied, out of nudges, no date.
  expect(followUpWait(person({ state: 'planned', next_follow_up_at: null, draft: 'first' }))).toBeNull()
  expect(followUpWait(person({ state: 'replied', next_follow_up_at: null }))).toBeNull()
  expect(followUpWait(person({ follow_ups_sent: MAX_FOLLOW_UPS, next_follow_up_at: null }))).toBeNull()
  expect(followUpWait(person({ next_follow_up_at: null }))).toBeNull()
})

test('"I sent it" stays for a message the person really sent, early or not', () => {
  expect(canMarkSent(person({ state: 'planned', next_follow_up_at: null, draft: 'first' }))).toBe(true)
  expect(canMarkSent(person({ draft: 'follow_up' }))).toBe(true)
  // The follow-up is not due, so no draft is offered, but they may have written anyway.
  expect(canMarkSent(person())).toBe(true)
  expect(canMarkSent(person({ state: 'replied', next_follow_up_at: null }))).toBe(false)
  expect(canMarkSent(person({ state: 'dropped', next_follow_up_at: null }))).toBe(false)
  expect(canMarkSent(person({ state: 'cold', follow_ups_sent: MAX_FOLLOW_UPS, next_follow_up_at: null }))).toBe(false)
})

test('done clears the step and its date; saving keeps both', () => {
  expect(NEXT_STEP_DONE).toEqual({ next_step: null, next_step_at: null })
  expect(nextStepPatch('  Call on Thursday ', '2026-10-22')).toEqual({ next_step: 'Call on Thursday', next_step_at: '2026-10-22' })
  // A step without a date is kept; it is simply never owed.
  expect(nextStepPatch('Send the repo link', '')).toEqual({ next_step: 'Send the repo link', next_step_at: null })
  // A date with no step has nothing to remind about, so it clears like Done.
  expect(nextStepPatch('   ', '2026-10-22')).toEqual(NEXT_STEP_DONE)
  expect(nextStepPatch('x'.repeat(NEXT_STEP_MAX + 30), '')?.next_step).toHaveLength(NEXT_STEP_MAX)
})

test('only a refusal about the thread itself makes the drawer read it again', () => {
  expect(isStaleError(Object.assign(new Error('They replied.'), { status: 409 }))).toBe(true)
  expect(isStaleError(Object.assign(new Error('Thread not found'), { status: 404 }))).toBe(true)
  expect(isStaleError(Object.assign(new Error('Choose who to write to.'), { status: 422 }))).toBe(false)
  expect(isStaleError(Object.assign(new Error('Server error'), { status: 500 }))).toBe(false)
  expect(isStaleError(new Error('Failed to fetch'))).toBe(false)
  expect(isStaleError('409')).toBe(false)
  expect(isStaleError(null)).toBe(false)
})

const issue = { repo: 'tessel-ai/engine', number: 41, title: 'Flaky shutdown test', url: 'https://github.com/tessel-ai/engine/issues/41', labels: ['good first issue'], updated_at: '2026-10-01T00:00:00Z', comments: 2 }
const snapshot = (over: Partial<IssueSnapshot> = {}): IssueSnapshot => ({
  org: 'tessel-ai', status: 'ok', total_count: 1, issues: [issue], fetched_at: '2026-10-14T02:40:00+00:00', ...over,
})
const thread = (over: { github_org?: string | null; issues?: IssueSnapshot | null; company?: boolean } = {}) => ({
  company_name: 'Tessel AI',
  company: over.company === false ? null : { github_org: over.github_org === undefined ? 'tessel-ai' : over.github_org },
  issues: over.issues === undefined ? null : over.issues,
})

test('the Open source section has one honest line for every state', () => {
  // A name-only thread has no company; the drawer's other hint already says why.
  expect(openSourceNote(thread({ company: false }))).toBeNull()

  expect(openSourceNote(thread({ github_org: null }))).toBe('No GitHub organisation on record for Tessel AI, so there are no issues to look at.')
  expect(openSourceNote(thread({ github_org: '' }))).toBe('No GitHub organisation on record for Tessel AI, so there are no issues to look at.')
  expect(openSourceNote(thread())).toBe('Not checked yet. Good first issues in tessel-ai are looked up once a day.')

  expect(openSourceNote(thread({ issues: snapshot() }))).toBe(
    "Good first issues in tessel-ai's repositories, checked 14 Oct. Read the issue first; the comment is yours to post.",
  )
  expect(openSourceNote(thread({ issues: snapshot({ status: 'not_found', total_count: 0, issues: [] }) }))).toBe('No public issues found. Checked 14 Oct.')
  expect(openSourceNote(thread({ issues: snapshot({ total_count: 0, issues: [] }) }))).toBe('No good first issues open right now. Checked 14 Oct.')
})

test('a failed refresh keeps the old list and says so', () => {
  const failedWithList = openSourceNote(thread({ issues: snapshot({ status: 'error' }) }))
  expect(failedWithList).toBe('Refreshing failed on 14 Oct, so these are from the last good check. Read the issue first; the comment is yours to post.')
  const failedEmpty = openSourceNote(thread({ issues: snapshot({ status: 'error', total_count: 0, issues: [] }) }))
  expect(failedEmpty).toBe('Refreshing failed on 14 Oct. We will try again at the next daily check.')
  // A rate limit is a failed refresh as far as the reader can tell.
  expect(openSourceNote(thread({ issues: snapshot({ status: 'rate_limited', issues: [] }) }))).toContain('Refreshing failed on 14 Oct')
})
