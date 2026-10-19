import { expect, test } from '@playwright/test'
import {
  buildPins, placementOf, splitSources, STACK_SPACING_M,
  type Bounds, type PinCollection, type PlaceableEvent,
} from '../lib/event-map'

/**
 * The events map's honesty rules (spec 6.4), run on `lib/event-map.ts` itself.
 *
 * The repo has no unit-test runner and Playwright transpiles TypeScript, so this
 * spec imports the pure rules directly and uses no `page`: it needs no browser,
 * no map and no API. What it pins is what the map must never get wrong: a solid
 * dot is only ever an exact venue, a ring is "somewhere near here", amber is only
 * the selected event and the events you are going to, and a pin is drawn once.
 */

/** `BBOX` in lib/maplibre.ts, which this file does not import because that one needs a DOM. */
const BENGALURU: Bounds = { south: 12.80, west: 77.40, north: 13.15, east: 77.80 }
const VENUE = { lat: 12.9352, lng: 77.6245 }

const event = (over: Partial<PlaceableEvent> = {}): PlaceableEvent => ({
  id: 'e1', title: 'Swift Bengaluru × Okta', starts_at: '2026-10-16T09:30:00Z', is_online: false,
  venue_name: 'Okta Bellandur', area: 'Bellandur', ...VENUE, geo_precision: 'exact', is_cancelled: false, ...over,
})

/** What the page does with a list of events: pins, then the two sources the layers draw. */
const draw = (events: PlaceableEvent[], going: string[] = [], selected: string | null = null) => {
  const model = buildPins(events, BENGALURU)
  return { model, ...splitSources(model.pins, new Set(going), selected) }
}
const ids = (collection: PinCollection) => collection.features.map(feature => feature.properties.id)
const propsOf = (collection: PinCollection, id: string) => collection.features.find(f => f.properties.id === id)?.properties

/** Metres between two points, close enough at city scale. */
const metres = (a: { lat: number; lng: number }, b: { lat: number; lng: number }) =>
  Math.hypot((a.lat - b.lat) * 111_320, (a.lng - b.lng) * 111_320 * Math.cos((b.lat * Math.PI) / 180))

test('only a literal exact is a solid dot', () => {
  const { plain } = draw([event({ id: 'exact' })])
  expect(propsOf(plain, 'exact')?.ring).toBe(0)
  expect(placementOf(event(), BENGALURU)).toEqual({ kind: 'exact', ...VENUE })

  // A precision the API did not state, or one it never sends, is not an address.
  for (const [n, precision] of ['EXACT', 'street', 'rooftop', ' exact', ''].entries()) {
    const odd = event({ id: `odd-${n}`, geo_precision: precision as unknown as PlaceableEvent['geo_precision'] })
    expect(placementOf(odd, BENGALURU).kind, `precision ${JSON.stringify(precision)}`).toBe('area')
    expect(propsOf(draw([odd]).plain, odd.id)?.ring, `precision ${JSON.stringify(precision)}`).toBe(1)
  }
})

test('a null or area precision is a ring', () => {
  const { plain, lit } = draw([event({ id: 'a', geo_precision: 'area' }), event({ id: 'n', geo_precision: null })])
  expect(propsOf(plain, 'a')?.ring).toBe(1)
  expect(propsOf(plain, 'n')?.ring).toBe(1)
  expect(lit.features).toHaveLength(0)
  // A ring does not name the venue: it is not at the venue.
  expect(draw([event({ id: 'a', geo_precision: 'area' })]).model.pins[0].note).toBe('Approximate — area only · Bellandur')
})

test('none, online and out-of-bounds events are counted, not drawn', () => {
  const { model } = draw([
    event({ id: 'drawn' }),
    event({ id: 'none', geo_precision: 'none' }),
    event({ id: 'online', is_online: true }),
    event({ id: 'online-2', is_online: true, geo_precision: 'none' }),
    event({ id: 'no-lat', lat: null }),
    event({ id: 'nan', lng: Number.NaN }),
    event({ id: 'delhi', lat: 28.61, lng: 77.2 }),
    event({ id: 'drawn' }),
  ])
  expect(model.pins.map(pin => pin.id)).toEqual(['drawn'])
  expect(model.online).toBe(2)
  expect(model.unplaced).toBe(4)
})

test('a cancelled event you are going to is not amber', () => {
  const { plain, lit } = draw(
    [event({ id: 'on', lat: 12.93 }), event({ id: 'off', lat: 12.94, is_cancelled: true })],
    ['on', 'off'],
  )
  expect(ids(lit)).toEqual(['on'])
  expect(ids(plain)).toEqual(['off'])
  expect(propsOf(plain, 'off')?.dim).toBe(1)
})

