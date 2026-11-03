'use client'

import { FormEvent, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { KINDS, LABEL_MAX, MAX_BLOCKS, newBlockId, nextBlockTime, routineProblem, type Routine, type RoutineBlock, type RoutineKind } from '@/lib/routine'

const KIND_KEYS = Object.keys(KINDS) as RoutineKind[]

/**
 * The routine's blocks as a form: a time, what the block is for, an optional name and a way
 * to remove it. It edits in place, as the interview time on /applications does. Saving sends
 * the whole routine; what the server answers is what is shown and what `onSaved` is given.
 * A refused save shows the server's own sentence, which `routineProblem` has usually said first.
 */
export default function RoutineEditor({ initial, onSaved, onCancel, saveLabel = 'Save routine' }: {
  initial: RoutineBlock[]
  onSaved: (routine: Routine) => void
  onCancel?: () => void
  saveLabel?: string
}) {
  const [blocks, setBlocks] = useState<RoutineBlock[]>(initial)
  // Counts every edit, so a reply to a save is not laid over what was typed while it was on its way.
  const edits = useRef(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // A block added here takes the keyboard; the ones that were already there do not.
  const [added, setAdded] = useState<string | null>(null)
  const addButton = useRef<HTMLButtonElement>(null)
  // Removing a row takes the keyboard's place with it; it goes to the button that adds one, once that is enabled again.
  const [removals, setRemovals] = useState(0)
  useEffect(() => { if (removals > 0) addButton.current?.focus() }, [removals])

  const update = (next: (current: RoutineBlock[]) => RoutineBlock[]) => { edits.current += 1; setBlocks(next) }
  const change = (id: string, patch: Partial<RoutineBlock>) => update(current => current.map(block => block.id === id ? { ...block, ...patch } : block))
  // A name left at its kind's default follows the kind; one the user wrote stays.
  const changeKind = (block: RoutineBlock, kind: RoutineKind) =>
    change(block.id, { kind, label: block.label === KINDS[block.kind]?.label ? '' : block.label })
  const add = () => {
    const block: RoutineBlock = { id: newBlockId(), at: nextBlockTime(blocks), kind: 'apply', label: '' }
    setAdded(block.id)
    update(current => [...current, block])
  }
  const remove = (id: string) => {
    update(current => current.filter(block => block.id !== id))
    setRemovals(count => count + 1)
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (busy) return
    const problem = routineProblem(blocks)
    if (problem) { setError(problem); return }
    setError('')
    setBusy(true)
    const sent = edits.current
    try {
      const saved = await api.routine.save(blocks)
      // The server's blocks are in clock order, each with its name. They replace the form's unless it has been edited since.
      if (edits.current === sent) setBlocks(saved.blocks)
      onSaved(saved)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the routine.')
    } finally {
      setBusy(false)
    }
  }

  return <form className="yd-edit" onSubmit={submit} aria-label="Daily routine">
    <div className="yd-fields">
      {blocks.length > 0 && <>
        <p className="ov2-quiet">Times are in IST. Leave a name empty to use the kind&apos;s own.</p>
        <ul className="yd-rows">
          {blocks.map((block, i) => <li className="yd-row" key={block.id}>
            <input
              className="yd-at"
              type="time"
              step={300}
              autoFocus={block.id === added}
              aria-label={`Time of block ${i + 1}`}
              value={block.at}
              onChange={e => change(block.id, { at: e.target.value })}
            />
            <select className="yd-kind" aria-label={`What block ${i + 1} is for`} value={block.kind} onChange={e => changeKind(block, e.target.value as RoutineKind)}>
              {KIND_KEYS.map(kind => <option key={kind} value={kind}>{KINDS[kind].name}</option>)}
            </select>
            <input
              className="yd-name"
              maxLength={LABEL_MAX}
              aria-label={`Name of block ${i + 1}`}
              placeholder={KINDS[block.kind]?.label || 'Name it'}
              value={block.label}
              onChange={e => change(block.id, { label: e.target.value })}
            />
            <button type="button" className="text-button yd-remove" aria-label={`Remove block ${i + 1}`} onClick={() => remove(block.id)}>Remove</button>
          </li>)}
        </ul>
      </>}
      <button type="button" className="outline-button yd-add" ref={addButton} disabled={blocks.length >= MAX_BLOCKS} onClick={add}>Add a block</button>
    </div>
    <div className="yd-actions">
      <button type="submit" className="primary-button" disabled={busy}>{busy ? 'Saving…' : saveLabel}</button>
      {onCancel && <button type="button" className="text-button" disabled={busy} onClick={onCancel}>Cancel</button>}
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
  </form>
}
