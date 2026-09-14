'use client'

/**
 * Wake the Render API as soon as any page mounts.
 *
 * The free instance sleeps after 15 minutes idle and the first request then
 * takes 30–60 s. Before this, a visitor typed their credentials, pressed sign
 * in, and watched a button spin for most of a minute with no explanation. A
 * `/health` ping on mount starts that wake while they are still reading the
 * page, and the status it publishes lets the UI say what is actually happening.
 *
 * `/health` touches neither Postgres nor Redis, so pinging it never wakes Neon.
 */

export type WarmupPhase =
  | 'idle'      // nothing checked yet
  | 'checking'  // ping in flight, not yet slow enough to mention
  | 'waking'    // ping in flight past SLOW_MS — the instance is cold
  | 'awake'
  | 'down'      // no answer inside GIVE_UP_MS

export type WarmupState = { phase: WarmupPhase; startedAt: number | null; wasCold: boolean }

// A warm instance answers /health in ~150–450 ms from India; anything past this
// is a cold start, and only then is it worth interrupting the page.
export const SLOW_MS = 2500
// Render's own cold start tops out around a minute; two covers a slow build.
export const GIVE_UP_MS = 120_000
// Render sleeps after 15 minutes idle; re-check a tab returning after 14.
const STALE_MS = 14 * 60_000
const ATTEMPT_TIMEOUT_MS = 70_000

let state: WarmupState = { phase: 'idle', startedAt: null, wasCold: false }
let inflight: Promise<boolean> | null = null
let lastOkAt = 0
const listeners = new Set<() => void>()

function set(next: Partial<WarmupState>) {
  state = { ...state, ...next }
  listeners.forEach(listener => listener())
}

export function subscribeWarmup(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
export function warmupState() { return state }
const SERVER_STATE: WarmupState = { phase: 'idle', startedAt: null, wasCold: false }
export function serverWarmupState() { return SERVER_STATE }

/** Any successful API response proves the instance is up. */
export function markApiAlive() {
  lastOkAt = Date.now()
  if (state.phase !== 'awake') set({ phase: 'awake' })
}

export function apiIsWaking() { return state.phase === 'checking' || state.phase === 'waking' }

/**
 * Idempotent: concurrent callers share one ping, and a recent success skips it.
 * Resolves true once the API answers, false if it gave up.
 */
export function wakeApi(apiUrl: string, { force = false } = {}): Promise<boolean> {
  if (inflight) return inflight
  if (!force && Date.now() - lastOkAt < STALE_MS) return Promise.resolve(true)

  const startedAt = Date.now()
  // A retry after 'down' is already known to be slow: keep the strip up
  // rather than letting it vanish for SLOW_MS, and confirm when it lands.
  const retrying = state.phase === 'down'
  set({ phase: retrying ? 'waking' : 'checking', startedAt, wasCold: retrying })
  const slow = setTimeout(() => { if (state.phase === 'checking') set({ phase: 'waking', wasCold: true }) }, SLOW_MS)

  inflight = (async () => {
    let backoff = 2000
    while (Date.now() - startedAt < GIVE_UP_MS) {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), Math.min(ATTEMPT_TIMEOUT_MS, GIVE_UP_MS - (Date.now() - startedAt)))
      try {
        // Render answers 502/503 while the container boots; only 2xx means up.
        const response = await fetch(`${apiUrl}/health`, { cache: 'no-store', signal: controller.signal })
        if (response.ok) { lastOkAt = Date.now(); set({ phase: 'awake' }); return true }
      } catch {
        // Network error or our own abort — retry until GIVE_UP_MS.
      } finally {
        clearTimeout(timer)
      }
      await new Promise(resolve => setTimeout(resolve, backoff))
      backoff = Math.min(backoff * 2, 10_000)
    }
    set({ phase: 'down' })
    return false
  })().finally(() => { clearTimeout(slow); inflight = null })

  return inflight
}