test('the selected event is amber, going or not', () => {
  const events = [event({ id: 'a', lat: 12.93 }), event({ id: 'b', lat: 12.94 }), event({ id: 'c', lat: 12.95, is_cancelled: true })]

  const first = draw(events, [], 'a')
  expect(ids(first.lit)).toEqual(['a'])
  expect(propsOf(first.lit, 'a')?.selected).toBe(1)
  expect(ids(first.plain)).toEqual(['b', 'c'])

  // The one that is open stays lit even when it was cancelled, and is still drawn dim.
  const cancelled = draw(events, [], 'c')
  expect(ids(cancelled.lit)).toEqual(['c'])
  expect(propsOf(cancelled.lit, 'c')?.dim).toBe(1)

  expect(draw(events).lit.features).toHaveLength(0)
})

test('stacks fan out, and the displaced pins carry the shared label', () => {
  const { model } = draw([
    event({ id: 'a', starts_at: '2026-10-16T09:00:00Z' }),
    event({ id: 'b', starts_at: '2026-10-16T10:00:00Z' }),
    event({ id: 'c', starts_at: '2026-10-16T11:00:00Z' }),
    event({ id: 'alone', lat: 12.99, lng: 77.55, venue_name: 'Hall Four' }),
  ])
  const stack = model.pins.filter(pin => pin.id !== 'alone')
  const alone = model.pins.find(pin => pin.id === 'alone')!

  // Fanned out: three different places, each one ring (24 m) from the shared spot.
  expect(new Set(stack.map(pin => `${pin.lat.toFixed(6)},${pin.lng.toFixed(6)}`)).size).toBe(3)
  for (const pin of stack) expect(metres(pin, VENUE), pin.id).toBeCloseTo(STACK_SPACING_M, 0)

  // Displaced, so none of them says it is the exact venue; they say the venue is shared.
  for (const pin of stack) {
    expect(pin.stacked, pin.id).toBe(3)
    expect(pin.note, pin.id).toBe('Shared venue · 3 events · Okta Bellandur')
    expect(pin.note).not.toContain('Exact venue')
    expect(pin.kind).toBe('exact')
  }

  // A pin alone at its venue is not moved and keeps the exact claim.
  expect(alone).toMatchObject({ lat: 12.99, lng: 77.55, stacked: 1, note: 'Exact venue · Hall Four' })
})

test('a stack fans out the same way whatever order the events arrive in', () => {
  const stack = ['a', 'b', 'c', 'd'].map((id, n) => event({ id, starts_at: `2026-10-16T0${n + 1}:00:00Z` }))
  const where = (events: PlaceableEvent[]) =>
    Object.fromEntries(buildPins(events, BENGALURU).pins.map(pin => [pin.id, [pin.lat, pin.lng]]))

  expect(where(stack.slice().reverse())).toEqual(where(stack))
})

test('an area ring never claims a venue, shared or not', () => {
  const { model } = draw([
    event({ id: 'x', geo_precision: 'area', area: 'Indiranagar' }),
    event({ id: 'y', geo_precision: 'area', area: 'Indiranagar' }),
  ])

  expect(model.pins.map(pin => pin.note)).toEqual(['Approximate — area only · Indiranagar', 'Approximate — area only · Indiranagar'])
  expect(model.pins.every(pin => pin.kind === 'area')).toBe(true)
})

test('a pin is never in both the plain and the amber source', () => {
  const events = [
    event({ id: 'a', lat: 12.90 }),
    event({ id: 'b', lat: 12.91, geo_precision: 'area' }),
    event({ id: 'c', lat: 12.92, is_cancelled: true }),
    event({ id: 'd', lat: 12.93 }),
    event({ id: 'e', lat: 12.94, geo_precision: null, is_cancelled: true }),
  ]
  const everyone = events.map(e => e.id)
  const selections: Array<string | null> = [null, 'a', 'c', 'e', 'missing']
  const goings = [[], ['a'], ['c', 'e'], everyone, ['missing']]

  for (const selected of selections) {
    for (const going of goings) {
      const { plain, lit } = draw(events, going, selected)
      const label = `selected ${selected}, going ${going.join(',') || 'none'}`
      expect([...ids(plain), ...ids(lit)].sort(), label).toEqual(everyone)
      expect(ids(plain).filter(id => ids(lit).includes(id)), label).toEqual([])
    }
  }
})
