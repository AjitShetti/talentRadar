'use client'

import Link from 'next/link'
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { usePathname } from 'next/navigation'
import { api, currentEmail, signedIn } from '@/lib/api'
import {
  arrivalPending, clockLabel, dayFor, dayIsFresh, dayLit, daySnapshot, DAY_MINUTES, istDay, istMinutes, labelFlips, labelledMarks,
  markArrived, mergeMarks, onDayLit, onDaySnapshot, onRoutine, publishBriefing, publishRoutine, routineFor, routineIsFresh, routineSnapshot,
  setDayLit, type DayMark,
} from '@/lib/day-line'
import { createRail, type Rail } from '@/lib/day-rail'
import { currentBlock, routineMarks, type Routine } from '@/lib/routine'
import { EXTERNAL_LINK_PROPS, externalHref } from '@/lib/safe-url'

/** The minute now in IST, updated on the minute and on coming back to the tab. Null until mounted. */
export function useIstMinute() {
  const [minute, setMinute] = useState<number | null>(null)
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>
    const tick = () => {
      const ms = Date.now()
      setMinute(istMinutes(ms))
      timer = setTimeout(tick, 60_000 - (ms % 60_000) + 20)
    }
    const onShow = () => { if (!document.hidden) { clearTimeout(timer); tick() } }
    tick()
    document.addEventListener('visibilitychange', onShow)
    return () => { clearTimeout(timer); document.removeEventListener('visibilitychange', onShow) }
  }, [])
  return minute
}

/** The user's routine for today, or null: not read yet, signed out, or the stored one is from another day. */
export function useRoutine(): Routine | null {
  useSyncExternalStore(onRoutine, routineSnapshot, routineSnapshot)
  return routineFor(currentEmail(), Date.now())
}

/**
 * What today holds: the briefing's marks and the routine's blocks together, in time order, so
 * the Overview counts a block exactly as it counts a dated card.
 */
export function useDay() {
  const day = useSyncExternalStore(onDaySnapshot, daySnapshot, daySnapshot)
  const shown = dayFor(day, currentEmail(), Date.now())
  const routine = useRoutine()
  // `shown` and `routine` keep their identity until the store changes, so these do too.
  const marks = useMemo(
    () => routine ? mergeMarks(shown.marks, routineMarks(routine.blocks, routine.done_today)) : shown.marks,
    [shown.marks, routine],
  )
  return useMemo(() => ({ ...shown, marks }), [shown, marks])
}

export function useDayLit() {
  return useSyncExternalStore(onDayLit, dayLit, () => null)
}

/**
 * One digit of the time. Keyed by its value, so only the digit that changed turns
 * over when the minute moves, not the whole clock.
 */
function Clock({ value }: { value: string }) {
  return <>{value.split('').map((ch, i) => <span className="day-digit" key={`${i}${ch}`}>{ch}</span>)}</>
}

const HOURS = [0, 6, 12, 18, 24]

/**
 * Today, as a line across the top of the signed-in product: past in ink, what is
 * left in faint ticks, and whatever the briefing dated marked where it falls.
 * The Overview reads the briefing itself and publishes it (no second request);
 * every other page reads it once and shares it for five minutes.
 */
