/**
 * The events map's rules, with no DOM, no MapLibre and no React so they can be
 * run on their own: which events have a place on the map, how exactly they may
 * be drawn, how events that share a spot are fanned out, and what the two
 * GeoJSON sources are made of.
 *
 * Honesty rules (spec 6.4):
 *  - Only an event whose precision is literally `exact` is drawn as a solid dot.
 *    `area` (and a precision the API did not say) is a hollow ring: "somewhere
 *    near here".
 *  - Precision `none`, an online event, an event with no usable position, and a
 *    position outside the city's bounds are not drawn at all. They are counted so
 *    the page can say how many are missing.
 *  - Events that share one venue are fanned out around it so each can be clicked.
 *    A fanned-out pin is drawn tens of metres off the venue, so it says "Shared
 *    venue", not "Exact venue".
 */

/** south/west/north/east, as `BBOX` in `lib/maplibre.ts`. Passed in so this file imports nothing. */
export type Bounds = { readonly south: number; readonly west: number; readonly north: number; readonly east: number }

/** The part of an `EventCard` the map reads. An `EventCard` satisfies it as it is. */
export type PlaceableEvent = {
  id: string
  title: string
  starts_at: string
  is_online: boolean
  venue_name: string | null
  area: string | null
  lat: number | null
  lng: number | null
  geo_precision: 'exact' | 'area' | 'none' | null
  is_cancelled: boolean
}

export type PinKind = 'exact' | 'area'

export type Placement =
  | { kind: 'online' }
  | { kind: 'unplaced' }
  | { kind: PinKind; lat: number; lng: number }

const isCoordinate = (value: number | null): value is number => typeof value === 'number' && Number.isFinite(value)

export function inBounds(lat: number, lng: number, bounds: Bounds): boolean {
  return lat >= bounds.south && lat <= bounds.north && lng >= bounds.west && lng <= bounds.east
}

/**
 * Where, and how exactly, one event may be drawn.
 *
 * Exact only when the API says `exact` and gives a position. Everything else that
 * has a position is an area ring, so a missing or unexpected precision can never
 * produce a solid dot.
 */
export function placementOf(event: PlaceableEvent, bounds: Bounds): Placement {
  if (event.is_online) return { kind: 'online' }
  if (event.geo_precision === 'none') return { kind: 'unplaced' }
  const { lat, lng } = event
  if (!isCoordinate(lat) || !isCoordinate(lng) || !inBounds(lat, lng, bounds)) return { kind: 'unplaced' }
  return { kind: event.geo_precision === 'exact' ? 'exact' : 'area', lat, lng }
}

export type EventPin = {
  id: string
  lat: number
  lng: number
  kind: PinKind
  title: string
  /** The venue (exact) or the area (ring) the note names; `null` when the event states neither. */
  place: string | null
  /** What the tooltip says under the title: the precision, then where. */
  note: string
  /** How many events share this pin's spot, itself included. Above 1 the pin is drawn off the spot. */
  stacked: number
  cancelled: boolean
  startsAt: string
}

export type PinModel = {
  /** Events with a place on the map, fanned out where several share one. */
  pins: EventPin[]
  /** Online events: not on the map, counted for the chip. */
  online: number
  /** In-person events with no usable place: not on the map. */
  unplaced: number
}

export const PRECISION_NOTE: Record<PinKind, string> = {
  exact: 'Exact venue',
  area: 'Approximate — area only',
}

/** What an exact pin says when it shares its venue and so is drawn beside it, not on it. */
export const sharedVenueNote = (count: number): string => `Shared venue · ${count} events`

/** The place a note names. A venue is named only for an exact pin: an area ring is not at the venue. */
const placeOf = (kind: PinKind, event: Pick<PlaceableEvent, 'venue_name' | 'area'>): string | null =>
  (kind === 'exact' ? event.venue_name || event.area : event.area) || null

/**
 * The tooltip line, from the precision and the place. `stacked` is how many events share the pin's
 * spot: an exact pin among several is displaced, so it must not claim to be the exact venue.
 */
export function describePin(kind: PinKind, place: string | null, stacked = 1): string {
  const claim = kind === 'exact' && stacked > 1 ? sharedVenueNote(stacked) : PRECISION_NOTE[kind]
  return [claim, place].filter(Boolean).join(' · ')
}

export function pinNote(kind: PinKind, event: Pick<PlaceableEvent, 'venue_name' | 'area'>, stacked = 1): string {
  return describePin(kind, placeOf(kind, event), stacked)
}

// ── Several events at one spot ───────────────────────────────────────────────
/** Pins within about a metre of each other (five decimals of a degree) are one stack. */
const STACK_DECIMALS = 5
/** How far from the shared spot the first ring of a stack sits, and the gap between rings, in metres. */
export const STACK_SPACING_M = 24
const M_PER_DEG_LAT = 111_320

const stackKey = (lat: number, lng: number) => `${lat.toFixed(STACK_DECIMALS)},${lng.toFixed(STACK_DECIMALS)}`

