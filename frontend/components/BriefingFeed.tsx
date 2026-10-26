'use client'

import Link from 'next/link'
import { AlarmClock, ArrowRight, ArrowUpRight, Loader2, Sparkles, X } from 'lucide-react'
import { Briefing, BriefingCard } from '@/lib/api'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

/** Human label for a card's origin, shown above its title. */
const KIND_LABELS: Record<string, string> = {
  priority: 'Do this first',
  scheduled_interview: 'On the calendar',
  stale_application: 'Going cold',
  saved_backlog: 'Unfinished',
  closed_role: 'No longer listed',
  interview_momentum: 'Momentum',
  outreach_followup: 'Follow-up due',
  outreach_suggestion: 'Worth a note',
  event_today: 'On today',
  event_soon: 'This week',
  event_met_prompt: 'Who you met',
  practice_deadline: 'Take-home due',
  practice_contest: 'Weekly contest',
  practice_today: 'Daily question',
}

function Card({ card, onDismiss }: { card: BriefingCard; onDismiss: (id: string, days?: number) => void }) {
  return <article className="cp-card" data-tone={card.tone}>
    <span className="cp-lamp" />
    <div className="cp-card-body">
      <div className="cp-card-headrow">
        <h3>{card.title}<span className="cp-card-kind">{KIND_LABELS[card.kind] || card.kind.replace(/_/g, ' ')}</span></h3>
        {card.dismissible && <div className="cp-card-tools">
          <button className="icon-refresh" title="Snooze for a week" onClick={() => onDismiss(card.id, 7)}>
            <AlarmClock size={14}/>
          </button>
          <button className="icon-refresh" title="Dismiss" onClick={() => onDismiss(card.id)}>
            <X size={14}/>
          </button>
        </div>}
      </div>
      <p>{card.detail}</p>
      {card.actions.length > 0 && <div className="cp-card-actions">
        {card.actions.map(action => {
          const className = action.style === 'primary' ? 'primary-button' : 'outline-button'
          // An absolute http(s) URL (the LeetCode cards) opens in a new tab; an in-app path stays a Link.
          const external = externalHref(action.href)
          return external
            ? <a key={action.label} href={external} className={className} {...EXTERNAL_LINK_PROPS}>{action.label} <ArrowUpRight size={13}/></a>
            : <Link key={action.label} href={action.href} className={className}>{action.label} <ArrowRight size={13}/></Link>
        })}
      </div>}
    </div>
  </article>
}

/**
 * Today's briefing: the deterministic cards from /agent/briefing.
 *
 * Presentational on purpose — the owning page holds the briefing state so it
 * can dismiss optimistically and refetch on failure.
 */
export default function BriefingFeed({
  briefing,
  failed,
  onDismiss,
}: {
  briefing: Briefing | null
  failed: boolean
  onDismiss: (id: string, days?: number) => void
}) {
  const cards = briefing?.cards ?? []
  return <section className="briefing-section">
    <div className="cp-section-head">
      <div className="panel-heading">
        <div>
          {/* A stable section label, not the headline. The overview already
              says the headline in its subhead, and printing the same sentence
              twice a screen apart read as a bug rather than as emphasis. */}
          <h2>Today&apos;s briefing</h2>
        </div>
      </div>
      {briefing && briefing.hidden_count > 0 && <span className="ci-count">{briefing.hidden_count} hidden</span>}
    </div>

    {!briefing && !failed && <div className="loading-state"><Loader2 size={16} className="spin"/> Reading your career context…</div>}
    {failed && !briefing && <p className="muted-copy">Your briefing is unavailable right now. The rest of your overview is still live.</p>}

    {briefing && cards.length === 0 && <div className="cp-clear">
      <Sparkles size={20}/>
      <strong>Nothing needs you right now</strong>
      <p>No stale applications, no unfinished saves. Go find new roles, or ask your copilot a question.</p>
      <Link href="/search" className="outline-button">Find roles <ArrowRight size={13}/></Link>
    </div>}

    {cards.length > 0 && <div className="cp-briefing">
      {cards.map(card => <Card key={card.id} card={card} onDismiss={onDismiss}/>)}
    </div>}
  </section>
}
