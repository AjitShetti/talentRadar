/**
 * Product analytics, as one function.
 *
 * Events go straight to PostHog's capture endpoint with `sendBeacon` — no SDK,
 * no injected script, no cookies. Without NEXT_PUBLIC_POSTHOG_KEY this does
 * nothing at all, so a local build, a fork or a preview never reports anywhere.
 *
 * The id is a random string kept in localStorage. It is not derived from the
 * account and is never sent alongside an email address.
 */
const KEY = process.env.NEXT_PUBLIC_POSTHOG_KEY || ''
const HOST = (process.env.NEXT_PUBLIC_POSTHOG_HOST || 'https://eu.i.posthog.com').replace(/\/$/, '')
const ID_KEY = 'talentradar_anon_id'

export type AnalyticsEvent =
  | 'public_role_view' | 'check_submitted' | 'signup' | 'dossier_opened'
  | 'prep_started' | 'prep_completed' | 'referral_drafted' | 'resume_tailored' | 'marked_applied'

function anonymousId(): string {
  try {
    let id = localStorage.getItem(ID_KEY)
    if (!id) {
      id = (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`)
      localStorage.setItem(ID_KEY, id)
    }
    return id
  } catch { return 'unknown' }
}

export function track(event: AnalyticsEvent, properties: Record<string, string | number | boolean | null> = {}) {
  if (!KEY || typeof window === 'undefined') return
  try {
    const body = JSON.stringify({
      api_key: KEY, event, distinct_id: anonymousId(),
      properties: { ...properties, $current_url: window.location.pathname, $process_person_profile: false },
      timestamp: new Date().toISOString(),
    })
    const url = `${HOST}/i/v0/e/`
    if (!navigator.sendBeacon?.(url, new Blob([body], { type: 'application/json' }))) {
      void fetch(url, { method: 'POST', body, headers: { 'Content-Type': 'application/json' }, keepalive: true }).catch(() => {})
    }
  } catch { /* analytics must never break the page */ }
}