/**
 * Where the `index`-th of `count` events at one spot sits, as metres east and
 * north of it. One event stays on the spot. Several fan out on concentric rings
 * (6 on the first, 12 on the second, ...), each ring spread evenly and starting
 * due north, so two events sit on opposite sides and three form a triangle.
 */
export function stackOffset(index: number, count: number): { east: number; north: number } {
  if (count <= 1) return { east: 0, north: 0 }
  let ring = 1
  let first = 0
  while (index >= first + 6 * ring) { first += 6 * ring; ring += 1 }
  const onRing = Math.min(6 * ring, count - first)
  const angle = (2 * Math.PI * (index - first)) / onRing
  const radius = ring * STACK_SPACING_M
  return { east: radius * Math.sin(angle), north: radius * Math.cos(angle) }
}

/** Fan out pins that share a spot so each can be seen and clicked. Pins alone on their spot are untouched. */
export function spreadStacks(pins: readonly EventPin[]): EventPin[] {
  const stacks = new Map<string, EventPin[]>()
  for (const pin of pins) {
    const key = stackKey(pin.lat, pin.lng)
    const stack = stacks.get(key)
    if (stack) stack.push(pin)
    else stacks.set(key, [pin])
  }
  const moved = new Map<string, EventPin>()
  stacks.forEach(stack => {
    if (stack.length < 2) return
    // Soonest first, then by id: the same stack always fans out the same way.
    const ordered = stack.slice().sort((a, b) => (a.startsAt < b.startsAt ? -1 : a.startsAt > b.startsAt ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    ordered.forEach((pin, index) => {
      const { east, north } = stackOffset(index, ordered.length)
      const dLat = north / M_PER_DEG_LAT
      const dLng = east / (M_PER_DEG_LAT * Math.cos((pin.lat * Math.PI) / 180))
      moved.set(pin.id, {
        ...pin,
        lat: pin.lat + dLat,
        lng: pin.lng + dLng,
        stacked: ordered.length,
        note: describePin(pin.kind, pin.place, ordered.length),
      })
    })
  })
  return pins.map(pin => moved.get(pin.id) ?? pin)
}

/** Everything the map needs from a page of events. */
export function buildPins(events: readonly PlaceableEvent[], bounds: Bounds): PinModel {
  const pins: EventPin[] = []
  let online = 0
  let unplaced = 0
  const seen = new Set<string>()
  for (const event of events) {
    if (seen.has(event.id)) continue
    seen.add(event.id)
    const placement = placementOf(event, bounds)
    if (placement.kind === 'online') { online += 1; continue }
    if (placement.kind === 'unplaced') { unplaced += 1; continue }
    pins.push({
      id: event.id,
      lat: placement.lat,
      lng: placement.lng,
      kind: placement.kind,
      title: event.title,
      place: placeOf(placement.kind, event),
      note: pinNote(placement.kind, event),
      stacked: 1,
      cancelled: event.is_cancelled,
      startsAt: event.starts_at,
    })
  }
  return { pins: spreadStacks(pins), online, unplaced }
}

// ── The two GeoJSON sources ──────────────────────────────────────────────────
export type PinProps = {
  id: string
  title: string
  note: string
  /** 0 = solid dot (exact), 1 = hollow ring (area). */
  ring: 0 | 1
  selected: 0 | 1
  /** 1 for a cancelled event, drawn dim. */
  dim: 0 | 1
}

export type PinCollection = {
  type: 'FeatureCollection'
  features: Array<{ type: 'Feature'; geometry: { type: 'Point'; coordinates: [number, number] }; properties: PinProps }>
}

const toFeature = (pin: EventPin, selectedId: string | null): PinCollection['features'][number] => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [pin.lng, pin.lat] },
  properties: {
    id: pin.id,
    title: pin.title,
    note: pin.note,
    ring: pin.kind === 'exact' ? 0 : 1,
    selected: pin.id === selectedId ? 1 : 0,
    dim: pin.cancelled ? 1 : 0,
  },
})

/**
 * Split the pins into what clusters and what never does.
 *
 * `lit` holds the selected event and the events the user is going to (never a
 * cancelled one): the only amber on the map, so they are always their own pin and
 * the lamp is never swallowed by a cluster. `plain` is everything else, and it is
 * what a cluster's count is made of, so an event is never counted in both.
 */
export function splitSources(
  pins: readonly EventPin[],
  goingIds: ReadonlySet<string>,
  selectedId: string | null,
): { plain: PinCollection; lit: PinCollection } {
  const plain: PinCollection = { type: 'FeatureCollection', features: [] }
  const lit: PinCollection = { type: 'FeatureCollection', features: [] }
  for (const pin of pins) {
    const isLit = pin.id === selectedId || (goingIds.has(pin.id) && !pin.cancelled)
    ;(isLit ? lit : plain).features.push(toFeature(pin, selectedId))
  }
  return { plain, lit }
}

/** The diameter in px of a cluster button, by how many events it holds. */
export function clusterSize(count: number): number {
  return count < 10 ? 30 : count < 50 ? 36 : count < 200 ? 42 : 48
}
