/**
 * The cities the Events page names. Only Bengaluru is collected: the relevance rules
 * (domain/event_relevance.py) keep Bengaluru events and nothing else, so another city
 * is shown as "coming soon" and requests nothing (spec 2026-11-23, 4.6).
 */
export type EventCity = { id: string; name: string; live: boolean }

export const EVENT_CITIES: ReadonlyArray<EventCity> = [
  { id: 'bengaluru', name: 'Bengaluru', live: true },
  { id: 'mumbai', name: 'Mumbai', live: false },
  { id: 'delhi-ncr', name: 'Delhi NCR', live: false },
  { id: 'hyderabad', name: 'Hyderabad', live: false },
  { id: 'pune', name: 'Pune', live: false },
  { id: 'chennai', name: 'Chennai', live: false },
]

export const LIVE_CITY: EventCity = EVENT_CITIES.find(city => city.live) ?? EVENT_CITIES[0]