export default function DayLine() {
  const path = usePathname()
  const onOverview = path === '/dashboard'
  const now = useIstMinute()
  const { marks } = useDay()
  const routine = useRoutine()
  const lit = useDayLit()

  const box = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const readout = useRef<HTMLSpanElement>(null)
  const rail = useRef<Rail | null>(null)
  const [width, setWidth] = useState(0)
  // Marks already known on mount are not news; marks that arrive afterwards drop in.
  const [freshMarks] = useState(() => daySnapshot().marks.length === 0 && (routineSnapshot().routine?.blocks.length ?? 0) === 0)
  const [playing] = useState(() => onOverview && arrivalPending())
  const [landed, setLanded] = useState(!playing)

  const day = now === null ? null : istDay(Date.now())
  const firstDay = useRef<number | null>(null)
  useEffect(() => {
    if (day === null || !signedIn()) return
    if (firstDay.current === null) firstDay.current = day
    // The Overview reads the briefing itself on load; left open past midnight, that falls to the line.
    if (onOverview && day === firstDay.current) return
    const owner = currentEmail()
    if (dayIsFresh(owner)) return
    let live = true
    api.agent.briefing().then(briefing => { if (live) publishBriefing(briefing, owner) }).catch(() => {})
    return () => { live = false }
  }, [onOverview, day])

  // The routine is its own read from its own route. Every page makes it (the Overview too) unless a recent one is
  // stored, and a page left open past midnight makes it again. A read that fails shows nothing and says nothing.
  useEffect(() => {
    if (day === null || !signedIn()) return
    const owner = currentEmail()
    if (routineIsFresh(owner)) return
    let live = true
    api.routine.get().then(routine => { if (live) publishRoutine(routine, owner) }).catch(() => {})
    return () => { live = false }
  }, [day])

  useEffect(() => {
    if (!canvas.current || !box.current) return
    const engine = createRail(canvas.current)
    rail.current = engine
    const measure = new ResizeObserver(entries => setWidth(entries[0].contentRect.width))
    measure.observe(box.current)
    // A click on a mark leaves the page with the pointer still on it, and no leave event follows.
    return () => { measure.disconnect(); engine.destroy(); rail.current = null; setDayLit(null) }
  }, [])

  // The first minute reaches the engine before the sweep starts, so it runs to the real now.
  const started = useRef(false)
  useEffect(() => {
    if (now === null || !rail.current) return
    rail.current.setNow(now)
    if (playing && !started.current) {
      started.current = true
      markArrived()
      rail.current.sweep(() => setLanded(true))
    }
  }, [now, playing])

  useEffect(() => {
    rail.current?.setLit(marks.find(mark => mark.id === lit)?.at ?? null)
  }, [lit, marks])

  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType !== 'mouse' || !box.current || !readout.current) return
    const bounds = box.current.getBoundingClientRect()
    const x = Math.max(0, Math.min(bounds.width, e.clientX - bounds.left))
    rail.current?.setPointer(x)
    readout.current.style.left = `${x}px`
    readout.current.style.opacity = '1'
    readout.current.textContent = clockLabel(Math.round(((x / bounds.width) * DAY_MINUTES) / 5) * 5)
  }
  const onLeave = () => {
    rail.current?.setPointer(null)
    if (readout.current) readout.current.style.opacity = '0'
  }

  const labelled = labelledMarks(marks, width)
  // The block to do now stays in the phone's list while it is under way, as on the Overview's "Your day".
  const current = routine && now !== null ? currentBlock(routine.blocks, routine.done_today, now) : null
  const currentMark = current ? `routine:${current.id}` : null
  const pct = (minute: number) => `${(minute / DAY_MINUTES) * 100}%`
  const animateMarks = playing || freshMarks

  return <section className="day-line" data-tour="day" aria-label={now === null ? 'Today, in IST' : `Today, in IST. It is ${clockLabel(now)}.`}>
    <div className="day-box" ref={box} onPointerMove={onMove} onPointerLeave={onLeave}>
      <canvas className="day-canvas" ref={canvas} aria-hidden="true" />
      {now !== null && <>
        {HOURS.filter(hour => Math.abs(hour * 60 - now) > 45).map(hour =>
          <span className="day-hour" key={hour} style={{ left: pct(hour * 60) }} aria-hidden="true">{clockLabel(hour === 24 ? DAY_MINUTES - 1 : hour * 60).replace('23:59', '24:00')}</span>)}
        {landed && <span className="day-now" style={{ left: pct(now) }} aria-hidden="true"><Clock value={clockLabel(now)} /></span>}
        <span className="day-read" ref={readout} aria-hidden="true" />
        {landed && marks.map((mark, i) => <MarkOnLine
          key={mark.id}
          mark={mark}
          past={mark.at <= now}
          labelled={labelled.has(mark.id)}
          flip={labelFlips(mark, width)}
          lit={lit === mark.id}
          left={pct(mark.at)}
          order={animateMarks ? i : null}
        />)}
      </>}
    </div>
    {now !== null && marks.length > 0 && <ul className="day-list">
      {marks.map(mark => <li
        key={mark.id}
        data-past={mark.at <= now ? 'true' : undefined}
        data-routine={mark.routine ? 'true' : undefined}
        data-done={mark.done ? 'true' : undefined}
        data-current={mark.id === currentMark ? 'true' : undefined}
      >
        <MarkLink href={mark.href} aria-label={mark.done ? `${mark.time} ${mark.label}, done` : undefined}>{mark.time} {mark.label}</MarkLink>
      </li>)}
    </ul>}
  </section>
}

/** A mark links where its card links: an absolute http(s) URL (the contest) opens in a new tab, an in-app path stays a Link. */
function MarkLink({ href, ...rest }: Omit<React.ComponentProps<'a'>, 'href'> & { href: string }) {
  const external = externalHref(href)
  return external ? <a href={external} {...EXTERNAL_LINK_PROPS} {...rest} /> : <Link href={href} {...rest} />
}

function MarkOnLine({ mark, past, labelled, flip, lit, left, order }: { mark: DayMark; past: boolean; labelled: boolean; flip: boolean; lit: boolean; left: string; order: number | null }) {
  return <MarkLink
    className="day-mark"
    href={mark.href}
    style={{ left, '--i': order ?? 0 } as React.CSSProperties}
    aria-label={`${mark.label} at ${mark.time}${mark.done ? ', done' : ''}`}
    data-past={past ? 'true' : undefined}
    data-routine={mark.routine ? 'true' : undefined}
    data-done={mark.done ? 'true' : undefined}
    data-end={flip ? 'true' : undefined}
    data-lit={lit ? 'true' : undefined}
    data-drop={order !== null ? 'true' : undefined}
    onPointerEnter={() => setDayLit(mark.id)}
    onPointerLeave={() => setDayLit(null)}
    onFocus={() => setDayLit(mark.id)}
    onBlur={() => setDayLit(null)}
  >
    <i aria-hidden="true" />
    <span data-quiet={labelled ? undefined : 'true'}>{mark.time} {mark.label}</span>
  </MarkLink>
}
