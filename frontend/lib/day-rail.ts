import { motionPaused } from '@/components/landing/motion'
import { DAY_MINUTES } from '@/lib/day-line'

/**
 * Draws the day line's ticks and its now line on one canvas, the way the landing
 * page draws its ribs: flat colour, nothing blurred, and a frame only while
 * something is moving. Four things move it, each for a reason:
 *   - the sweep, once, on arrival: the line runs from midnight to now and lands;
 *   - the minute: a small ripple runs along the past ticks as the time changes;
 *   - the pointer: nearby ticks rise, so the line can be read with the cursor;
 *   - a lit mark: the ticks around it rise when its row is pointed at.
 * With reduced motion, or motion paused on the landing or sign-in page, it draws
 * the settled line and stops.
 */
const SWEEP_MS = 950
const SWEEP_TAIL_MS = 520
const RIPPLE_MS = 900
/** The now line lands about here: the sweep's ease has done nearly all its travel. */
const LAND_AT = 0.62

const easeOutExpo = (t: number) => t >= 1 ? 1 : 1 - Math.pow(2, -10 * t)
const easeInOut = (t: number) => t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2
const bell = (distance: number, sigma: number) => Math.exp(-(distance * distance) / (2 * sigma * sigma))

export type Rail = {
  setNow: (minutes: number) => void
  setPointer: (x: number | null) => void
  setLit: (minutes: number | null) => void
  sweep: (onLand: () => void) => void
  destroy: () => void
}

export function createRail(canvas: HTMLCanvasElement): Rail {
  const ctx = canvas.getContext('2d')
  const reducedQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  const still = () => reducedQuery.matches || motionPaused()

  let width = 0, height = 0, ratio = 1
  let ticks: { minute: number; x: number; size: number; passedAt: number }[] = []
  let ink = '#fff', faint = 'rgba(255,255,255,.3)'
  let now = 0

  let sweepFrom = -1, onLanded: (() => void) | null = null, landTimer = 0
  let rippleFrom = -1
  let pointerX = 0, pointerGoal = 0, pointerPower = 0
  let litX = 0, litGoal = 0, litPower = 0
  let litMinute: number | null = null
  let frame = 0

  const xOf = (minute: number) => (minute / DAY_MINUTES) * width

  const readColours = () => {
    const style = getComputedStyle(canvas)
    ink = style.color
    faint = style.borderTopColor
  }

  const layout = () => {
    const box = canvas.parentElement?.getBoundingClientRect()
    if (!box || !box.width) return
    ratio = Math.min(window.devicePixelRatio || 1, 2)
    width = box.width
    height = box.height
    canvas.width = Math.round(width * ratio)
    canvas.height = Math.round(height * ratio)
    // A tick every ten minutes where there is room for it, every half hour on a phone.
    const step = width >= 1100 ? 10 : width >= 760 ? 15 : 30
    ticks = []
    for (let minute = 0; minute <= DAY_MINUTES; minute += step) {
      ticks.push({ minute, x: xOf(minute), size: minute % 360 === 0 ? 11 : minute % 60 === 0 ? 8 : 5, passedAt: -1 })
    }
    if (litMinute !== null) litX = xOf(litMinute)
  }

  const draw = (time: number) => {
    if (!ctx || !width) return false
    const sweeping = sweepFrom >= 0
    const progress = sweeping ? easeOutExpo((time - sweepFrom) / SWEEP_MS) : 1
    const head = now * progress
    const headX = xOf(head)
    const rippleAt = rippleFrom >= 0 ? (time - rippleFrom) / RIPPLE_MS : -1
    const rippleX = rippleAt >= 0 ? xOf(now) * easeInOut(Math.min(1, rippleAt)) : 0
    const rippleFade = rippleAt >= 0 ? Math.sin(Math.PI * Math.min(1, rippleAt)) : 0

    pointerPower += (pointerGoal - pointerPower) * .16
    litPower += (litGoal - litPower) * .14

    ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
    ctx.clearRect(0, 0, width, height)
    const floor = height - 18

    for (const tick of ticks) {
      const past = tick.minute <= head
      let rise = 0
      if (sweeping && past) {
        if (tick.passedAt < 0) tick.passedAt = time
        rise += 13 * Math.exp(-(time - tick.passedAt) / 170)
      }
      if (rippleFade > 0 && tick.minute <= now) rise += 7 * rippleFade * bell(tick.x - rippleX, 36)
      if (pointerPower > .002) rise += 15 * pointerPower * bell(tick.x - pointerX, 30)
      if (litPower > .002) rise += 13 * litPower * bell(tick.x - litX, 22)
      const size = tick.size + rise
      ctx.fillStyle = past ? ink : faint
      ctx.fillRect(Math.round(tick.x * ratio) / ratio, floor - size, 1, size)
    }

    ctx.fillStyle = ink
    ctx.fillRect(Math.round(headX * ratio) / ratio, 0, 1, floor)

    if (sweeping) {
      const elapsed = time - sweepFrom
      if (onLanded && elapsed >= SWEEP_MS * LAND_AT) { onLanded(); onLanded = null }
      if (elapsed >= SWEEP_MS + SWEEP_TAIL_MS) sweepFrom = -1
    }
    if (rippleAt >= 1) rippleFrom = -1

    return sweepFrom >= 0 || rippleFrom >= 0
      || Math.abs(pointerGoal - pointerPower) > .002 || Math.abs(litGoal - litPower) > .002
  }

  const loop = (time: number) => {
    frame = draw(time) ? requestAnimationFrame(loop) : 0
  }
  const kick = () => { if (!frame) frame = requestAnimationFrame(loop) }

  const redraw = () => {
    readColours()
    layout()
    kick()
  }

  const resize = new ResizeObserver(redraw)
  if (canvas.parentElement) resize.observe(canvas.parentElement)
  // The theme is an attribute on <html>, or the device's own scheme under "System".
  const theme = new MutationObserver(redraw)
  theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  const scheme = window.matchMedia('(prefers-color-scheme: dark)')
  scheme.addEventListener('change', redraw)

  readColours()
  layout()

  return {
    setNow(minutes) {
      const changed = minutes !== now
      const first = now === 0
      now = minutes
      if (changed && !first && !still() && !document.hidden && sweepFrom < 0) rippleFrom = performance.now()
      kick()
    },
    setPointer(x) {
      if (still()) return
      pointerGoal = x === null ? 0 : 1
      if (x !== null) pointerX = x
      kick()
    },
    setLit(minutes) {
      litMinute = minutes
      if (minutes !== null) litX = xOf(minutes)
      if (still()) return
      litGoal = minutes === null ? 0 : 1
      kick()
    },
    sweep(onLand) {
      if (still() || document.hidden) { onLand(); kick(); return }
      ticks.forEach(tick => { tick.passedAt = -1 })
      sweepFrom = performance.now()
      onLanded = onLand
      kick()
      // A tab that stops drawing mid-sweep must not leave the marks unshown.
      landTimer = window.setTimeout(() => { if (onLanded) { onLanded(); onLanded = null } }, SWEEP_MS + 100)
    },
    destroy() {
      if (frame) cancelAnimationFrame(frame)
      window.clearTimeout(landTimer)
      onLanded = null
      resize.disconnect()
      theme.disconnect()
      scheme.removeEventListener('change', redraw)
    },
  }
}
