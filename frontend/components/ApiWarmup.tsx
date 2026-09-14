'use client'

import { useEffect, useState, useSyncExternalStore } from 'react'
import { API_URL } from '@/lib/api'
import { GIVE_UP_MS, serverWarmupState, subscribeWarmup, wakeApi, warmupState } from '@/lib/api-warmup'

/**
 * Starts the API wake on mount and reports it honestly: nothing at all when the
 * instance is warm, and when it is cold, the real elapsed time against the real
 * expected range — no invented progress bar, since there is no progress to
 * measure until the instance answers.
 */
export default function ApiWarmup() {
  const { phase, startedAt, wasCold } = useSyncExternalStore(subscribeWarmup, warmupState, serverWarmupState)
  const [now, setNow] = useState(() => Date.now())
  const [showReady, setShowReady] = useState(false)

  useEffect(() => {
    void wakeApi(API_URL)
    // A tab left open past Render's idle window is talking to a sleeping
    // instance again; wakeApi skips the ping if it heard back recently.
    const onVisible = () => { if (document.visibilityState === 'visible') void wakeApi(API_URL) }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [])

  useEffect(() => {
    if (phase !== 'waking') return
    setNow(Date.now())
    const tick = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(tick)
  }, [phase])

  // Confirm the wake the user just sat through, then get out of the way.
  useEffect(() => {
    if (phase !== 'awake' || !wasCold) return
    setShowReady(true)
    const hide = setTimeout(() => setShowReady(false), 2500)
    return () => clearTimeout(hide)
  }, [phase, wasCold])

  if (phase === 'waking' && startedAt) {
    const seconds = Math.max(0, Math.floor((now - startedAt) / 1000))
    const clock = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
    return <div className="api-wake" role="status" aria-live="polite" data-phase="waking">
      <span className="api-wake-lamp" aria-hidden="true" />
      <div className="api-wake-copy">
        <strong>{seconds < 60 ? 'Starting the server' : 'Still starting — slower than usual'}</strong>
        <span>It sleeps when nobody has used it for 15 minutes. Waking usually takes 30–60 seconds.</span>
      </div>
      <span className="api-wake-clock" aria-label={`${seconds} seconds elapsed`}>{clock}</span>
    </div>
  }

  if (phase === 'down') {
    return <div className="api-wake" role="alert" data-phase="down">
      <span className="api-wake-lamp" aria-hidden="true" />
      <div className="api-wake-copy">
        <strong>The server isn’t answering</strong>
        <span>No response after {Math.round(GIVE_UP_MS / 60_000)} minutes. It may be redeploying or down — searches and saves will fail until it’s back.</span>
      </div>
      <button type="button" className="api-wake-retry" onClick={() => void wakeApi(API_URL, { force: true })}>Try again</button>
    </div>
  }

  if (phase === 'awake' && showReady) {
    return <div className="api-wake" role="status" aria-live="polite" data-phase="ready">
      <span className="api-wake-lamp" aria-hidden="true" />
      <div className="api-wake-copy"><strong>Server is up</strong></div>
    </div>
  }

  return null
}
