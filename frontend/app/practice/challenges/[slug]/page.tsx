'use client'

import { FormEvent, ReactNode, useEffect, useState } from 'react'
import Link from 'next/link'
import { Loader2 } from 'lucide-react'
import AppShell from '@/components/AppShell'
import CopyButton from '@/components/CopyButton'
import FlapText from '@/components/FlapText'
import RequireAuth from '@/components/RequireAuth'
import { api, AppealState, AttemptResult, Challenge, ChallengeBoard, Criterion, Writeup } from '@/lib/api'
import { clock, dayTime, duration, NBSP, weekdayTime } from '@/lib/practice-time'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

// The numbers below mirror the server (services/practice/challenges.py WRITEUP_BOUNDS,
// VIDEO_URL_CHARS; services/practice/judge.py APPEAL_REASON_CHARS; domain/practice_judge.py
// WEIGHTS). They only stop a submit that cannot succeed; the server stays the authority.
const WRITEUP_FIELDS: Array<{ key: keyof Writeup; prompt: string; min: number; max: number; rows: number }> = [
  { key: 'decisions', prompt: 'The main decisions you made, and what you considered instead', min: 200, max: 1500, rows: 6 },
  { key: 'tradeoffs', prompt: 'What you traded away for time, and what breaks first', min: 80, max: 1000, rows: 6 },
  { key: 'ai_usage', prompt: 'What AI tools wrote or suggested, and what you checked, changed or rejected', min: 40, max: 1000, rows: 6 },
  { key: 'next_steps', prompt: 'What you would do with another day', min: 40, max: 800, rows: 3 },
]
const FIELD_NAMES: Record<keyof Writeup, string> = { decisions: 'Decisions', tradeoffs: 'Trade-offs', ai_usage: 'AI use', next_steps: 'Next steps' }
const VIDEO_URL_MAX = 300
const VIDEO_HOSTS = ['youtube.com', 'www.youtube.com', 'youtu.be', 'loom.com', 'www.loom.com', 'drive.google.com']
const APPEAL_MAX = 500
const REPO_URL = /^https:\/\/github\.com\/([A-Za-z0-9-]{1,39})\/([A-Za-z0-9._-]{1,100})\/?$/

// Process (the first four) is 70 of 100, the product 30.
const CRITERIA: Array<{ key: Criterion; label: string; short: string; note: string; weight: number; process: boolean }> = [
  { key: 'thinking', label: 'Decisions and alternatives', short: 'Decisions', note: 'Decisions', weight: 25, process: true },
  { key: 'tradeoffs', label: 'Trade-offs and next steps', short: 'Trade-offs', note: 'Trade-offs', weight: 15, process: true },
  { key: 'ai_usage', label: 'How you used AI', short: 'AI use', note: 'AI use', weight: 15, process: true },
  { key: 'build_process', label: 'How it was built', short: 'Build process', note: 'Build process', weight: 15, process: true },
  { key: 'meets_brief', label: 'Meets the brief', short: 'Meets brief', note: 'Meets brief', weight: 15, process: false },
  { key: 'code_quality', label: 'Code and tests', short: 'Code & tests', note: 'Code & tests', weight: 15, process: false },
]

// The facts the judge was given, in reading order. Anything the server adds later is not shown.
const SIGNALS: Array<{ key: string; label: (value: number | boolean) => string; show: (value: number | boolean) => string }> = [
  { key: 'repo_created_in_window', label: () => 'repo created in window', show: yesNo },
  { key: 'pushes_in_window', label: () => 'pushes in window', show: count },
  { key: 'push_span_hours', label: () => 'first to last push', show: hours },
  { key: 'commits_at_snapshot', label: () => 'commits at the snapshot', show: count },
  { key: 'tests_present', label: () => 'tests present', show: yesNo },
  { key: 'readme_chars', label: () => 'README characters', show: count },
  { key: 'writeup_words', label: () => 'write-up words', show: count },
  { key: 'late_pushes_ignored', label: value => (value === 1 ? 'push after deadline, not read' : 'pushes after deadline, not read'), show: count },
  { key: 'largest_push_commits', label: () => 'most commits in one push', show: count },
  { key: 'proof_found', label: () => '.talentradar proof', show: value => (value ? 'Found' : 'Missing') },
]

