import { expect, test } from '@playwright/test'
import { asksInterviewTime, inputFromInterviewAt, interviewAtFromInput } from '../lib/interview-time'

/**
 * The rules behind the interview time on a tracker row, run on `lib/interview-time.ts` itself.
 * No `page`: no browser, no API. What it pins: the wall clock a person types is the wall clock
 * that comes back, in IST, whatever the API's offset, and the field only asks on a live role.
 */

test('what is typed is sent as that wall clock in IST', () => {
  expect(interviewAtFromInput('2026-11-03T14:30')).toBe('2026-11-03T14:30:00+05:30')
})

test('an empty field clears the time', () => {
  expect(interviewAtFromInput('')).toBeNull()
})

test('a stored UTC time comes back as the IST wall clock it was typed as', () => {
  expect(inputFromInterviewAt('2026-11-03T09:00:00+00:00')).toBe('2026-11-03T14:30')
})

test('a time just past IST midnight is on the IST day', () => {
  expect(inputFromInterviewAt('2026-11-02T19:00:00+00:00')).toBe('2026-11-03T00:30')
})

test('typing, saving and reading back changes nothing', () => {
  expect(inputFromInterviewAt(interviewAtFromInput('2026-12-31T23:45'))).toBe('2026-12-31T23:45')
})

test('no time and junk both read as an empty field', () => {
  expect(inputFromInterviewAt(null)).toBe('')
  expect(inputFromInterviewAt('not a time')).toBe('')
})

test('the field asks at interview stage, with or without a time', () => {
  expect(asksInterviewTime('interview', null)).toBe(true)
  expect(asksInterviewTime('interview', '2026-11-03T09:00:00+00:00')).toBe(true)
})

test('it stays on a live role that has a time and asks nothing of one without', () => {
  expect(asksInterviewTime('screening', '2026-11-03T09:00:00+00:00')).toBe(true)
  expect(asksInterviewTime('screening', null)).toBe(false)
})

test('a finished role is not asked, even with a time on it', () => {
  for (const status of ['offer', 'rejected', 'withdrawn']) {
    expect(asksInterviewTime(status, '2026-11-03T09:00:00+00:00')).toBe(false)
  }
})
