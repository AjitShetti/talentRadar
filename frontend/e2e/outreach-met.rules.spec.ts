import { expect, test } from '@playwright/test'
import { alreadyRecorded, findMet, metCompany, metHappenedAt, metPersonFields, metRef, metTouch, personNamed } from '../lib/outreach-met'
import type { MetAtEvent, OutreachPerson, OutreachTouch } from '../lib/api'

/**
 * The rules behind /outreach?add=event_contact:<uuid>, run on `lib/outreach-met.ts` itself.
 *
 * Like outreach.rules.spec.ts it imports the pure helpers and uses no `page`: no browser, no API.
 * What it pins: a malformed link starts nothing, a person already in a thread is not added twice,
 * a meeting is recorded once, and a date the server would refuse is never sent.
 */

const REF = 'event_contact:3f2c1d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f'

const met = (over: Partial<MetAtEvent> = {}): MetAtEvent => ({
  source_ref: REF, name: 'Asha Verma', title: 'Staff Engineer', company_name: 'Lumen Works', company_id: null,
  linkedin_url: 'https://www.linkedin.com/in/asha-verma-example', x_url: 'https://x.com/asha_example',
  github_url: 'https://github.com/asha-example', email: 'asha@example.test', note: null,
  event_id: 'e1', event_title: 'Swift Bengaluru × Okta', event_url: 'https://luma.com/swift-okta',
  met_on: '2026-10-16', created_at: '2026-10-16T09:30:00+00:00', context_line: 'Met at Swift Bengaluru × Okta on 16 Oct',
  ...over,
})

const person = (over: Partial<OutreachPerson> = {}): OutreachPerson => ({
  id: 'p1', contact_id: null, name: 'Asha Verma', title: null, kind: 'referral', state: 'planned', verified: false,
  user_saved: true, source_url: null, source_host: null, channels: [], first_sent_at: null, last_outbound_at: null,
  follow_ups_sent: 0, next_follow_up_at: null, replied_at: null, draft: null,
  ...over,
})

const touch = (over: Partial<OutreachTouch> = {}): OutreachTouch => ({
  id: 't1', person_id: 'p1', person_name: 'Asha Verma', channel: 'event', direction: 'in', kind: 'reply',
  note: 'Met at Swift Bengaluru × Okta on 16 Oct', body: null, happened_at: '2026-10-15T18:30:00+00:00',
  ...over,
})

test('only a well-formed event_contact reference starts an import', () => {
  expect(metRef(REF)).toBe(REF)
  expect(metRef(REF.toUpperCase().replace('EVENT_CONTACT', 'event_contact'))).not.toBeNull()
  for (const bad of [null, '', 'event_contact:', 'event_contact:not-a-uuid', 'thread:3f2c1d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f', `${REF}x`, ` ${REF}`]) {
    expect(metRef(bad)).toBeNull()
  }
})

test('the link finds its own row and no other', () => {
  const other = met({ source_ref: 'event_contact:00000000-0000-4000-8000-000000000000', name: 'Ravi Menon' })
  expect(findMet([other, met()], REF)?.name).toBe('Asha Verma')
  expect(findMet([other], REF)).toBeNull()
  expect(findMet([], REF)).toBeNull()
})

test('a company on the map wins over a typed name; with neither, the user is asked', () => {
  expect(metCompany(met({ company_id: 'c1' }))).toEqual({ company_id: 'c1' })
  expect(metCompany(met())).toEqual({ company_name: 'Lumen Works' })
  expect(metCompany(met({ company_name: '  Lumen Works  ' }))).toEqual({ company_name: 'Lumen Works' })
  expect(metCompany(met({ company_name: '   ' }))).toBeNull()
  expect(metCompany(met({ company_name: null }))).toBeNull()
})

test('a person is the same person whatever the case or spacing of the name', () => {
  expect(personNamed([person()], 'asha  verma')?.id).toBe('p1')
  expect(personNamed([person()], ' ASHA VERMA ')?.id).toBe('p1')
  expect(personNamed([person()], 'Ravi Menon')).toBeNull()
  expect(personNamed([], 'Asha Verma')).toBeNull()
})

test('the thread keeps what Outreach can hold and nothing is invented', () => {
  expect(metPersonFields(met())).toEqual({
    name: 'Asha Verma', title: 'Staff Engineer', email: 'asha@example.test', linkedin_url: 'https://www.linkedin.com/in/asha-verma-example',
  })
  // The X and GitHub links have nowhere to go.
  expect(Object.keys(metPersonFields(met()))).not.toContain('x_url')
  expect(metPersonFields(met(), false)).toEqual({ name: 'Asha Verma', title: 'Staff Engineer' })
  expect(metPersonFields(met({ title: null, email: null, linkedin_url: null }))).toEqual({ name: 'Asha Verma' })
})

test('a meeting already on the timeline is not recorded again', () => {
  expect(alreadyRecorded([touch()], 'p1', met())).toBe(true)
  expect(alreadyRecorded([], 'p1', met())).toBe(false)
  // Another person, another channel, or another event's line is a different meeting.
  expect(alreadyRecorded([touch({ person_id: 'p2' })], 'p1', met())).toBe(false)
  expect(alreadyRecorded([touch({ channel: 'linkedin' })], 'p1', met())).toBe(false)
  expect(alreadyRecorded([touch({ note: 'Met at Rust Night on 2 Nov' })], 'p1', met())).toBe(false)
})

test('the meeting is dated the start of its IST day, and only when the server would accept that', () => {
  const now = new Date('2026-10-17T05:00:00Z')
  // 00:00 IST on the 16th is 18:30 UTC on the 15th.
  expect(metHappenedAt('2026-10-16', now)).toBe('2026-10-15T18:30:00.000Z')
  // The event is today: its day began before now.
  expect(metHappenedAt('2026-10-17', now)).toBe('2026-10-16T18:30:00.000Z')
  // Not yet started (future): the server refuses a future touch, so it is dated now.
  expect(metHappenedAt('2026-10-18', now)).toBeUndefined()
  // Older than 30 days: also refused.
  expect(metHappenedAt('2026-09-01', now)).toBeUndefined()
  expect(metHappenedAt('2026-09-17', now)).toBeUndefined()
  expect(metHappenedAt('2026-09-18', now)).toBe('2026-09-17T18:30:00.000Z')
  expect(metHappenedAt('16 Oct', now)).toBeUndefined()
})

test('the touch is a conversation that happened, never a message sent', () => {
  const now = new Date('2026-10-17T05:00:00Z')
  expect(metTouch('p1', met(), now)).toEqual({
    person_id: 'p1', channel: 'event', direction: 'in', note: 'Met at Swift Bengaluru × Okta on 16 Oct', happened_at: '2026-10-15T18:30:00.000Z',
  })
})
