/**
 * The landing page's one pause switch. Every moving thing on the page listens to
 * it, so a viewer pauses the page once rather than chasing a control per section.
 */
type Listener = (paused: boolean) => void

let paused = false
const listeners = new Set<Listener>()

export const motionPaused = () => paused

export function setMotionPaused(next: boolean) {
  paused = next
  listeners.forEach(listener => listener(next))
}

export function onMotionChange(listener: Listener) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
