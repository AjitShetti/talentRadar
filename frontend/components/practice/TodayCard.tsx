'use client'

import Link from 'next/link'
import { api, PracticeFeel, PracticeToday } from '@/lib/api'
import { clock } from '@/lib/practice-time'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

const FEELS: Array<[PracticeFeel, string]> = [['easy', 'Easy'], ['right', 'Right'], ['hard', 'Hard']]
const SWAP_WARNING = "Your drawing stays saved, but it can't be finished."
const words = (text: string) => text.replace(/[-_]/g, ' ')
const capitalise = (text: string) => text ? text[0].toUpperCase() + text.slice(1) : text

/** "Thu 15 Oct" from the server's IST day (YYYY-MM-DD). */
function dayLabel(day: string) {
  const date = new Date(`${day}T00:00:00Z`)
  if (Number.isNaN(date.getTime())) return day
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short' }).formatToParts(date)
  const pick = (type: string) => parts.find(part => part.type === type)?.value ?? ''
  return `${pick('weekday')} ${pick('day')} ${pick('month')}`
}

/** The server's links all point at leetcode.com; anything else is not rendered as a link. */
export function leetcodeHref(url: string | null | undefined): string | null {
  const href = externalHref(url)
  if (!href) return null
  const host = new URL(href).hostname
  return host === 'leetcode.com' || host.endsWith('.leetcode.com') ? href : null
}

type Act = (call: () => Promise<PracticeToday>, refresh?: boolean, focusId?: string) => void

type Props = { today: PracticeToday | null; error: string; busy: boolean; act: Act }

/** Today's one problem: a LeetCode question, or a system design problem drawn on its own page. */
export default function TodayCard({ today, error, busy, act }: Props) {
  const question = today?.question ?? null
  const design = today?.design ?? null
  const done = !!today?.done_at
  const questionHref = leetcodeHref(question?.url)
  const dailyHref = leetcodeHref(today?.leetcode_daily_url)
  const swapped = !done && today?.swaps_left === 0

  function swapDesign() {
    if (design?.has_answer && !window.confirm(SWAP_WARNING)) return
    act(() => api.practice.swap(), false, 'pr-draw')
  }

  return <section className="pr-section" id="today">
    <div className="pr-section-head">
      <h2>Today</h2>
      {today && <span>{dayLabel(today.day)}. One problem a day.{swapped ? ' Swapped once.' : ''}</span>}
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}

    {today && question && <article className="pr-today" data-done={done ? '' : undefined}>
      <span className="pr-lamp" aria-hidden="true" />
      <div className="pr-code">{capitalise(question.difficulty)}<b>#{question.id}</b></div>
      <div>
        <h3 className="pr-title">{question.title}</h3>
        <div className="chips">{question.topics.slice(0, 6).map(topic => <span key={topic}>{words(topic)}</span>)}</div>
        <p className="pr-reason">{today.reason}</p>
        {done && <div className="pr-feel" role="group" aria-label="How was it?">
          <span>How was it?</span>
          {FEELS.map(([value, text]) =>
            <button key={value} id={`pr-feel-${value}`} type="button" aria-pressed={today.feel === value} disabled={busy} onClick={() => act(() => api.practice.feel(value), false, `pr-feel-${value}`)}>{text}</button>)}
        </div>}
      </div>
      <div className="pr-actions">
        {done
          ? <>
            <span className="pr-stamp">Done {clock(today.done_at!)}</span>
            <button type="button" className="pr-ghost" disabled={busy} onClick={() => act(() => api.practice.undo(), true, 'pr-mark-done')}>Undo</button>
          </>
          : <>
            {questionHref
              ? <a className="primary-button" href={questionHref} {...EXTERNAL_LINK_PROPS}>Open on LeetCode ↗</a>
              : null}
            <button id="pr-mark-done" type="button" className="pr-ghost" disabled={busy} onClick={() => act(() => api.practice.done(), true, 'pr-feel-easy')}>Mark done</button>
          </>}
        <button type="button" className="pr-ghost" disabled={busy || today.swaps_left === 0} onClick={() => act(() => api.practice.swap(), false, 'pr-mark-done')}>Swap</button>
      </div>
    </article>}

    {today && design && <article className="pr-today" data-done={done ? '' : undefined}>
      <span className="pr-lamp" aria-hidden="true" />
      <div className="pr-code">System design<b>about {design.minutes} min</b></div>
      <div>
        <h3 className="pr-title">{design.title}</h3>
        <div className="chips">{design.topics.map(topic => <span key={topic}>{words(topic)}</span>)}</div>
        <p className="pr-reason">{today.reason}</p>
      </div>
      <div className="pr-actions">
        {done
          ? <>
            <span className="pr-stamp">Done {clock(today.done_at!)}</span>
            <Link id="pr-draw" className="primary-button" href={`/practice/design/${encodeURIComponent(design.slug)}`}>See your answer →</Link>
          </>
          : <>
            <Link id="pr-draw" className="primary-button" href={`/practice/design/${encodeURIComponent(design.slug)}`}>Draw and solve →</Link>
            <button type="button" className="pr-ghost" disabled={busy || today.swaps_left === 0} onClick={swapDesign}>Swap</button>
          </>}
      </div>
    </article>}

    {today && !question && !design && <div className="empty-state"><h2>The question list is not loaded yet.</h2></div>}

    {today && <div className="pr-links">
      {today.track === 'general' && (question || design) && <Link href="/settings">Add a target role in Profile &amp; goals to tune picks →</Link>}
      {question && dailyHref && <a href={dailyHref} {...EXTERNAL_LINK_PROPS}>LeetCode&apos;s own daily is on their problem set ↗</a>}
    </div>}
  </section>
}