function yesNo(value: number | boolean) { return value ? 'Yes' : 'No' }
function count(value: number | boolean) { return typeof value === 'number' ? value.toLocaleString('en-US') : yesNo(value) }
function hours(value: number | boolean) { return typeof value === 'number' ? `${value.toFixed(1)} h` : yesNo(value) }

const TRACK_NAMES: Record<string, string> = { ml: 'ML', ai: 'AI', devops: 'DevOps' }
const trackName = (track: string) => TRACK_NAMES[track] ?? (track ? track[0].toUpperCase() + track.slice(1) : track)
/** A criterion score: whole, or to a half after an appeal averaged two readings. */
const score = (value: number) => (Number.isInteger(value) ? String(value) : value.toFixed(1))
function ordinal(n: number) {
  const suffixes = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return `${n}${suffixes[(v - 20) % 10] || suffixes[v] || suffixes[0]}`
}
/** The length the server counts: CRLF as LF, the ends trimmed, one per character. */
const lengthOf = (text: string) => Array.from(text.replace(/\r\n/g, '\n').trim()).length

function validRepo(url: string) {
  const match = REPO_URL.exec(url)
  if (!match) return false
  const name = match[2].replace(/\.git$/, '')
  return name !== '' && name !== '.' && name !== '..'
}

function validVideo(url: string) {
  if (/[\s@]/.test(url)) return false
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' && VIDEO_HOSTS.includes(parsed.hostname) && (parsed.port === '' || parsed.port === '443')
  } catch {
    return false
  }
}

/** The strip's right-hand side: the hours before Start, the clock while it runs, the outcome after. */
function stripClock(challenge: Challenge, now: number): { big: ReactNode; small: string } {
  const attempt = challenge.attempt
  const hoursLabel = `${challenge.brief.hours} h`
  if (!attempt) {
    if (now >= new Date(challenge.closes_at).getTime()) return { big: hoursLabel, small: 'this build has closed' }
    const late = now > new Date(challenge.last_start_at).getTime()
    return { big: <FlapText value={hoursLabel.replace(/ /g, NBSP)} />, small: late ? 'too late to start' : 'once you start' }
  }
  const left = new Date(attempt.deadline_at).getTime() - now
  switch (attempt.status) {
    case 'started':
    case 'submitted':
      if (left > 0) return { big: <FlapText value={duration(left, false).replace(/ /g, NBSP)} />, small: `left · until ${weekdayTime(attempt.deadline_at)}` }
      return { big: 'Time is up', small: now < new Date(attempt.editable_until).getTime() ? `write-up editable until ${clock(attempt.editable_until)}` : 'window closed' }
    case 'queued':
      return attempt.held
        ? { big: 'Under review', small: 'a person checks this before it is ranked' }
        : { big: 'Queued', small: 'waiting for the judge' }
    case 'judged':
      return { big: attempt.total != null ? <FlapText value={attempt.total.toFixed(1)} /> : 'Judged', small: 'of 100' }
    case 'rejected':
      return { big: 'Rejected', small: 'read why below' }
    case 'judge_failed':
      return { big: 'Not judged', small: 'we could not judge this yet' }
    default:
      return { big: 'Not submitted', small: 'this build closed' }
  }
}

/** What an appeal that is not on offer says: it is in progress, it ended, or it was never open. */
const APPEAL_NOTES: Partial<Record<AppealState, string>> = {
  pending: 'Appeal sent. The judge will read your submission again, and your score then becomes the average of the two readings.',
  done: 'Your appeal has been read.',
  gave_up: 'We could not make the second reading, so your first score stands.',
  refused: 'A second reading would not have been of the same material as the first, so your first score stands.',
  reviewed: 'A person has already reviewed this result.',
  closed: 'The appeal window for this result has closed.',
}

