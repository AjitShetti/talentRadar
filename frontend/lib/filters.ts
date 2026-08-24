// Job-search filter vocabulary. Mirrors domain/geo.py and domain/experience.py
// on the backend — keep the keys in sync with those modules.

export const INDIAN_CITIES = [
  'Bengaluru', 'Hyderabad', 'Pune', 'Chennai', 'Mumbai', 'Delhi',
  'Gurugram', 'Noida', 'Kolkata', 'Ahmedabad', 'Kochi', 'Coimbatore',
  'Chandigarh', 'Indore', 'Jaipur', 'Thiruvananthapuram',
] as const

export type ExperienceBand = { key: string; label: string; maxYears: number | null }

export const EXPERIENCE_BANDS: ExperienceBand[] = [
  { key: 'fresher', label: 'Fresher (0-1 yrs)', maxYears: 1 },
  { key: 'junior', label: '1-3 yrs', maxYears: 3 },
  { key: 'mid', label: '3-5 yrs', maxYears: 5 },
  { key: 'senior', label: '5-8 yrs', maxYears: 8 },
  { key: 'lead', label: '8+ yrs', maxYears: null },
]

/** Map a candidate's years of experience onto the band that fits. */
export function bandForYears(years: number | null | undefined): string {
  if (years == null || Number.isNaN(years)) return ''
  const band = EXPERIENCE_BANDS.find(b => b.maxYears === null || years < b.maxYears)
  return band ? band.key : ''
}

/** Match a free-text profile location against the city options we offer. */
export function matchCity(location: string | null | undefined): string {
  if (!location) return ''
  const value = location.trim().toLowerCase()
  const aliases: Record<string, string> = {
    bangalore: 'Bengaluru', blr: 'Bengaluru', bombay: 'Mumbai', 'navi mumbai': 'Mumbai',
    'new delhi': 'Delhi', ncr: 'Delhi', 'delhi ncr': 'Delhi', gurgaon: 'Gurugram',
    madras: 'Chennai', calcutta: 'Kolkata', cochin: 'Kochi', ernakulam: 'Kochi',
    trivandrum: 'Thiruvananthapuram', mohali: 'Chandigarh', 'greater noida': 'Noida',
  }
  if (aliases[value]) return aliases[value]
  const direct = INDIAN_CITIES.find(city => city.toLowerCase() === value)
  return direct || ''
}
