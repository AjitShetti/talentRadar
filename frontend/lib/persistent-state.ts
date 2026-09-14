'use client'

import { Dispatch, SetStateAction, useCallback, useEffect, useRef, useState } from 'react'

const PREFIX = 'talentradar:state:'

export type Scope = 'session' | 'local'

function backing(scope: Scope): Storage | null {
  if (typeof window === 'undefined') return null
  try { return scope === 'local' ? window.localStorage : window.sessionStorage } catch { return null }
}

// Module memory outlives a page component: client-side navigation keeps the
// bundle loaded, so this mirror is what a page remounting reads first. Storage
// is what survives a reload, and the fallback when storage is unavailable.
const memory = new Map<string, { raw: string; value: unknown }>()
const listeners = new Map<string, Set<() => void>>()

const slot = (key: string, scope: Scope) => `${scope}:${PREFIX}${key}`

function notify(id: string) {
  listeners.get(id)?.forEach(listener => listener())
}

function subscribe(id: string, listener: () => void) {
  let set = listeners.get(id)
  if (!set) { set = new Set(); listeners.set(id, set) }
  set.add(listener)
  return () => { set!.delete(listener) }
}

export function readPersisted<T>(key: string, scope: Scope = 'session'): T | undefined {
  const id = slot(key, scope)
  const cached = memory.get(id)
  if (cached) return cached.value as T
  const store = backing(scope)
  if (!store) return undefined
  try {
    const raw = store.getItem(PREFIX + key)
    if (raw == null) return undefined
    const value = JSON.parse(raw) as T
    memory.set(id, { raw, value })
    return value
  } catch { return undefined }
}

export function writePersisted(key: string, value: unknown, scope: Scope = 'session') {
  const id = slot(key, scope)
  let raw: string
  try { raw = JSON.stringify(value) } catch { return }
  if (memory.get(id)?.raw === raw) return
  memory.set(id, { raw, value })
  // Quota or private-mode failures degrade to the in-memory mirror rather than breaking the page.
  try { backing(scope)?.setItem(PREFIX + key, raw) } catch { /* ignore */ }
  notify(id)
}

export function clearPersistedState() {
  const ids = Array.from(memory.keys())
  memory.clear()
  for (const scope of ['session', 'local'] as const) {
    const store = backing(scope)
    if (!store) continue
    try {
      const keys: string[] = []
      for (let i = 0; i < store.length; i++) { const k = store.key(i); if (k && k.startsWith(PREFIX)) keys.push(k) }
      keys.forEach(k => store.removeItem(k))
    } catch { /* ignore */ }
  }
  ids.forEach(notify)
}

/**
 * useState that survives client-side navigation and tab reloads.
 *
 * The setter writes to storage immediately rather than from an effect, so an
 * async callback that resolves after the user has moved to another page still
 * lands — a search or an interview answer that finishes while its page is
 * unmounted is there when the user comes back. Mounted copies subscribe, which
 * keeps them current too.
 *
 * Storage is read after mount (never during render) so server and client
 * markup match; `hydrated` says when that read has landed.
 */
export function usePersistentState<T>(key: string, initial: T, scope: Scope = 'session'): [T, Dispatch<SetStateAction<T>>, boolean] {
  const [value, setValue] = useState<T>(initial)
  const [hydrated, setHydrated] = useState(false)
  const initialRef = useRef(initial)

  useEffect(() => {
    const sync = () => {
      const stored = readPersisted<T>(key, scope)
      setValue(stored === undefined ? initialRef.current : stored)
    }
    sync()
    setHydrated(true)
    return subscribe(slot(key, scope), sync)
  }, [key, scope])

  const set = useCallback<Dispatch<SetStateAction<T>>>(next => {
    // Resolve updaters against what is stored, not this component's copy,
    // which may be a render behind or belong to an unmounted page.
    const stored = readPersisted<T>(key, scope)
    const previous = stored === undefined ? initialRef.current : stored
    const resolved = typeof next === 'function' ? (next as (prev: T) => T)(previous) : next
    writePersisted(key, resolved, scope)
  }, [key, scope])

  return [value, set, hydrated]
}

/** Latest value of a persistent state, readable from async callbacks without re-subscribing. */
export function useLatest<T>(value: T) {
  const ref = useRef(value)
  ref.current = value
  return ref
}

// ── Requests that outlive their page ───────────────────────────────────────
// Deliberately in memory only: a reload aborts the request, and a "loading"
// flag left in storage would spin forever.

const running = new Map<string, number>()
const TASK = (key: string) => `task:${key}`

/**
 * Run `work` under `key` so a page can tell, even after remounting, that the
 * request it started is still out. Pair with `useTaskRunning`.
 */
export async function runTask<T>(key: string, work: () => Promise<T>): Promise<T> {
  running.set(key, (running.get(key) ?? 0) + 1)
  notify(TASK(key))
  try {
    return await work()
  } finally {
    const left = (running.get(key) ?? 1) - 1
    if (left > 0) running.set(key, left); else running.delete(key)
    notify(TASK(key))
  }
}

export function isTaskRunning(key: string) {
  return running.has(key)
}

export function useTaskRunning(key: string): boolean {
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const sync = () => setBusy(running.has(key))
    sync()
    return subscribe(TASK(key), sync)
  }, [key])
  return busy
}

/** True while the component is mounted — for async work deciding whether to touch the page. */
export function useMounted() {
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])
  return mounted
}
