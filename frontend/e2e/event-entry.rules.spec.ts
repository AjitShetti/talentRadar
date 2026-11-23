import { expect, test } from '@playwright/test'
import { EVENT_CITIES, LIVE_CITY } from '../lib/event-cities'
import { entryCode, entryLabel, registerHref } from '../lib/event-entry'

/**
 * What an event says about entry, where "Register" goes, and which cities are live
 * (docs/superpowers/specs/2026-11-23-events-coverage-design.md, 4.3 to 4.6).
 * Pure rules: no page, no browser, no API.
 */

test('entry reads free, the price, paid, or not stated', () => {
  expect(entryLabel({ is_free: true, price: null })).toBe('Free')
  expect(entryLabel({ is_free: true, price: '₹499' })).toBe('Free')
  expect(entryLabel({ is_free: false, price: '₹499' })).toBe('₹499')
  expect(entryLabel({ is_free: null, price: '₹499' })).toBe('₹499')
  expect(entryLabel({ is_free: false, price: null })).toBe('Paid')
  expect(entryLabel({ is_free: null, price: null })).toBe('Entry not stated')
  expect(entryLabel({ is_free: null })).toBe('Entry not stated')
})

test('a row shows an entry code only when the source said something', () => {
  expect(entryCode({ is_free: true, price: null })).toBe('Free')
  expect(entryCode({ is_free: false, price: null })).toBe('Paid')
  expect(entryCode({ is_free: null, price: null })).toBeNull()
})

test('register goes to the registration page first, then the event page', () => {
  expect(registerHref({ register_url: 'https://konfhub.com/x', url: 'https://gdg.community.dev/e/' })).toBe('https://konfhub.com/x')
  expect(registerHref({ register_url: null, url: 'https://luma.com/abc' })).toBe('https://luma.com/abc')
  expect(registerHref({ register_url: 'javascript:alert(1)', url: 'https://luma.com/abc' })).toBe('https://luma.com/abc')
  expect(registerHref({ register_url: null, url: null })).toBeNull()
  expect(registerHref({ url: null })).toBeNull()
})

test('Bengaluru is the one live city; the others are coming soon', () => {
  expect(EVENT_CITIES.filter(city => city.live).map(city => city.id)).toEqual(['bengaluru'])
  expect(LIVE_CITY.name).toBe('Bengaluru')
  expect(EVENT_CITIES.map(city => city.name)).toEqual(['Bengaluru', 'Mumbai', 'Delhi NCR', 'Hyderabad', 'Pune', 'Chennai'])
})
