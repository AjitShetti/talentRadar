'use client'

import Link from 'next/link'
import { AlarmClock, ArrowRight, Loader2, Sparkles, X } from 'lucide-react'
import { Briefing, BriefingCard } from '@/lib/api'

/** Human label for a card's origin, shown above its title. */
const KIND_LABELS: Record<string, string> = {
  priority: 'DO THIS FIRST',
  stale_application: 'GOING COLD',
  saved_backlog: 'UNFINISHED',
  interview_momentum: 'MOMENTUM',
}

function Card({ card, onDismiss }: { card: BriefingCard; onDismiss: (id: string, days?: number) => void }) {
  return <article className="cp-card" data-tone={card.tone}>
    <span className="cp-lamp" />
    <div className="cp-card-body">
      <div className="cp-card-headrow">
        <h3>{card.title}<span className="cp-card-kind">{KIND_LABELS[card.kind] || card.kind.replace(/_/g, ' ').toUpperCase()}</span></h3>
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
        {card.actions.map(action => <Link
          key={action.label}
          href={action.href}
          className={action.style === 'primary' ? 'primary-button' : 'outline-button'}
        >{action.label} <ArrowRight size={13}/></Link>)}
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
          <h2>{briefing?.headline || 'What needs you today'}</h2>
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
