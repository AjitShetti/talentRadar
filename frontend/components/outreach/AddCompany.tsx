'use client'

import { KeyboardEvent, useEffect, useRef, useState } from 'react'
import { Search } from 'lucide-react'
import { api, OutreachCompanyMatch } from '@/lib/api'

/** A stored company the user picked, or a name we have no record of. */
export type AddChoice = { companyId: string } | { companyName: string }

type Props = {
  /** Resolves true when the thread was created or already existed, false when it failed (the page shows why). */
  onAdd: (choice: AddChoice) => Promise<boolean>
}

const MIN_CHARS = 2
const DEBOUNCE_MS = 250
const NAME_MAX = 256 // services.outreach.threads.COMPANY_NAME_MAX

type Result = { query: string; items: OutreachCompanyMatch[]; failed: boolean }

const detail = (m: OutreachCompanyMatch) => [m.area, m.summary].filter(Boolean).join(' · ').slice(0, 90)

// "Add a company": a name search over the company map, and always a last option to add the
// name as typed. Below two characters nothing is searched and nothing is shown.
export default function AddCompany({ onAdd }: Props) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(true)
  const [result, setResult] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const typed = query.trim()
  const searchable = typed.length >= MIN_CHARS

  useEffect(() => {
    if (!searchable) { setResult(null); return }
    let live = true
    const timer = setTimeout(async () => {
      try {
        const items = await api.outreach.searchCompanies(typed)
        if (live) setResult({ query: typed, items, failed: false })
      } catch {
        if (live) setResult({ query: typed, items: [], failed: true })
      }
    }, DEBOUNCE_MS)
    return () => { live = false; clearTimeout(timer) }
  }, [typed, searchable])

  const options = () => Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>('button') ?? [])

  function inputKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Escape') { setOpen(false) }
    else if ((e.key === 'ArrowDown' || e.key === 'Enter') && searchable && open) {
      e.preventDefault()
      options()[0]?.focus()
    }
  }

  function optionKey(e: KeyboardEvent<HTMLUListElement>) {
    const buttons = options()
    const at = buttons.findIndex(b => b === document.activeElement)
    if (e.key === 'ArrowDown') { e.preventDefault(); buttons[Math.min(at + 1, buttons.length - 1)]?.focus() }
    else if (e.key === 'ArrowUp') { e.preventDefault(); if (at <= 0) inputRef.current?.focus(); else buttons[at - 1]?.focus() }
    else if (e.key === 'Escape') { e.preventDefault(); setOpen(false); inputRef.current?.focus() }
  }

  async function choose(choice: AddChoice) {
    if (busy) return
    setBusy(true)
    try {
      if (await onAdd(choice)) { setQuery(''); setResult(null) }
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  const matches = result && result.query === typed ? result : null

  return <>
    <div className="or-add">
      <Search size={15} aria-hidden />
      <input
        ref={inputRef}
        type="text"
        value={query}
        maxLength={NAME_MAX}
        placeholder="Add a company by name"
        aria-label="Add a company by name"
        autoComplete="off"
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onKeyDown={inputKey}
      />
    </div>
    {searchable && open &&
      <ul className="or-matches" ref={listRef} aria-label="Matching companies" onKeyDown={optionKey}>
        {matches?.items.map(m =>
          <li key={m.id}>
            <button type="button" disabled={busy} onClick={() => choose({ companyId: m.id })}>
              {m.name}
              {detail(m) && <small>{detail(m)}</small>}
            </button>
          </li>)}
        {matches?.failed && <li><small>Search is unavailable right now. You can still add the name.</small></li>}
        <li>
          <button type="button" disabled={busy} onClick={() => choose({ companyName: typed })}>
            Add &ldquo;{typed}&rdquo; as a new name
          </button>
        </li>
      </ul>}
  </>
}
