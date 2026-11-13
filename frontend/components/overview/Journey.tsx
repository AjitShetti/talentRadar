'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import FlapText from '@/components/FlapText'
import type { InterviewInsights } from '@/components/overview/StrengthsAndGaps'
import { AgendaItem, api, OutreachHome } from '@/lib/api'

type JourneyData = { analytics: Record<string, number>; agenda: AgendaItem[]; interviews: InterviewInsights | null }

/**
 * "Your journey so far" on Profile & goals: the search in four counts, each a link
 * to where it is kept. It reads the dashboard and Outreach itself; without the
 * dashboard it shows nothing, without Outreach the People cell says so.
 */
export default function Journey() {
  const [data, setData] = useState<JourneyData | null>(null)
  const [outreach, setOutreach] = useState<OutreachHome | null>(null)

  useEffect(() => {
    api.dashboard()
      .then(d => setData({
        analytics: (d.analytics as Record<string, number> | undefined) ?? {},
        agenda: (d.agenda as AgendaItem[] | undefined) ?? [],
        interviews: (d.interviews as InterviewInsights | null | undefined) ?? null,
      }))
      .catch(() => setData(null))
    api.outreach.home().then(setOutreach).catch(() => setOutreach(null))
  }, [])

  if (!data) return null
  const { analytics, agenda, interviews } = data

  return <section className="ov2-section" aria-labelledby="journey-title">
    <div className="ov2-head"><h2 id="journey-title">Your journey so far</h2></div>
    <div className="ov2-ledger">
      <Link href="/applications">
        <span>Applications</span>
        <b><FlapText value={analytics.total_applications ?? 0} /></b>
        <small>tracked</small>
      </Link>
      <Link href="/outreach">
        <span>People</span>
        {outreach ? <OutreachLedger home={outreach} /> : <><b>—</b><small>Start a conversation with someone at a company you like.</small></>}
      </Link>
      <Link href="/applications">
        <span>Interviews</span>
        <b><FlapText value={(analytics.interview ?? 0) + (analytics.offers ?? 0)} /></b>
        <small>{analytics.interview ?? 0} in progress · {analytics.offers ?? 0} cracked · {agenda.length} coming up</small>
      </Link>
      <Link href="/interview">
        <span>Mock interviews</span>
        {interviews && interviews.sessions_analyzed > 0
          ? <>
            <b><FlapText value={Math.round(interviews.average_score)} />{interviews.delta !== 0 && <em data-up={interviews.delta > 0 ? 'true' : undefined}>{interviews.delta > 0 ? '+' : ''}{Math.round(interviews.delta)}</em>}</b>
            <small>avg score · {interviews.sessions_analyzed} session{interviews.sessions_analyzed === 1 ? '' : 's'} · {interviews.questions_analyzed} answers</small>
          </>
          : <><b>—</b><small>No sessions yet. One mock sets your baseline.</small></>}
      </Link>
    </div>
  </section>
}

/** The people cell: who is talking, and the referrals and conversations behind it. */
function OutreachLedger({ home }: { home: OutreachHome }) {
  const talking = home.threads.filter(thread => thread.stage === 'replied').length
  const referrals = home.threads.filter(thread => thread.outcome === 'referred').length
  return <>
    <b><FlapText value={talking} /></b>
    <small>talking to you · {referrals} referral{referrals === 1 ? '' : 's'} · {home.conversations_started} started</small>
  </>
}
