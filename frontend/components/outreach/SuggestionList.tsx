'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import type { Suggestion, Suggestions } from '@/lib/api'
import { KIND_LABEL } from '@/lib/outreach'

type Props = {
  data: Suggestions | null
  /** A company id from ?company=: its card is scrolled into view and marked. */
  focusId: string | null
  onAdd: (suggestion: Suggestion) => Promise<void>
  onDismiss: (suggestion: Suggestion) => void
}

// "Start with: Asha Rao, founder": the stored title when there is one, else the kind of person.
function startWith(person: NonNullable<Suggestion['top_person']>): string {
  const role = person.title || KIND_LABEL[person.kind] || ''
  return role ? `${person.name}, ${role.toLowerCase()}` : person.name
}

// The ranked "Who to reach". The order is a ranking from rules, not a plan, so there are
// no plate numerals, and amber is not spent here.
export default function SuggestionList({ data, focusId, onAdd, onDismiss }: Props) {
  const [adding, setAdding] = useState<string | null>(null)

  const focusPresent = !!focusId && !!data?.suggestions.some(s => s.company_id === focusId)
  useEffect(() => {
    if (!focusId || !focusPresent) return
    const el = document.getElementById(`or-sugg-${focusId}`)
    el?.scrollIntoView({ block: 'center', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
  }, [focusId, focusPresent])

  if (!data) return null

  async function add(suggestion: Suggestion) {
    if (adding) return
    setAdding(suggestion.company_id)
    try { await onAdd(suggestion) } finally { setAdding(null) }
  }

  return <>
    {!data.profile_complete &&
      <p className="or-hint"><Link href="/settings">Add a target role and your skills to rank these by who is hiring for you &rarr;</Link></p>}

    {!data.suggestions.length
      ? <div className="empty-state"><p>Nothing to suggest yet. The company map is still filling in &mdash; add a company by name.</p></div>
      : <div>
          {data.suggestions.map(s =>
            <article className="or-sugg" id={`or-sugg-${s.company_id}`} key={s.company_id} data-focus={s.company_id === focusId ? '' : undefined}>
              <div className="or-sugg-top">
                <strong>{s.name}</strong>
                {s.area && <small>{s.area}</small>}
              </div>
              {s.summary && <p>{s.summary}</p>}
              {s.reasons.length > 0 && <div className="or-reasons">{s.reasons.map(r => <span key={r}>{r}</span>)}</div>}
              {s.top_person && <div className="or-start">Start with: {startWith(s.top_person)}</div>}
              <div className="or-actions">
                <button type="button" className="outline-button" disabled={adding !== null} onClick={() => add(s)}>Add to outreach</button>
                <button type="button" className="text-button" disabled={adding !== null} onClick={() => onDismiss(s)}>Not now</button>
              </div>
            </article>)}
        </div>}
  </>
}
