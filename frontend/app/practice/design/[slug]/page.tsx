'use client'

import { ComponentType, useEffect, useRef, useState } from 'react'
import dynamic from 'next/dynamic'
import Link from 'next/link'
import { Loader2 } from 'lucide-react'
import AppShell from '@/components/AppShell'
import FlapText from '@/components/FlapText'
import PrepTabs from '@/components/PrepTabs'
import type { DesignBoardProps } from '@/components/practice/DesignBoard'
import RequireAuth from '@/components/RequireAuth'
import { api, ApiError, DesignPage } from '@/lib/api'
import { clock } from '@/lib/practice-time'

const AUTOSAVE_MS = 5_000
const REASONING_MAX = 4_000
const RE_READ_MS = 5_000
const RE_READS = 6
/** Browsers refuse a keepalive request over 64 KB; a bigger drawing goes as an ordinary one. */
const KEEPALIVE_MAX_CHARS = 60_000

const CRITERIA = [
  { key: 'requirements', label: 'Requirements' },
  { key: 'components', label: 'Components' },
  { key: 'data_flow', label: 'Data flow' },
  { key: 'scale', label: 'Scale' },
  { key: 'failure_handling', label: 'Failure handling' },
  { key: 'tradeoffs', label: 'Trade-offs' },
]

const NOT_SAVED = 'Not saved. It will try again on your next change.'
const SAVES_ITSELF = 'Saved automatically. Your drawing stays private.'
const FEEDBACK_PROBLEMS = {
  limit: "You have used today's feedback. Your drawing is saved and the day is counted.",
  busy: 'Feedback is busy right now. Try again in a minute. Your drawing is saved and the day is counted.',
}

/** Stands in for the board when its code could not be fetched, so the page can say so. */
function BoardMissing({ onLoadFailed }: DesignBoardProps) {
  useEffect(onLoadFailed, [onLoadFailed])
  return null
}

const DesignBoard = dynamic<DesignBoardProps>(() => {
  // Excalidraw reads this for its fonts, which we serve; otherwise it asks a public CDN for them.
  (window as unknown as { EXCALIDRAW_ASSET_PATH: string }).EXCALIDRAW_ASSET_PATH = '/excalidraw/'
  return import('@/components/practice/DesignBoard').catch(() => ({ default: BoardMissing as ComponentType<DesignBoardProps> }))
}, { ssr: false, loading: () => <div className="loading-state"><Loader2 className="spin" /> Loading the whiteboard…</div> })

export default function DesignProblemPage({ params, searchParams }: { params: { slug: string }; searchParams: { day?: string } }) {
  return <RequireAuth><AppShell><PrepTabs /><DesignView slug={params.slug} day={searchParams.day} /></AppShell></RequireAuth>
}

