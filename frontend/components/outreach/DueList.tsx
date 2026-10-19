'use client'

import { useState } from 'react'
import type { DueItem } from '@/lib/api'
import { dayMonth, initials, MAX_FOLLOW_UPS, stamp } from '@/lib/outreach'

type Props = {
  items: DueItem[]
  onDraft: (item: DueItem) => void
  onSent: (item: DueItem) => Promise<void>
  onReplied: (item: DueItem) => void
  onSnooze: (item: DueItem) => Promise<void>
  /** Clears a thread's next step, so a step the user has done stops being owed. */
  onStepDone: (item: DueItem) => Promise<void>
  onOpen: (item: DueItem) => void
}

const keyOf = (item: DueItem) => `${item.thread_id}:${item.person_id ?? 'thread'}:${item.kind}`

// Follow-ups and next steps that are owed today. The row's amber lamp is the only
// amber here; every action is an outline button, and none of them sends anything.
export default function DueList({ items, onDraft, onSent, onReplied, onSnooze, onStepDone, onOpen }: Props) {
  // One call at a time per row, so "I sent it" pressed twice is one touch, not two.
  const [pending, setPending] = useState<string | null>(null)

  async function run(item: DueItem, work: (item: DueItem) => Promise<void>) {
    if (pending) return
    setPending(keyOf(item))
    try { await work(item) } finally { setPending(null) }
  }

  if (!items.length) return null

  return <div role="list" aria-label="Due today">
    {items.map(item => {
      const key = keyOf(item)
      const isFollowUp = item.kind === 'follow_up' && item.person_id !== null
      const who = item.person_name || 'them'
      const title = isFollowUp ? `Follow up with ${who}` : (item.next_step || 'Next step')
      const detail = isFollowUp
        ? [
            item.company_name,
            item.last_outbound_at ? `wrote ${dayMonth(item.last_outbound_at)}` : null,
            item.follow_up_number ? `follow-up ${item.follow_up_number} of ${MAX_FOLLOW_UPS}` : null,
          ].filter(Boolean).join(' · ')
        : item.company_name
      return <div className="or-row" role="listitem" data-tone="due" key={key}>
        <span className="or-lamp" aria-hidden />
        <span className="or-mark" aria-hidden>{initials(item.company_name)}</span>
        <div className="or-main"><strong>{title}</strong><span>{detail}</span></div>
        <span className="or-date">Due {stamp(item.due_at)}</span>
        <div className="or-actions">
          {isFollowUp && <>
            <button type="button" className="outline-button" onClick={() => onDraft(item)}>Draft follow-up</button>
            <button type="button" className="outline-button" disabled={pending !== null} onClick={() => run(item, onSent)}>I sent it</button>
            <button type="button" className="outline-button" onClick={() => onReplied(item)}>They replied</button>
            <button type="button" className="outline-button" disabled={pending !== null} onClick={() => run(item, onSnooze)}>Snooze 3 days</button>
          </>}
          {!isFollowUp && <button type="button" className="outline-button" disabled={pending !== null} onClick={() => run(item, onStepDone)} aria-label={`Done with ${title} at ${item.company_name}`}>Done</button>}
          <button type="button" className="text-button" onClick={() => onOpen(item)}>Open thread</button>
        </div>
      </div>
    })}
  </div>
}