export default function ChallengePage({ params }: { params: { slug: string } }) {
  return <RequireAuth><AppShell><ChallengeView slug={params.slug} /></AppShell></RequireAuth>
}

function ChallengeView({ slug }: { slug: string }) {
  const [challenge, setChallenge] = useState<Challenge | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [now, setNow] = useState(() => Date.now())
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState('')

  const [repo, setRepo] = useState('')
  const [writeup, setWriteup] = useState<Writeup>({ decisions: '', tradeoffs: '', ai_usage: '', next_steps: '' })
  const [video, setVideo] = useState('')
  const [attempted, setAttempted] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [justSaved, setJustSaved] = useState(false)

  const [result, setResult] = useState<AttemptResult | null>(null)
  const [resultError, setResultError] = useState('')
  const [board, setBoard] = useState<ChallengeBoard | null>(null)
  const [reason, setReason] = useState('')
  const [appealing, setAppealing] = useState(false)
  const [appealError, setAppealError] = useState('')

  function adopt(next: Challenge) {
    const attempt = next.attempt
    setRepo(attempt?.repo_url ?? '')
    setWriteup(attempt?.writeup ?? { decisions: '', tradeoffs: '', ai_usage: '', next_steps: '' })
    setVideo(attempt?.video_url ?? '')
  }

  useEffect(() => {
    let live = true
    api.practice.challenge(slug)
      .then(next => { if (live) { setChallenge(next); adopt(next) } })
      .catch(err => { if (live) setError(err instanceof Error ? err.message : 'Could not load this build.') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [slug])

  // The countdown moves once a minute, as on /practice.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(id)
  }, [])

  // Only these statuses have anything to fetch; queued is a message, not a result.
  const status = challenge?.attempt?.status
  const hasResult = status === 'judged' || status === 'rejected' || status === 'judge_failed'
  useEffect(() => {
    if (!hasResult) { setResult(null); return }
    let live = true
    setResultError('')
    api.practice.result(slug)
      .then(next => { if (live) setResult(next) })
      .catch(err => { if (live) setResultError(err instanceof Error ? err.message : 'Could not load your result.') })
    return () => { live = false }
  }, [slug, hasResult])

  // Where the score stands is a nicety: a failed read just leaves it out.
  const ranked = result?.status === 'judged' && !result.held && result.total != null
  useEffect(() => {
    if (!ranked) { setBoard(null); return }
    let live = true
    api.practice.board(slug).then(next => { if (live) setBoard(next) }).catch(() => undefined)
    return () => { live = false }
  }, [slug, ranked])

  async function start() {
    setStarting(true)
    setStartError('')
    try {
      const next = await api.practice.start(slug)
      setChallenge(next)
      adopt(next)
      // The Start button is gone now: put keyboard focus where the work begins.
      window.setTimeout(() => document.getElementById('pr-repo')?.focus(), 0)
    } catch (err) {
      setStartError(err instanceof Error ? err.message : 'Could not start this build.')
    } finally {
      setStarting(false)
    }
  }

  function problems() {
    const out: string[] = []
    if (!validRepo(repo.trim())) out.push('Repo: paste the link as https://github.com/<owner>/<repo>.')
    for (const field of WRITEUP_FIELDS) {
      const n = lengthOf(writeup[field.key])
      if (n < field.min || n > field.max) out.push(`${FIELD_NAMES[field.key]}: write ${field.min.toLocaleString('en-US')} to ${field.max.toLocaleString('en-US')} characters (yours has ${n.toLocaleString('en-US')}).`)
    }
    const link = video.trim()
    if (link.length > VIDEO_URL_MAX) out.push(`Video: keep the link to ${VIDEO_URL_MAX} characters or fewer.`)
    else if (link && !validVideo(link)) out.push('Video: use an https link on youtube.com, youtu.be, loom.com or drive.google.com.')
    return out
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    setAttempted(true)
    setJustSaved(false)
    setSaveError('')
    if (problems().length) return
    setSaving(true)
    try {
      const next = await api.practice.submit(slug, {
        repo_url: repo.trim(),
        writeup: {
          decisions: writeup.decisions.trim(),
          tradeoffs: writeup.tradeoffs.trim(),
          ai_usage: writeup.ai_usage.trim(),
          next_steps: writeup.next_steps.trim(),
        },
        video_url: video.trim() || null,
      })
      setChallenge(next)
      adopt(next)
      setAttempted(false)
      setJustSaved(true)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Could not save your submission.')
    } finally {
      setSaving(false)
    }
  }

  async function appeal(event: FormEvent) {
    event.preventDefault()
    const text = reason.trim()
    if (!text) { setAppealError('Say, in a sentence or two, what you think the judge got wrong.'); return }
    if (lengthOf(text) > APPEAL_MAX) { setAppealError(`Keep your reason to ${APPEAL_MAX} characters or fewer.`); return }
    setAppealing(true)
    setAppealError('')
    try {
      setResult(await api.practice.appeal(slug, text))
      setReason('')
      // The form has gone: focus its note, so a keyboard user hears what happened.
      window.setTimeout(() => document.getElementById('pr-appeal-note')?.focus(), 0)
    } catch (err) {
      setAppealError(err instanceof Error ? err.message : 'Could not send your appeal.')
    } finally {
      setAppealing(false)
    }
  }

  if (loading) return <div className="loading-state"><Loader2 className="spin" /> Loading the build…</div>
  if (!challenge) return <>
    <p className="form-error" role="alert">{error || 'We could not find that build.'}</p>
    <p className="pr-help"><Link href="/practice">← Back to Practice</Link></p>
  </>

  const brief = challenge.brief
  const attempt = challenge.attempt
  const closed = now >= new Date(challenge.closes_at).getTime()
  const pastLastStart = now > new Date(challenge.last_start_at).getTime()
  const deadline = attempt ? new Date(attempt.deadline_at).getTime() : 0
  const editableUntil = attempt ? new Date(attempt.editable_until).getTime() : 0
  const running = !!attempt && (attempt.status === 'started' || attempt.status === 'submitted')
  const editable = running && now < editableUntil
  const strip = stripClock(challenge, now)
  const live = problems()
  const bad = (invalid: boolean) => (attempted && invalid ? true : undefined)
  const videoHref = externalHref(attempt?.video_url)
  const repoHref = externalHref(attempt?.repo_url)
  const saved = attempt?.writeup ?? null
  const snapshotHref = externalHref(result?.snapshot_url)
  const weights = result?.weights
  const scores = result?.scores ?? null
  const notes = result?.notes ?? null
  const signals = result?.signals ?? null
  const spread = scores ? Object.values(scores) : []
  const weakest = scores && Math.min(...spread) < Math.max(...spread)
    ? CRITERIA.reduce((low, item) => (scores[item.key] < scores[low.key] ? item : low), CRITERIA[0]).key
    : null
  const standing = board?.me && result?.total != null ? `${ordinal(board.me.rank)} of ${board.total_entries}${board.provisional ? ' so far' : ''}` : ''
  const appealUntil = result?.appeal.until ? dayTime(result.appeal.until) : ''

  return <>
    <section className="page-heading split-heading">
      <div>
        <span className="board-kicker">Build · {trackName(brief.track)} · {brief.hours} h</span>
        <h1>{brief.title}<span>.</span></h1>
        <p>{brief.summary}</p>
      </div>
      <div className="tracker-total"><strong>{strip.big}</strong><span>{strip.small}</span></div>
    </section>

    <div className="pr-brief">
      <div className="pr-prose">
        {brief.context.map((paragraph, i) => <p key={i}>{paragraph}</p>)}
        <div className="pr-label">Must have</div>
        <ul className="pr-list">{brief.must_have.map((item, i) => <li key={i}>{item}</li>)}</ul>
        {brief.nice_to_have.length > 0 && <>
          <div className="pr-label">Nice to have</div>
          <ul className="pr-list">{brief.nice_to_have.map((item, i) => <li key={i}>{item}</li>)}</ul>
        </>}
        {brief.constraints.length > 0 && <>
          <div className="pr-label">Constraints</div>
          <ul className="pr-list">{brief.constraints.map((item, i) => <li key={i}>{item}</li>)}</ul>
        </>}
        <div className="pr-label">Deliverables</div>
        <ul className="pr-list">{brief.deliverables.map((item, i) => <li key={i}>{item}</li>)}</ul>
        {brief.bengaluru_context && <>
          <div className="pr-label">Where this work happens</div>
          <p>{brief.bengaluru_context}</p>
        </>}
      </div>

      <aside>
        {!attempt && <>
          <div className="pr-label">Start</div>
          <button type="button" className="primary-button" disabled={starting || closed || pastLastStart} onClick={start}>
            {starting ? 'Starting…' : `Start the ${brief.hours} h clock`}
          </button>
          {startError && <p className="form-error" role="alert" style={{ marginTop: 12 }}>{startError}</p>}
          <p className="pr-help">
            {closed
              ? 'This build has closed.'
              : pastLastStart
                ? `Not enough time left in this week's window — next build ${dayTime(challenge.closes_at)}`
                : `Starting begins your ${brief.hours} h clock. It does not pause.`}
          </p>
        </>}

        {running && now < deadline && attempt && <>
          <div className="pr-label">Your proof code</div>
          <div className="question-card pr-proof" style={{ marginTop: 0 }}>
            <code>{attempt.proof_code}</code><CopyButton text={attempt.proof_code} />
            <p>Add a file named <code style={{ fontSize: 12, letterSpacing: 0 }}>.talentradar</code> containing this code to the repo before your deadline.</p>
          </div>
        </>}

        <div className="pr-label">Rules</div>
        <ul className="pr-list">
          <li>Your clock: {brief.hours} h from Start.{attempt ? ` ${weekdayTime(attempt.started_at).replace(' IST', '')} → ${weekdayTime(attempt.deadline_at)}.` : ''}</li>
          <li>Create a new public repo after you press Start. No forks.</li>
          <li>Add a file named .talentradar containing your proof code.</li>
          <li>We read the last push GitHub recorded before your deadline. The write-up can be edited for one hour after it.</li>
          <li>One entry per person.</li>
        </ul>

        <div className="pr-label">What the judge looks at</div>
        <ul className="pr-rubric">
          {CRITERIA.map(item => <li key={item.key} data-process={item.process ? '' : undefined}><span>{item.label}</span><em>{item.weight}</em></li>)}
        </ul>
        {brief.judge_focus.map((line, i) => <p className="pr-help" key={i}>{line}</p>)}
      </aside>
    </div>

    {attempt && (running || attempt.repo_url) && <section className="pr-section">
      <div className="pr-section-head">
        <h2>Your submission</h2>
        {running && <span>{editable ? `editable until ${dayTime(attempt.editable_until)}` : 'the window has closed'}</span>}
      </div>

      {editable
        ? <form className="pr-form" onSubmit={save} noValidate>
          <div className="pr-field">
            <label htmlFor="pr-repo">Public GitHub repo<small>github.com/owner/repo</small></label>
            <input id="pr-repo" type="url" inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://github.com/you/your-build" value={repo}
              aria-invalid={bad(!validRepo(repo.trim()))} onChange={e => { setRepo(e.target.value); setJustSaved(false) }} />
            <p className="pr-help">Created after you pressed Start · public · not a fork.</p>
          </div>

          {WRITEUP_FIELDS.map(field => {
            const n = lengthOf(writeup[field.key])
            const outside = n < field.min || n > field.max
            return <div className="pr-field" key={field.key} data-short={outside && n > 0 ? '' : undefined}>
              <label htmlFor={`pr-${field.key}`}>{field.prompt}<small>{n.toLocaleString('en-US')} / {field.min}–{field.max.toLocaleString('en-US')}</small></label>
              <textarea id={`pr-${field.key}`} rows={field.rows} style={field.key === 'next_steps' ? { minHeight: 70 } : undefined} value={writeup[field.key]}
                aria-invalid={bad(outside)} onChange={e => { setWriteup({ ...writeup, [field.key]: e.target.value }); setJustSaved(false) }} />
            </div>
          })}

          <div className="pr-field">
            <label htmlFor="pr-video">Screen recording, optional<small>YouTube, Loom or Drive link</small></label>
            <input id="pr-video" type="url" inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://www.loom.com/share/…" value={video}
              aria-invalid={bad(!!video.trim() && (video.trim().length > VIDEO_URL_MAX || !validVideo(video.trim())))} onChange={e => { setVideo(e.target.value); setJustSaved(false) }} />
            <p className="pr-help">Only you and a person reviewing an appeal can see it. The judge never watches it.</p>
          </div>

          {attempted && live.length > 0 && <div className="form-error" role="alert">
            <ul style={{ margin: 0, paddingLeft: 18 }}>{live.map(line => <li key={line}>{line}</li>)}</ul>
          </div>}
          {saveError && <p className="form-error" role="alert">{saveError}</p>}
          {justSaved && <p className="success-message" role="status">Saved.</p>}
          <div className="pr-saved">
            <button className="primary-button" disabled={saving}>{saving ? 'Saving…' : 'Save submission'}</button>
            <span>{attempt.submitted_at ? `Saved ${clock(attempt.submitted_at)} IST · ` : 'Not saved yet · '}editable until {weekdayTime(attempt.editable_until)}</span>
          </div>
        </form>

        : <div className="pr-prose">
          {repoHref
            ? <p className="pr-meta">Repo <a href={repoHref} {...EXTERNAL_LINK_PROPS}>{attempt.repo_url} ↗</a></p>
            : <p className="muted-copy">The submission window closed without a repo being saved.</p>}
          {attempt.video_url && <p className="pr-meta">{videoHref ? <>Recording <a href={videoHref} {...EXTERNAL_LINK_PROPS}>{attempt.video_url} ↗</a></> : 'A recording link was saved.'}</p>}
          {saved && <details className="pr-appeal" style={{ marginTop: 14 }}>
            <summary>Read your write-up</summary>
            {WRITEUP_FIELDS.map(field => <div key={field.key}>
              <div className="pr-label">{FIELD_NAMES[field.key]}</div>
              <p style={{ whiteSpace: 'pre-wrap' }}>{saved[field.key]}</p>
            </div>)}
          </details>}
        </div>}
    </section>}

    {attempt && !running && <section className="pr-section">
      <div className="pr-section-head"><h2>Your result</h2></div>
      {resultError && <p className="form-error" role="alert">{resultError}</p>}
      {hasResult && !result && !resultError && <p className="muted-copy">Loading your result…</p>}

      {attempt.status === 'queued' && (attempt.held
        ? <p><span className="pr-pill">Under review</span> <span className="pr-meta">A person checks this build before it is ranked.</span></p>
        : <p className="muted-copy">Queued for the judge — results within a day.</p>)}
      {attempt.status === 'missed' && <p className="muted-copy">This build closed without a submission.</p>}
      {attempt.status === 'judge_failed' && <p className="muted-copy">We could not judge this yet.{result?.reject_reason ? ` ${result.reject_reason}` : ''}</p>}
      {attempt.status === 'rejected' && result && <p className="form-error" role="status">This build could not be judged. {result.reject_reason ?? 'The repo did not meet the rules.'}</p>}
      {result?.held && <p><span className="pr-pill">Under review</span> <span className="pr-meta">Held off the leaderboard until a person has checked it.</span></p>}

      {result?.status === 'judged' && scores && result.total != null && <>
        <div className="results-head">
          <div className="results-total">
            <strong><FlapText value={result.total.toFixed(1)} /></strong>
            <span>of 100{standing && <> · <Link href="/practice/leaderboard">{standing}</Link></>}</span>
          </div>
          <div className="results-dims">
            {CRITERIA.map(item => <div key={item.key} className={weakest === item.key ? 'weakest' : undefined}>
              <span>{item.short} · {weights?.[item.key] ?? item.weight}</span>
              <i><b style={{ width: `${Math.max(0, Math.min(10, scores[item.key])) * 10}%` }} /></i>
              <em>{score(scores[item.key])}</em>
            </div>)}
          </div>
        </div>
        {result.rejudged && <p className="pr-help">Re-judged on appeal: the average of two readings.</p>}

        {notes && <>
          <ul className="pr-notes">
            {CRITERIA.map(item => <li key={item.key}><span>{item.note}</span><p>{notes[item.key]}</p><em>{score(scores[item.key])}</em></li>)}
          </ul>
          <div className="pr-two" style={{ marginTop: 22 }}>
            <div><div className="pr-label">Strengths</div><ul className="pr-list">{notes.strengths.map((line, i) => <li key={i}>{line}</li>)}</ul></div>
            <div><div className="pr-label">To improve</div><ul className="pr-list">{notes.improvements.map((line, i) => <li key={i}>{line}</li>)}</ul></div>
          </div>
        </>}

        {signals && <>
          <div className="pr-label">What the judge was told</div>
          <div className="pr-sig">
            {SIGNALS.filter(item => item.key in signals).map(item => {
              const value = signals[item.key]
              return <div key={item.key}><b>{item.show(value)}</b><span>{item.label(value)}</span></div>
            })}
          </div>
        </>}

        {result.snapshot_sha && <p className="pr-meta" style={{ marginTop: 12 }}>
          Judged commit {snapshotHref ? <a href={snapshotHref} {...EXTERNAL_LINK_PROPS}>{result.snapshot_sha.slice(0, 7)}</a> : result.snapshot_sha.slice(0, 7)} · commit dates in the repo are treated as claims
        </p>}

        {result.appeal.available
          ? <details className="pr-appeal" style={{ marginTop: 18 }}>
            <summary>Appeal this result</summary>
            <form className="pr-field" style={{ marginTop: 12, maxWidth: 760 }} onSubmit={appeal}>
              <label htmlFor="pr-appeal">Why you think it is wrong<small>{lengthOf(reason)} / {APPEAL_MAX}</small></label>
              <textarea id="pr-appeal" rows={4} maxLength={APPEAL_MAX} style={{ minHeight: 80 }} value={reason} onChange={e => setReason(e.target.value)} />
              <p className="pr-help">Your reason goes to a person, not to the judge. The judge reads your submission again, blind, and your score becomes the average of the two readings. One appeal{appealUntil ? `, until ${appealUntil}` : ''}.</p>
              {appealError && <p className="form-error" role="alert">{appealError}</p>}
              <button className="pr-ghost" style={{ marginTop: 10 }} disabled={appealing}>{appealing ? 'Sending…' : 'Send appeal'}</button>
            </form>
          </details>
          : APPEAL_NOTES[result.appeal.state] && <p id="pr-appeal-note" tabIndex={-1} className="pr-help">{APPEAL_NOTES[result.appeal.state]}</p>}

        <p className="pr-help"><Link href="/practice/leaderboard">See the leaderboard →</Link></p>
      </>}
    </section>}
  </>
}