function DesignView({ slug, day }: { slug: string; day?: string }) {
  const [page, setPage] = useState<DesignPage | null>(null)
  const [missing, setMissing] = useState(false)
  const [error, setError] = useState('')
  const [reasoning, setReasoning] = useState('')
  const [closed, setClosed] = useState(false)
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [notSaved, setNotSaved] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [boardFailed, setBoardFailed] = useState(false)
  const [sending, setSending] = useState(false)
  const [feedbackError, setFeedbackError] = useState('')

  // What the timer, the page-hidden flush and the unmount flush all save: never a stale render's.
  const latest = useRef<{ elements: unknown[]; reasoning: string }>({ elements: [], reasoning: '' })
  const dirty = useRef(false)
  const locked = useRef(true)
  const timer = useRef<number | undefined>(undefined)
  const live = useRef(true)

  const readOnly = closed || !page?.editable
  locked.current = readOnly

  async function save(keepalive = false): Promise<boolean> {
    window.clearTimeout(timer.current)
    if (!dirty.current || locked.current) return true
    dirty.current = false
    const body = latest.current
    try {
      const saved = await api.practice.saveAnswer(slug, body, { keepalive: keepalive && JSON.stringify(body).length < KEEPALIVE_MAX_CHARS })
      if (!live.current) return true
      setSavedAt(saved.saved_at)
      setNotSaved(false)
      setSaveError('')
      return true
    } catch (err) {
      dirty.current = true
      if (!live.current) return false
      setNotSaved(true)
      if (err instanceof ApiError && err.status === 409) {
        locked.current = true
        setClosed(true)
      } else if (err instanceof ApiError && err.status === 422) {
        setSaveError(err.message)
      }
      return false
    }
  }

  // The flush handlers outlive the render that added them, so they always reach the newest save.
  const saveNow = useRef(save)
  saveNow.current = save

  function changed() {
    dirty.current = true
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => void saveNow.current(), AUTOSAVE_MS)
  }

  const onBoardChange = useRef((elements: unknown[]) => {
    latest.current = { ...latest.current, elements }
    changed()
  })
  const onBoardFailed = useRef(() => setBoardFailed(true))

  useEffect(() => {
    live.current = true
    let current = true
    setPage(null)
    setError('')
    setMissing(false)
    api.practice.design(slug, day).then(next => {
      if (!current) return
      latest.current = { elements: next.answer?.elements ?? [], reasoning: next.answer?.reasoning ?? '' }
      dirty.current = false
      setReasoning(latest.current.reasoning)
      setSavedAt(next.answer?.saved_at ?? null)
      setClosed(false)
      setPage(next)
    }).catch(err => {
      if (!current) return
      if (err instanceof ApiError && err.status === 404) setMissing(true)
      else setError(err instanceof Error ? err.message : 'Could not load this problem.')
    })
    return () => { current = false }
  }, [slug, day])

  useEffect(() => {
    function flush() { if (document.visibilityState === 'hidden') void saveNow.current(true) }
    document.addEventListener('visibilitychange', flush)
    return () => {
      document.removeEventListener('visibilitychange', flush)
      void saveNow.current(true)
      live.current = false
    }
  }, [])

  async function getFeedback() {
    setSending(true)
    setFeedbackError('')
    setSaveError('')
    const saved = await save()
    if (!saved) { setSending(false); return }
    try {
      setPage(await api.practice.feedback(slug))
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // One is already on its way, from another tab or an earlier press: wait for it.
        const before = page?.feedback?.at
        for (let read = 0; read < RE_READS && live.current; read += 1) {
          await new Promise(resolve => window.setTimeout(resolve, RE_READ_MS))
          const next = await api.practice.design(slug).catch(() => null)
          if (next?.feedback && next.feedback.at !== before) { setPage(next); setSending(false); return }
        }
      }
      setFeedbackError(err instanceof Error ? err.message : 'Could not get feedback.')
    } finally {
      setSending(false)
    }
  }

  if (missing) return <div className="empty-state">
    <h2>We could not find that problem.</h2>
    <p><Link href="/practice">Back to Practice</Link></p>
  </div>
  if (error) return <p className="form-error" role="alert">{error}</p>
  if (!page) return <div className="loading-state"><Loader2 className="spin" /> Loading the problem…</div>

  const { problem, answer, feedback } = page
  const cleared = !!answer && answer.elements === null
  const count = reasoning.length
  const low = feedback ? CRITERIA.reduce((lowest, item) => (feedback.scores[item.key] < feedback.scores[lowest.key] ? item : lowest), CRITERIA[0]).key : null
  const lastAnswer = !day && page.last_answer_day

  return <>
    <section className="page-heading">
      <span className="board-kicker">Practice · system design</span>
      <h1>{problem.title}<span>.</span></h1>
      <p>{problem.summary} About {problem.minutes} minutes.</p>
    </section>

    <div className="pr-solve">
      <div className="pr-prose">
        {problem.context.map((paragraph, i) => <p key={i}>{paragraph}</p>)}
        <div className="pr-label">Must handle</div>
        <ul className="pr-list">{problem.must_handle.map((item, i) => <li key={i}>{item}</li>)}</ul>
        <div className="pr-label">Show in your answer</div>
        <ul className="pr-list">{problem.show_in_answer.map((item, i) => <li key={i}>{item}</li>)}</ul>
        {lastAnswer && <p className="pr-help"><Link href={`/practice/design/${encodeURIComponent(problem.slug)}?day=${encodeURIComponent(lastAnswer)}`}>See your last answer</Link></p>}
      </div>

      <section className="pr-solve-work" aria-label="Your answer">
        {closed && <p className="form-error" role="alert">This one closed at 06:00 IST</p>}
        {!closed && !page.editable && <p className="pr-help">This answer is read-only.</p>}
        {cleared
          ? <p className="muted-copy">This drawing was cleared after 180 days.</p>
          : boardFailed
            ? <p className="form-error" role="alert">The whiteboard could not load. Reload the page to try again; your reasoning below still saves.</p>
            : <>
              <p className="pr-help">Feedback reads your labels and arrows, not freehand drawing.</p>
              <div className="pr-whiteboard">
                <DesignBoard key={page.day} elements={answer?.elements ?? []} readOnly={readOnly} onChange={onBoardChange.current} onLoadFailed={onBoardFailed.current} />
              </div>
            </>}

        <div className="pr-field" data-short={count > REASONING_MAX ? '' : undefined}>
          <label htmlFor="pr-reasoning">Your reasoning and trade-offs<small>{count.toLocaleString('en-US')} / {REASONING_MAX.toLocaleString('en-US')}</small></label>
          <textarea id="pr-reasoning" rows={5} value={reasoning} readOnly={readOnly} aria-invalid={count > REASONING_MAX || undefined}
            onChange={e => {
              setReasoning(e.target.value)
              latest.current = { ...latest.current, reasoning: e.target.value }
              changed()
            }} />
        </div>

        {saveError && <p className="form-error" role="alert">{saveError}</p>}
        {feedbackError && <p className="form-error" role="alert">{feedbackError}</p>}
        {page.feedback_problem && <p className="form-error" role="alert">{FEEDBACK_PROBLEMS[page.feedback_problem]}</p>}
        <div className="pr-saved">
          {!readOnly && <button type="button" className="primary-button" disabled={sending} onClick={getFeedback}>{sending ? 'Reading it…' : 'Get feedback'}</button>}
          <span>{notSaved ? NOT_SAVED : savedAt ? `Saved ${clock(savedAt)}` : SAVES_ITSELF}</span>
        </div>
      </section>
    </div>

    {feedback && <section className="pr-section" aria-live="polite">
      <div className="pr-section-head"><h2>Feedback</h2><span>{clock(feedback.at)}</span></div>
      <div className="results-head">
        <div className="results-total">
          <strong><FlapText value={feedback.total.toFixed(0)} /></strong>
          <span>of 100</span>
        </div>
        <div className="results-dims">
          {CRITERIA.map(item => <div key={item.key} className={low === item.key ? 'weakest' : undefined}>
            <span>{item.label}</span>
            <i><b style={{ width: `${Math.max(0, Math.min(10, feedback.scores[item.key])) * 10}%` }} /></i>
            <em>{feedback.scores[item.key]}</em>
          </div>)}
        </div>
      </div>
      <ul className="pr-notes">
        {CRITERIA.map(item => <li key={item.key}><span>{item.label}</span><p>{feedback.notes[item.key]}</p><em>{feedback.scores[item.key]}</em></li>)}
      </ul>
      <div className="pr-prose">
        <div className="pr-label">Work on next</div>
        <p>{feedback.work_on_next}</p>
      </div>
    </section>}
  </>
}
