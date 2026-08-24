'use client'

import { Dispatch, SetStateAction, useEffect, useRef, useState } from 'react'

const PREFIX = 'talentradar:state:'

export type Scope = 'session' | 'local'

function backing(scope: Scope): Storage | null {
  if (typeof window === 'undefined') return null
  try { return scope === 'local' ? window.localStorage : window.sessionStorage } catch { return null }
}

export function readPersisted<T>(key: string, scope: Scope = 'session'): T | undefined {
  const store = backing(scope)
  if (!store) return undefined
  try { const raw = store.getItem(PREFIX + key); return raw == null ? undefined : JSON.parse(raw) as T } catch { return undefined }
}

export function writePersisted(key: string, value: unknown, scope: Scope = 'session') {
  const store = backing(scope)
  if (!store) return
  // Quota or private-mode failures degrade to in-memory state rather than breaking the page.
  try { store.setItem(PREFIX + key, JSON.stringify(value)) } catch { /* ignore */ }
}

export function clearPersistedState() {
  for (const scope of ['session', 'local'] as const) {
    const store = backing(scope)
    if (!store) continue
    try {
      const keys: string[] = []
      for (let i = 0; i < store.length; i++) { const k = store.key(i); if (k && k.startsWith(PREFIX)) keys.push(k) }
      keys.forEach(k => store.removeItem(k))
    } catch { /* ignore */ }
  }
}

/**
 * useState that survives client-side navigation and tab reloads.
 * Storage is read after mount (never during render) so server and client
 * markup match; writes are held back until that first read lands, otherwise
 * the initial value would clobber what was stored.
 */
export function usePersistentState<T>(key: string, initial: T, scope: Scope = 'session'): [T, Dispatch<SetStateAction<T>>, boolean] {
  const [value, setValue] = useState<T>(initial)
  const [hydrated, setHydrated] = useState(false)

  useEffect(() => {
    const stored = readPersisted<T>(key, scope)
    if (stored !== undefined) setValue(stored)
    setHydrated(true)
  }, [key, scope])

  useEffect(() => { if (hydrated) writePersisted(key, value, scope) }, [hydrated, key, scope, value])

  return [value, setValue, hydrated]
}

/** Latest value of a persistent state, readable from async callbacks without re-subscribing. */
export function useLatest<T>(value: T) {
  const ref = useRef(value)
  ref.current = value
  return ref
}
