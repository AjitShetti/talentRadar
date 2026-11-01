'use client'

import { ReactNode, useEffect, useState } from 'react'
import Link from 'next/link'
import FlapText from '@/components/FlapText'
import ContestBoard from '@/components/practice/ContestBoard'
import { leetcodeHref } from '@/components/practice/TodayCard'
import { Challenge, ChallengesOverview, Contest } from '@/lib/api'
import { clock, dayTime, duration, NBSP, shortDate, weekdayTime } from '@/lib/practice-time'
import { EXTERNAL_LINK_PROPS } from '@/lib/safe-url'

const TRACK_NAMES: Record<string, string> = { ml: 'ML', ai: 'AI', devops: 'DevOps' }
const STATUS_WORDS: Record<string, string> = {
  judged: 'judged', queued: 'queued', submitted: 'submitted', rejected: 'rejected',
  judge_failed: 'not judged', missed: 'not submitted', started: 'not submitted',
}
const capitalise = (text: string) => text ? text[0].toUpperCase() + text.slice(1) : text
const trackName = (track: string) => TRACK_NAMES[track] ?? capitalise(track)

/** The row's right-hand column: the user's own clock, or how the attempt ended. */
function contestState(challenge: Challenge, now: number): { tone: 'live' | 'done' | 'idle'; big: ReactNode; small: string; calm: boolean } {
  const attempt = challenge.attempt
  const hours = challenge.brief.hours
  if (!attempt) {
    const late = now > new Date(challenge.last_start_at).getTime()
    return { tone: late ? 'idle' : 'live', big: `${hours} h`, small: late ? 'too late to start' : 'once you start', calm: true }
  }
  const editableUntil = new Date(attempt.editable_until).getTime()
  switch (attempt.status) {
    case 'started': {
      const left = new Date(attempt.deadline_at).getTime() - now
      if (left > 0) return { tone: 'live', big: <FlapText value={duration(left, false).replace(/ /g, NBSP)} />, small: 'left on your clock', calm: false }
      return { tone: 'live', big: 'Time is up', small: now < editableUntil ? `write-up editable until ${clock(attempt.editable_until)}` : 'window closed', calm: true }
    }
    case 'submitted':
      return { tone: 'done', big: 'Submitted', small: now < editableUntil ? `editable until ${clock(attempt.editable_until)}` : 'waiting for the judge', calm: true }
    case 'queued':
      return attempt.held
        ? { tone: 'done', big: 'Under review', small: 'a person checks it before it is ranked', calm: true }
        : { tone: 'done', big: 'Submitted', small: 'queued for the judge', calm: true }
    case 'judged':
      return { tone: 'done', big: attempt.total != null ? <FlapText value={attempt.total.toFixed(1)} /> : 'Judged', small: 'judged · of 100', calm: true }
    case 'rejected':
      return { tone: 'idle', big: 'Rejected', small: 'open the brief for why', calm: true }
    case 'judge_failed':
      return { tone: 'idle', big: 'Not judged', small: 'we could not judge this yet', calm: true }
    default:
      return { tone: 'idle', big: 'Not submitted', small: 'this contest closed', calm: true }
  }
}

/** This week's contest, the ranking right under it, the person's past contests and LeetCode's own. */
export default function ContestSection({ overview, error, contest }: { overview: ChallengesOverview | null; error: string; contest?: Contest }) {
  const [now, setNow] = useState(() => Date.now())

  // The contest's countdown and LeetCode's line move once a minute.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(id)
  }, [])

  const current = overview?.current ?? null
  const state = current ? contestState(current, now) : null
  const pastLastStart = !!current && !current.attempt && now > new Date(current.last_start_at).getTime()
  const nextContest = overview?.next_opens_at ? dayTime(overview.next_opens_at) : 'Fri 18:00 IST'
  const contestHref = leetcodeHref(contest?.url)
  const leetcodeStart = contest ? new Date(contest.starts_at).getTime() : 0
  const leetcodeEnd = contest ? new Date(contest.ends_at).getTime() : 0

  return <section className="pr-section" id="contest">
    <div className="pr-section-head">
      <h2>Weekly contest</h2>
      {current
        ? <span>{now >= new Date(current.opens_at).getTime() ? 'opened' : 'opens'} {dayTime(current.opens_at)} · closes {dayTime(current.closes_at)}</span>
        : <span>Build something real in 24 or 48 hours. A new one opens every Friday at 18:00 IST.</span>}
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    {overview && (current && state
      ? <>
        <article className="pr-build" data-done={state.tone === 'done' ? '' : undefined} data-idle={state.tone === 'idle' ? '' : undefined}>
          <span className="pr-lamp" aria-hidden="true" />
          <div className="pr-code">{trackName(current.brief.track)}<b>{current.brief.hours} h</b></div>
          <div><h3>{current.brief.title}</h3><p>{current.brief.summary}</p></div>
          <div className="pr-clock" data-calm={state.calm ? '' : undefined} data-word={typeof state.big === 'string' && state.big.length > 8 ? '' : undefined}><b>{state.big}</b><span>{state.small}</span></div>
          <Link className="pr-ghost" href={`/practice/challenges/${encodeURIComponent(current.slug)}`}>Read the brief →</Link>
        </article>
        {pastLastStart && <p className="pr-help">Not enough time left in this week&apos;s window — next contest {nextContest}</p>}
      </>
      : <p className="muted-copy">Next contest opens {nextContest}.</p>)}

    <div className="pr-board-grid">
      <section className="pr-card" aria-label="Contest ranking">
        <p className="pr-lbl">Ranking</p>
        <ContestBoard slug={current?.slug ?? null} tabs />
      </section>
      <section className="pr-card" aria-label="Your past contests">
        <p className="pr-lbl">Your past contests</p>
        {overview && !overview.past.length && <p className="muted-copy">Past contests appear here once one has closed.</p>}
        {!!overview?.past.length && <div className="history-list">
          {overview.past.map(item => <div key={item.slug}>
            <span className="history-topic"><Link href={`/practice/challenges/${encodeURIComponent(item.slug)}`}>{item.title}</Link></span>
            <strong>{item.total != null ? item.total.toFixed(1) : '—'}</strong>
            <small>{shortDate(item.opens_at)} · {trackName(item.track)} · {(item.status === 'queued' && item.held ? 'under review' : item.status ? STATUS_WORDS[item.status] ?? item.status : 'Not entered')}</small>
          </div>)}
        </div>}
      </section>
    </div>

    {contest?.show && <p className="pr-lc-line">
      LeetCode&apos;s own weekly contest: {weekdayTime(contest.starts_at)} · <b>{contest.live || now >= leetcodeStart ? `live now, ends in ${duration(leetcodeEnd - now, true)}` : `starts in ${duration(leetcodeStart - now, true)}`}</b>
      {contestHref && <> · <a href={contestHref} {...EXTERNAL_LINK_PROPS}>Open on LeetCode ↗</a></>}
    </p>}
  </section>
}
