'use client'

import { FormEvent, ReactNode, useEffect, useState } from 'react'
import Link from 'next/link'
import { Loader2 } from 'lucide-react'
import AppShell from '@/components/AppShell'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { api, Challenge, ChallengesOverview, PracticeFeel, PracticeHistory, PracticeSettings, PracticeToday } from '@/lib/api'
import { clock, dayTime, duration, NBSP, shortDate, weekdayTime } from '@/lib/practice-time'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const FEELS: Array<[PracticeFeel, string]> = [['easy', 'Easy'], ['right', 'Right'], ['hard', 'Hard']]
const TRACK_NAMES: Record<string, string> = { ml: 'ML', ai: 'AI', devops: 'DevOps' }
const STATUS_WORDS: Record<string, string> = {
  judged: 'judged', queued: 'queued', submitted: 'submitted', rejected: 'rejected',
  judge_failed: 'not judged', missed: 'not submitted', started: 'not submitted',
}

/** "Thu 15 Oct" from the server's IST day (YYYY-MM-DD). */
function dayLabel(day: string) {
  const date = new Date(`${day}T00:00:00Z`)
  if (Number.isNaN(date.getTime())) return day
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short' }).formatToParts(date)
  const pick = (type: string) => parts.find(part => part.type === type)?.value ?? ''
  return `${pick('weekday')} ${pick('day')} ${pick('month')}`
}
const words = (text: string) => text.replace(/[-_]/g, ' ')
const capitalise = (text: string) => text ? text[0].toUpperCase() + text.slice(1) : text
const trackName = (track: string) => TRACK_NAMES[track] ?? capitalise(track)

/** The server's links all point at leetcode.com; anything else is not rendered as a link. */
function leetcodeHref(url: string | null | undefined): string | null {
  const href = externalHref(url)
  if (!href) return null
  const host = new URL(href).hostname
  return host === 'leetcode.com' || host.endsWith('.leetcode.com') ? href : null
}

/** The row's right-hand column: the user's own clock, or how the attempt ended. */
function buildState(challenge: Challenge, now: number): { tone: 'live' | 'done' | 'idle'; big: ReactNode; small: string; calm: boolean } {
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
      return { tone: 'done', big: 'Submitted', small: 'queued for the judge', calm: true }
    case 'judged':
      return { tone: 'done', big: attempt.total != null ? <FlapText value={attempt.total.toFixed(1)} /> : 'Judged', small: 'judged · of 100', calm: true }
    case 'rejected':
      return { tone: 'idle', big: 'Rejected', small: 'open the brief for why', calm: true }
    case 'judge_failed':
      return { tone: 'idle', big: 'Not judged', small: 'we could not judge this yet', calm: true }
    default:
      return { tone: 'idle', big: 'Missed', small: 'not submitted', calm: true }
  }
}

function Calendar({ days }: { days: PracticeHistory['days'] }) {
  if (!days.length) return null
  // Columns are weeks, rows Monday..Sunday: pad the first and last week out.
  const lead = (new Date(`${days[0].day}T00:00:00Z`).getUTCDay() + 6) % 7
  const cells: Array<{ key: string; state?: 'done' | 'today'; label?: string }> = [
    ...Array.from({ length: lead }, (_, i) => ({ key: `lead-${i}` })),
    ...days.map((day, i) => {
      const today = i === days.length - 1
      return {
        key: day.day,
        state: day.done ? 'done' as const : today ? 'today' as const : undefined,
        label: `${day.day}: ${day.done ? 'practised' : today ? 'today' : 'not practised'}`,
      }
    }),
  ]
  const trail = (7 - cells.length % 7) % 7
  for (let i = 0; i < trail; i++) cells.push({ key: `trail-${i}` })
  const weeks: typeof cells[] = []
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7))
  return <div className="pr-cal" role="grid" aria-label={`Practice calendar, last ${Math.round(days.length / 7)} weeks`}>
    {weeks.map((week, w) => <span role="row" style={{ display: 'contents' }} key={w}>
      {week.map(cell => cell.label
        ? <i key={cell.key} role="gridcell" data-s={cell.state} aria-label={cell.label} title={cell.label} />
        : <i key={cell.key} data-s="future" aria-hidden="true" />)}
    </span>)}
  </div>
}

