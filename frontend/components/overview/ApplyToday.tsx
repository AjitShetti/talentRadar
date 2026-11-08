'use client'

import Link from 'next/link'
import { useLayoutEffect, useRef, useState } from 'react'
import { api, JobMatch } from '@/lib/api'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

/** What the Overview reads of the last overnight sweep (services/sweep.py::get_last_sweep()). */
export type SweepSummary = { finished_at: string | null; boards: number; postings_read: number }

const GAP = 12
const PEEK = 11
/** How long a card takes to fade out, and the whole box after the last one. */
const LEAVE_MS = 380

/** Two letters for a company, where the stack would otherwise want a logo. */
const initials = (company: string) => {
  const words = company.split(/[\s&.,()/-]+/).filter(Boolean)
  if (words.length === 0) return '?'
  return words.length === 1 ? words[0].slice(0, 2) : words[0][0] + words[1][0]
}

const daysAgo = (iso: string | null | undefined) => {
  if (!iso) return null
  const at = new Date(iso).getTime()
  if (Number.isNaN(at)) return null
  const days = Math.floor((Date.now() - at) / 86_400_000)
  return days <= 0 ? 'posted today' : days === 1 ? 'posted yesterday' : `posted ${days}d ago`
}

const clock = (iso: string | null) => {
  if (!iso) return null
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? null : at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })
}

/**
 * Today's roles from the overnight sweep, as a stack to work through: the top one
 * is the one to apply to, "Show all" fans the rest out, and "Mark applied" moves the
 * role to the tracker and lifts the next one up. Once every role is applied the box
 * leaves the page, so what comes after it moves up.
 *
 * "Mark applied" records only what the user says they did; nothing is sent anywhere.
 */
export default function ApplyToday({ matches, sweep }: { matches: JobMatch[]; sweep: SweepSummary | null | undefined }) {
  const [applied, setApplied] = useState(() => new Set(matches.filter(m => m.applied).map(m => m.id)))
  const [leaving, setLeaving] = useState<Set<string>>(() => new Set())
  const [open, setOpen] = useState(false)
  const [failed, setFailed] = useState(false)
  // Shown only while there was something to do when the page opened; the last "Mark applied" then fades it out.
  const [gone, setGone] = useState(() => matches.every(m => m.applied))
  const [closing, setClosing] = useState(false)
  const [cardH, setCardH] = useState(88)
  const front = useRef<HTMLElement | null>(null)

  const total = matches.length
  const pending = matches.filter(m => !applied.has(m.id) || leaving.has(m.id))
  const live = pending.filter(m => !leaving.has(m.id))

  useLayoutEffect(() => {
    const el = front.current
    if (!el) return
    const measure = () => setCardH(el.offsetHeight)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [live.length])

  if (total === 0 || gone) return null

  async function markApplied(match: JobMatch) {
    setFailed(false)
    setApplied(current => new Set(current).add(match.id))
    setLeaving(current => new Set(current).add(match.id))
    const last = live.length === 1
    if (live.length <= 2) setOpen(false)
    setTimeout(() => setLeaving(current => { const next = new Set(current); next.delete(match.id); return next }), LEAVE_MS)
    if (last) {
      setTimeout(() => setClosing(true), LEAVE_MS)
      setTimeout(() => setGone(true), LEAVE_MS * 2)
    }
    try {
      if (match.application_id) await api.applications.update(match.application_id, { status: 'applied' })
      else await api.applications.create(match.id, 'applied')
    } catch {
      // Put the role back: the tracker did not take it.
      setClosing(false)
      setGone(false)
      setApplied(current => { const next = new Set(current); next.delete(match.id); return next })
      setFailed(true)
    }
  }

  const count = live.length
  const height = count === 0 ? cardH : open ? count * cardH + (count - 1) * GAP : cardH + Math.min(count - 1, 2) * PEEK
  const done = total - live.length
  const swept = sweep ? [clock(sweep.finished_at) && `Swept ${clock(sweep.finished_at)}`, `${sweep.boards} board${sweep.boards === 1 ? '' : 's'}`, `${sweep.postings_read} postings read`, `${total} kept`].filter(Boolean).join(' · ') : `${total} kept for your target roles`

  return <section className="at" data-closing={closing ? 'true' : undefined} aria-labelledby="at-title">
    <div className="at-brief">
      <span className="at-eyebrow">Apply today</span>
      <h2 id="at-title">Apply for these jobs</h2>
      <p>Last night&apos;s sweep, read against your resume. Open a posting, send your application, then mark it applied and the next one comes up.</p>
      <div className="at-progress">
        <div className="at-segs" style={{ '--n': total } as React.CSSProperties} aria-hidden="true">
          {matches.map((m, i) => <i key={m.id} data-on={i < done ? 'true' : undefined} />)}
        </div>
        <small><b>{done} of {total}</b> applied today</small>
      </div>
      <div className="at-sweep">{swept}</div>
    </div>

    <div className="at-col">
      <div
        className="at-stack"
        data-open={open ? 'true' : undefined}
        style={{ height }}
        aria-live="polite"
        onClick={e => { if (!open && count > 1 && !(e.target as HTMLElement).closest('a,button')) setOpen(true) }}
      >
        {pending.map(match => {
          const i = live.indexOf(match)
          const out = i === -1
          const depth = out ? 0 : open ? 0 : Math.min(i, 2)
          const transform = out ? undefined : open ? `translateY(${i * (cardH + GAP)}px)` : `translateY(${depth * PEEK}px) scale(${1 - depth * 0.05})`
          const reachable = !out && (open || i === 0)
          const posting = externalHref(match.source_url)
          const meta = [match.company || 'Company not listed', match.location || (match.is_remote ? 'Remote' : null), match.salary_raw, daysAgo(match.posted_at)].filter(Boolean).join(' · ')
          return <article
            key={match.id}
            ref={i === 0 ? front : undefined}
            className="at-job"
            data-leaving={out ? 'true' : undefined}
            data-depth={depth > 0 ? depth : undefined}
            data-hidden={!open && i > 2 ? 'true' : undefined}
            data-front={i === 0 ? 'true' : undefined}
            style={{ transform, zIndex: out ? total + 1 : count - i }}
            aria-hidden={reachable ? undefined : true}
          >
            <span className="at-mono" aria-hidden="true">{initials(match.company || '?')}</span>
            <div className="at-body">
              <h3><Link href={`/roles/${encodeURIComponent(match.id)}`} tabIndex={reachable ? 0 : -1}>{match.title}</Link></h3>
              <p>{meta}</p>
            </div>
            <div className="at-acts">
              {posting && <a className="text-button" href={posting} {...EXTERNAL_LINK_PROPS} tabIndex={reachable ? 0 : -1}>Open posting <span aria-hidden="true">↗</span></a>}
              <button
                className={i === 0 ? 'primary-button' : 'outline-button'}
                type="button"
                tabIndex={reachable ? 0 : -1}
                disabled={out}
                onClick={() => markApplied(match)}
              >Mark applied</button>
            </div>
          </article>
        })}
      </div>
      {failed && <p className="form-error" role="alert">That one did not reach your tracker. Try again.</p>}
      {count > 1 && <button className="at-toggle" type="button" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        {open ? 'Hide' : `Show all ${count}`}<i className="caret" aria-hidden="true" />
      </button>}
    </div>
  </section>
}
