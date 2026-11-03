'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { useDayLit, useIstMinute, useRoutine } from '@/components/DayLine'
import RoutineEditor from '@/components/RoutineEditor'
import { api, currentEmail } from '@/lib/api'
import { publishRoutine, setDayLit, tickLocally } from '@/lib/day-line'
import { currentBlock, KINDS, starterRoutine, type Routine, type RoutineBlock } from '@/lib/routine'

/**
 * The day's routine on the Overview, to tick off as it is done. It reads the routine the day
 * line has already fetched (`useRoutine`) and fetches nothing itself, so while that is not
 * read, or the read failed, there is nothing here. Ticking shows at once and is undone if
 * the server says no; whatever it answers is published, because that is the truth.
 * Nothing here reminds, schedules or does the work.
 */
export default function YourDay() {
  const routine = useRoutine()
  const now = useIstMinute()
  const lit = useDayLit()
  // The routine offered to someone with none. Its ids are made once, so "Use this" and "Edit first" start from the same blocks.
  const [starter] = useState(() => starterRoutine('both'))
  const [editing, setEditing] = useState<RoutineBlock[] | null>(null)
  const [tickFailed, setTickFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const section = useRef<HTMLElement>(null)
  const wasEditing = useRef(false)

  // The form takes the keyboard when it opens and gives it back to the section's first button when it closes.
  useEffect(() => {
    const open = editing !== null
    if (open !== wasEditing.current) {
      wasEditing.current = open
      const target = open ? section.current?.querySelector<HTMLElement>('.yd-edit input, .yd-edit select, .yd-edit button') : section.current?.querySelector<HTMLElement>('button')
      target?.focus()
    }
  }, [editing])

  if (!routine) return null

  const current = now === null ? null : currentBlock(routine.blocks, routine.done_today, now)

  async function tick(id: string, done: boolean) {
    setTickFailed(false)
    tickLocally(id, done)
    try {
      publishRoutine(await api.routine.tick(id, done), currentEmail())
    } catch {
      tickLocally(id, !done)
      setTickFailed(true)
    }
  }

  async function saveStarter() {
    setSaving(true)
    setSaveError('')
    try {
      publishRoutine(await api.routine.save(starter), currentEmail())
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Could not save the routine.')
    } finally {
      setSaving(false)
    }
  }

  const saved = (next: Routine) => {
    publishRoutine(next, currentEmail())
    setEditing(null)
  }

  return <section className="ov2-section" aria-labelledby="ov2-day" ref={section}>
    <div className="ov2-head">
      <h2 id="ov2-day">Your day</h2>
      {routine.has_routine && editing === null && <button type="button" className="text-button" aria-label="Edit your day" onClick={() => setEditing(routine.blocks)}>Edit</button>}
    </div>

    {editing !== null
      ? <RoutineEditor initial={editing} onSaved={saved} onCancel={() => setEditing(null)} />
      : routine.has_routine
        ? <>
          <ol className="yd-list">
            {routine.blocks.map(block => {
              const id = `routine:${block.id}`
              const done = routine.done_today.includes(block.id)
              const href = KINDS[block.kind]?.href ?? null
              return <li
                key={block.id}
                data-current={current?.id === block.id ? 'true' : undefined}
                data-done={done ? 'true' : undefined}
                data-lit={lit === id ? 'true' : undefined}
                aria-current={current?.id === block.id ? 'true' : undefined}
                onPointerEnter={() => setDayLit(id)}
                onPointerLeave={() => setDayLit(null)}
                onFocus={() => setDayLit(id)}
                onBlur={() => setDayLit(null)}
              >
                <label className="toggle-row">
                  <input type="checkbox" checked={done} onChange={e => void tick(block.id, e.target.checked)} />
                  <span className="yd-time">{block.at}</span>
                  <span className="yd-label">{block.label}</span>
                </label>
                {href && <Link className="text-button" href={href} aria-label={`Go: ${block.label}`}>Go</Link>}
              </li>
            })}
          </ol>
          {tickFailed && <p className="form-error" role="alert">Could not save that tick.</p>}
        </>
        : <>
          <p className="ov2-quiet">Give each part of the day a job.</p>
          <ol className="yd-list" data-plain="true">
            {starter.map(block => <li key={block.id}>
              <span className="yd-time">{block.at}</span>
              <span className="yd-label">{block.label}</span>
            </li>)}
          </ol>
          <div className="yd-actions">
            <button type="button" className="primary-button" disabled={saving} onClick={() => void saveStarter()}>{saving ? 'Saving…' : 'Use this'}</button>
            <button type="button" className="outline-button" disabled={saving} onClick={() => setEditing(starter)}>Edit first</button>
          </div>
          {saveError && <p className="form-error" role="alert">{saveError}</p>}
        </>}
  </section>
}
