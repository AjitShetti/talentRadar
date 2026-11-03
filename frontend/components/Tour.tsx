'use client'

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { usePathname } from 'next/navigation'
import { useRoutine } from '@/components/DayLine'
import { api, currentEmail } from '@/lib/api'
import { publishRoutine } from '@/lib/day-line'
import { placeCard, shouldAutoStart, spotlight, TOUR_EVENT, TOUR_STEPS, type Box, type TourTarget } from '@/lib/tour'
import { useDialog } from '@/lib/use-dialog'

/**
 * The first-run tour: five steps, each pointing at a part of the shell that is already on the
 * page (`data-tour` on the Job hunt and Prep buttons and on the day line). It opens by itself
 * once, for someone who finished onboarding and has not seen it, and again from the account
 * menu whenever they ask. Done and Skip both mean "seen". Rules and copy: lib/tour.ts.
 */
export default function Tour() {
  const routine = useRoutine()
  const path = usePathname()
  const [open, setOpen] = useState(false)
  // Once a page: closing it (or a failed save of "seen") must not open it again under the same visit.
  const autoStarted = useRef(false)

  useEffect(() => {
    if (autoStarted.current || !shouldAutoStart(routine, path)) return
    autoStarted.current = true
    setOpen(true)
  }, [routine, path])

  // A replay always starts at step one; while the tour is open a second request does nothing.
  useEffect(() => {
    const replay = () => setOpen(true)
    window.addEventListener(TOUR_EVENT, replay)
    return () => window.removeEventListener(TOUR_EVENT, replay)
  }, [])

  const close = useCallback(() => {
    setOpen(false)
    const owner = currentEmail()
    // A replay closes on a tour already seen: nothing to record, and the stored routine keeps its read time.
    if (!owner || routine?.tour_seen) return
    // Seen at once on the page, so nothing reopens it; the server's own answer settles it if it comes.
    if (routine) publishRoutine({ ...routine, tour_seen: true }, owner)
    api.routine.tourSeen()
      .then(reply => { if (currentEmail() === owner) publishRoutine(reply, owner) })
      .catch(() => {})
  }, [routine])

  return open ? <TourCard onClose={close} /> : null
}

/** What the tour needs of the page: the target's box (none when it is not there) and the viewport. */
type Frame = { target: Box | null; width: number; height: number }

function readFrame(target: TourTarget): Frame {
  const rect = document.querySelector<HTMLElement>(`[data-tour="${target}"]`)?.getBoundingClientRect()
  return {
    // A target that is not on this page (a bleed page has no day line), or is not drawn, is no target.
    target: rect && rect.width > 0 && rect.height > 0 ? { left: rect.left, top: rect.top, width: rect.width, height: rect.height } : null,
    width: document.documentElement.clientWidth,
    height: document.documentElement.clientHeight,
  }
}

const sameFrame = (a: Frame, b: Frame) => a.width === b.width && a.height === b.height && JSON.stringify(a.target) === JSON.stringify(b.target)

/** How long after a step, a resize or a scroll the target is followed frame by frame: the nav slides back in over 350 ms. */
const SETTLE_MS = 600

function TourCard({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(0)
  const { target, title, body } = TOUR_STEPS[step]
  const last = step === TOUR_STEPS.length - 1
  const cardRef = useRef<HTMLDivElement>(null)
  const onKeyDown = useDialog(cardRef, onClose, { modal: true, closeOnEscape: true })
  // Mounted only once it is open, in the browser, so the page can be read for the first paint.
  const [frame, setFrame] = useState(() => readFrame(TOUR_STEPS[0].target))
  const [size, setSize] = useState({ width: 360, height: 200 })

  // Follow the target: measured now, and again on every frame for a moment after a step, a resize or a scroll.
  useEffect(() => {
    const first = readFrame(target).target
    // The nav hides as the page scrolls down and the day line scrolls away: bring the page back to them.
    if (first && (first.top < 0 || first.top + first.height > document.documentElement.clientHeight)) window.scrollTo({ top: 0 })
    let raf = 0
    const settle = () => {
      cancelAnimationFrame(raf)
      const until = performance.now() + SETTLE_MS
      const tick = () => {
        const next = readFrame(target)
        setFrame(prev => (sameFrame(prev, next) ? prev : next))
        if (performance.now() < until) raf = requestAnimationFrame(tick)
      }
      tick()
    }
    settle()
    window.addEventListener('resize', settle)
    window.addEventListener('scroll', settle, { passive: true })
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', settle)
      window.removeEventListener('scroll', settle)
    }
  }, [target])

  // The card's own size, before it is painted: each step's words are a different length.
  useLayoutEffect(() => {
    const card = cardRef.current
    if (!card) return
    setSize(prev => (prev.width === card.offsetWidth && prev.height === card.offsetHeight ? prev : { width: card.offsetWidth, height: card.offsetHeight }))
  }, [step, frame.width])

  // Back is not there on the first step: when it was the button pressed, focus goes to Next, not to the page behind.
  useEffect(() => {
    const card = cardRef.current
    if (card && !card.contains(document.activeElement)) card.querySelector<HTMLElement>('[data-autofocus]')?.focus({ preventScroll: true })
  }, [step])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return
      if (e.key === 'ArrowRight') { e.preventDefault(); setStep(at => Math.min(at + 1, TOUR_STEPS.length - 1)) }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); setStep(at => Math.max(at - 1, 0)) }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  const place = placeCard(frame.target, size, { width: frame.width, height: frame.height })
  // With no target the cut-out closes to nothing at the middle and only the dim is left.
  const cut = spotlight(frame.target) ?? { left: frame.width / 2, top: frame.height / 2, width: 0, height: 0 }

  return <>
    {/* A press on the dim does nothing, and must not take focus out of the card either. */}
    <div className="tour-shield" aria-hidden="true" onMouseDown={e => e.preventDefault()} />
    <div className="tour-cut" data-none={frame.target ? undefined : 'true'} aria-hidden="true" style={cut} />
    <div
      ref={cardRef}
      className="tour-card"
      role="dialog"
      aria-modal="true"
      aria-labelledby="tour-title"
      tabIndex={-1}
      data-pinned={place.pinned ? 'true' : undefined}
      style={place.pinned ? undefined : { left: place.left, top: place.top }}
      onKeyDown={onKeyDown}
    >
      <div className="tour-text" aria-live="polite">
        <h2 id="tour-title">{title}</h2>
        <p>{body}</p>
      </div>
      <div className="tour-foot">
        <span className="tour-count">{step + 1} of {TOUR_STEPS.length}</span>
        <button type="button" className="text-button" onClick={onClose}>Skip</button>
        <div className="tour-nav">
          {step > 0 && <button type="button" className="text-button" onClick={() => setStep(step - 1)}>Back</button>}
          <button type="button" className="primary-button" data-autofocus onClick={() => (last ? onClose() : setStep(step + 1))}>{last ? 'Done' : 'Next'}</button>
        </div>
      </div>
    </div>
  </>
}