export default function PracticePage() {
  const [today, setToday] = useState<PracticeToday | null>(null)
  const [history, setHistory] = useState<PracticeHistory | null>(null)
  const [overview, setOverview] = useState<ChallengesOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState({ today: '', history: '', overview: '' })
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  const [settings, setSettings] = useState<PracticeSettings | null>(null)
  const [displayName, setDisplayName] = useState('')
  const [showRepo, setShowRepo] = useState(false)
  const [contestReminder, setContestReminder] = useState(true)
  const [saving, setSaving] = useState(false)
  const [settingsError, setSettingsError] = useState('')
  const [settingsSaved, setSettingsSaved] = useState(false)

  function adopt(next: PracticeSettings) {
    setSettings(next)
    setDisplayName(next.display_name ?? '')
    setShowRepo(next.show_repo)
    setContestReminder(next.contest_reminder)
  }

  async function loadHistory() {
    try {
      setHistory(await api.practice.history(12))
      setErrors(current => ({ ...current, history: '' }))
    } catch (err) {
      setErrors(current => ({ ...current, history: err instanceof Error ? err.message : 'Could not load your calendar.' }))
    }
  }

  useEffect(() => {
    let live = true
    async function load() {
      const [todayResult, historyResult, overviewResult, settingsResult] = await Promise.allSettled([
        api.practice.today(), api.practice.history(12), api.practice.challenges(), api.practice.settings(),
      ])
      if (!live) return
      const reason = (result: PromiseRejectedResult, fallback: string) => result.reason instanceof Error ? result.reason.message : fallback
      if (todayResult.status === 'fulfilled') setToday(todayResult.value)
      if (historyResult.status === 'fulfilled') setHistory(historyResult.value)
      if (overviewResult.status === 'fulfilled') setOverview(overviewResult.value)
      if (settingsResult.status === 'fulfilled') adopt(settingsResult.value)
      setErrors({
        today: todayResult.status === 'rejected' ? reason(todayResult, 'Could not load today’s question.') : '',
        history: historyResult.status === 'rejected' ? reason(historyResult, 'Could not load your calendar.') : '',
        overview: overviewResult.status === 'rejected' ? reason(overviewResult, 'Could not load this week’s build.') : '',
      })
      setLoading(false)
    }
    load()
    return () => { live = false }
  }, [])

  // The build countdown and the contest line move once a minute.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(id)
  }, [])

  async function act(call: () => Promise<PracticeToday>, refreshCalendar = false) {
    setBusy(true)
    setErrors(current => ({ ...current, today: '' }))
    try {
      setToday(await call())
      if (refreshCalendar) await loadHistory()
    } catch (err) {
      setErrors(current => ({ ...current, today: err instanceof Error ? err.message : 'Something went wrong.' }))
    } finally {
      setBusy(false)
    }
  }

  async function saveSettings(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    setSettingsError('')
    setSettingsSaved(false)
    try {
      adopt(await api.practice.saveSettings({ display_name: displayName.trim() || null, show_repo: showRepo, contest_reminder: contestReminder }))
      setSettingsSaved(true)
      // The contest line on the page follows the reminder switch.
      api.practice.today().then(setToday).catch(() => undefined)
    } catch (err) {
      setSettingsError(err instanceof Error ? err.message : 'Could not save your settings.')
    } finally {
      setSaving(false)
    }
  }

  const done = !!today?.done_at
  const question = today?.question ?? null
  const questionHref = leetcodeHref(question?.url)
  const dailyHref = leetcodeHref(today?.leetcode_daily_url)
  const contest = today?.contest
  const contestHref = leetcodeHref(contest?.url)
  const contestStart = contest ? new Date(contest.starts_at).getTime() : 0
  const contestEnd = contest ? new Date(contest.ends_at).getTime() : 0
  const current = overview?.current ?? null
  const currentState = current ? buildState(current, now) : null
  const pastLastStart = !!current && !current.attempt && now > new Date(current.last_start_at).getTime()
  const nextBuild = overview?.next_opens_at ? dayTime(overview.next_opens_at) : 'Fri 18:00 IST'
  const topTopics = [...(history?.topics ?? [])].sort((a, b) => b.count - a.count).slice(0, 3)
  const months = history ? history.days.filter((day, i) => i === 0 || day.day.slice(5, 7) !== history.days[i - 1].day.slice(5, 7)).map(day => MONTHS[Number(day.day.slice(5, 7)) - 1]) : []

  const subhead = today && today.catalogue === 'ok'
    ? `Picked for ${today.track === 'general' ? 'general practice' : today.track_label} · from ${today.catalogue_size.toLocaleString('en-US')} free LeetCode questions · solved on leetcode.com`
    : 'One LeetCode question a day, one build a week — solved on leetcode.com.'

  return <RequireAuth><AppShell>
    <section className="page-heading split-heading">
      <div>
        <span className="board-kicker">Practice</span>
        <h1>Practise a little, every day<span>.</span></h1>
        <p>{subhead}</p>
      </div>
      <div className="tracker-total"><strong><FlapText value={history ? history.counts.this_month : '–'} /></strong><span>days this month</span></div>
    </section>

    {loading
      ? <div className="loading-state"><Loader2 className="spin" /> Loading Practice…</div>
      : <>
        <div className="pr-section-head">
          <h2>Today&apos;s question</h2>
          {today && <span>{dayLabel(today.day)}{!done && question && today.swaps_left === 0 ? ' · swapped once' : ''}</span>}
        </div>
        {errors.today && <p className="form-error" role="alert">{errors.today}</p>}
        {today && (question
          ? <article className="pr-today" data-done={done ? '' : undefined}>
            <span className="pr-lamp" aria-hidden="true" />
            <div className="pr-code">{capitalise(question.difficulty)}<b>#{question.id}</b></div>
            <div>
              <h3 className="pr-title">{question.title}</h3>
              <div className="chips">{question.topics.slice(0, 6).map(topic => <span key={topic}>{words(topic)}</span>)}</div>
              <p className="pr-reason">{today.reason}</p>
              {done && <div className="pr-feel" role="group" aria-label="How was it?">
                <span>How was it?</span>
                {FEELS.map(([value, text]) =>
                  <button key={value} type="button" aria-pressed={today.feel === value} disabled={busy} onClick={() => act(() => api.practice.feel(value))}>{text}</button>)}
              </div>}
            </div>
            <div className="pr-actions">
              {done
                ? <>
                  <span className="pr-stamp">Done {clock(today.done_at!)}</span>
                  <button type="button" className="pr-ghost" disabled={busy} onClick={() => act(() => api.practice.undo(), true)}>Undo</button>
                </>
                : <>
                  {questionHref
                    ? <a className="primary-button" href={questionHref} {...EXTERNAL_LINK_PROPS}>Open on LeetCode ↗</a>
                    : null}
                  <button type="button" className="pr-ghost" disabled={busy} onClick={() => act(() => api.practice.done(), true)}>Mark done</button>
                </>}
              <button type="button" className="pr-ghost" disabled={busy || today.swaps_left === 0} onClick={() => act(() => api.practice.swap())}>Swap</button>
            </div>
          </article>
          : <div className="empty-state"><h2>The question list is not loaded yet.</h2></div>)}
        {today && <div className="pr-links">
          {today.track === 'general' && question && <Link href="/settings">Add a target role in Profile &amp; goals to tune picks ↗</Link>}
          {dailyHref && <a href={dailyHref} {...EXTERNAL_LINK_PROPS}>LeetCode&apos;s own daily is on their problem set ↗</a>}
          {contest?.show && <span>
            Weekly contest · {weekdayTime(contest.starts_at)} · <b>{contest.live || now >= contestStart ? `live now, ends in ${duration(contestEnd - now, true)}` : `starts in ${duration(contestStart - now, true)}`}</b>
            {contestHref && <> · <a href={contestHref} {...EXTERNAL_LINK_PROPS}>Open contest ↗</a></>}
          </span>}
        </div>}

        <section className="pr-section">
          <div className="pr-section-head"><h2>Your last 12 weeks</h2><span>days you marked done</span></div>
          {errors.history && <p className="form-error" role="alert">{errors.history}</p>}
          {history && <div className="pr-cal-wrap">
            <div>
              <Calendar days={history.days} />
              <div className="pr-cal-months">{months.map((month, i) => <span key={`${month}-${i}`}>{month}</span>)}</div>
            </div>
            <div>
              <div className="pr-counts">
                <div><b><FlapText value={history.counts.this_month} /></b><span>This month</span></div>
                <div><b><FlapText value={history.counts.last_30} /></b><span>Last 30 days</span></div>
                <div><b><FlapText value={history.counts.longest_run} /></b><span>Longest run</span></div>
              </div>
              <p className="pr-cal-note">{topTopics.length
                ? `Most practised: ${topTopics.map(item => `${words(item.topic)} ${item.count}`).join(' · ')}.`
                : 'Mark a question done and your calendar starts here.'}</p>
            </div>
          </div>}
        </section>

        <section className="pr-section">
          <div className="pr-section-head">
            <h2>This week&apos;s build</h2>
            {current && <span>opens {weekdayTime(current.opens_at)} · closes {dayTime(current.closes_at)}</span>}
          </div>
          {errors.overview && <p className="form-error" role="alert">{errors.overview}</p>}
          {overview && (current && currentState
            ? <>
              <article className="pr-build" data-done={currentState.tone === 'done' ? '' : undefined} data-idle={currentState.tone === 'idle' ? '' : undefined}>
                <span className="pr-lamp" aria-hidden="true" />
                <div className="pr-code">{trackName(current.brief.track)}<b>{current.brief.hours} h</b></div>
                <div><h3>{current.brief.title}</h3><p>{current.brief.summary}</p></div>
                <div className="pr-clock" data-calm={currentState.calm ? '' : undefined}><b>{currentState.big}</b><span>{currentState.small}</span></div>
                <Link className="pr-ghost" href={`/practice/challenges/${encodeURIComponent(current.slug)}`}>Read the brief →</Link>
              </article>
              {pastLastStart && <p className="pr-help">Not enough time left in this week&apos;s window — next build {nextBuild}</p>}
            </>
            : <p className="muted-copy">Next build opens {nextBuild}.</p>)}
          {!!overview?.past.length && <>
            <div className="pr-label" style={{ marginTop: 26 }}>Past builds</div>
            <div className="history-list">
              {overview.past.map(item => <div key={item.slug}>
                <span className="history-topic"><Link href={`/practice/challenges/${encodeURIComponent(item.slug)}`}>{item.title}</Link></span>
                <strong>{item.total != null ? item.total.toFixed(1) : '—'}</strong>
                <small>{shortDate(item.opens_at)} · {trackName(item.track).toUpperCase()} · {(item.status ? STATUS_WORDS[item.status] ?? item.status : 'not started').toUpperCase()}</small>
              </div>)}
            </div>
          </>}
        </section>

        <section className="pr-section">
          <details className="pr-settings">
            <summary>Leaderboard settings</summary>
            <form className="card-form" onSubmit={saveSettings}>
              <label>Leaderboard name <small>2–32 characters. Leave it empty to appear as {settings?.handle ?? 'builder-xxxx'}.</small>
                <input value={displayName} maxLength={32} onChange={e => { setDisplayName(e.target.value); setSettingsSaved(false) }} placeholder={settings?.handle ?? 'builder-xxxx'} />
              </label>
              <label className="toggle-row"><input type="checkbox" checked={showRepo} onChange={e => { setShowRepo(e.target.checked); setSettingsSaved(false) }} /> Show a link to my repo on the leaderboard</label>
              <label className="toggle-row"><input type="checkbox" checked={contestReminder} onChange={e => { setContestReminder(e.target.checked); setSettingsSaved(false) }} /> Show the weekly contest reminder</label>
              {settingsError && <p className="form-error" role="alert">{settingsError}</p>}
              {settingsSaved && <p className="success-message">Saved.</p>}
              <button className="primary-button" disabled={saving || !settings}>{saving ? 'Saving…' : 'Save settings'}</button>
            </form>
          </details>
        </section>
      </>}
  </AppShell></RequireAuth>
}
