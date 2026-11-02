'use client'

import { useEffect, useRef } from 'react'
import MotionButton from './MotionButton'
import { onMotionChange, setMotionPaused } from './motion'

/**
 * What keeps the page alive between the two prisms: a few of the prism's ribs
 * falling behind the text, faster as the viewer scrolls, plus the page's pause
 * control and the `--p` scroll progress that [data-progress] elements read.
 *
 * Canvas 2D rather than a third WebGL context, since phones limit those. The
 * loop stops while the hero fills the window, the tab is hidden, or it is paused.
 */

const AZURE = '44,117,255'
const SALMON = '255,160,122'
const CONTENT_WIDTH = 1180
const RIB_GAP = 5
const RIB_WIDTH = 3
// A rib stays faint behind the text and is brighter in the empty margins.
const ALPHA_OVER_TEXT = .3
const ALPHA_IN_MARGIN = .8
// How far the ribs fade out next to each prism, so neither one cuts a rib off.
const FADE = 200
const ADDRESS_BAR = 120
// [data-progress] runs from its top at 80% of the window to its top at 35%.
const PROGRESS_FROM = .8
const PROGRESS_TO = .35

type Rib = { x: number; length: number; follow: number; drift: number; phase: number }

/** Seeded, so the same window always gets the same lines. */
function makeRibs(width: number, height: number): Rib[] {
  let seed = 7
  const random = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 }
  const ribs: Rib[] = []
  const clusters = Math.max(5, Math.round(width / 90))
  for (let c = 0; c < clusters; c++) {
    const x = random() * width
    const count = 1 + Math.floor(random() * 4)
    const length = height * (.25 + random() * .55)
    const follow = .3 + random() * .7
    const drift = 8 + random() * 14
    const phase = random() * 4000
    for (let i = 0; i < count; i++) {
      ribs.push({ x: x + i * RIB_GAP, length: length * (.7 + random() * .3), follow, drift, phase })
    }
  }
  return ribs
}

export default function LandingMotion() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const page = canvas?.parentElement
    if (!canvas || !page) return
    const ctx = canvas.getContext('2d')
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const hero = document.querySelector('.lp-hero-wrap')
    const closer = document.querySelector('.lp-closer')
    const tracked = Array.from(document.querySelectorAll<HTMLElement>('[data-progress]'))

    let ribs: Rib[] = []
    let ribsWidth = 0
    let ribsHeight = 0
    let dpr = 1
    let isPaused = reduced
    let elapsed = 0
    let last = 0
    let raf = 0
    let scrollAtPause = 0

    const draw = (scroll: number) => {
      if (!ctx) return
      const { innerWidth: width, innerHeight: height } = window
      const gutter = width > CONTENT_WIDTH ? (width - CONTENT_WIDTH) / 2 + 40 : 0
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, width, height)
      for (const rib of ribs) {
        const travel = ribsHeight + rib.length
        const top = (scroll * rib.follow + elapsed * rib.drift + rib.phase) % travel - rib.length
        const inMargin = gutter > 0 && (rib.x < gutter || rib.x > width - gutter)
        const alpha = inMargin ? ALPHA_IN_MARGIN : ALPHA_OVER_TEXT
        const gradient = ctx.createLinearGradient(0, top, 0, top + rib.length)
        gradient.addColorStop(0, `rgba(${AZURE},0)`)
        gradient.addColorStop(.45, `rgba(${AZURE},${alpha})`)
        gradient.addColorStop(.9, `rgba(${SALMON},${alpha})`)
        gradient.addColorStop(1, `rgba(${SALMON},0)`)
        ctx.fillStyle = gradient
        ctx.fillRect(rib.x, top, RIB_WIDTH, rib.length)
      }
      const fade = (from: number, to: number) => {
        const gradient = ctx.createLinearGradient(0, from, 0, to)
        gradient.addColorStop(0, 'rgba(0,0,0,1)')
        gradient.addColorStop(1, 'rgba(0,0,0,0)')
        ctx.fillStyle = gradient
        ctx.fillRect(0, Math.min(from, to), width, FADE)
      }
      ctx.globalCompositeOperation = 'destination-out'
      if (hero) { const edge = hero.getBoundingClientRect().bottom; fade(edge, edge + FADE) }
      if (closer) {
        // The ribs end at the closer: nothing runs on behind the footer below it.
        const edge = closer.getBoundingClientRect().top
        fade(edge, edge - FADE)
        ctx.clearRect(0, edge, width, Math.max(height - edge, 0))
      }
      ctx.globalCompositeOperation = 'source-over'
    }

    const updateProgress = () => {
      const height = window.innerHeight
      for (const element of tracked) {
        const top = element.getBoundingClientRect().top
        const progress = (height * PROGRESS_FROM - top) / (height * (PROGRESS_FROM - PROGRESS_TO))
        // Reduced motion gets the finished state rather than a scroll-driven one.
        element.style.setProperty('--p', reduced ? '1' : Math.min(Math.max(progress, 0), 1).toFixed(3))
      }
    }

    const frame = (now: number) => {
      raf = 0
      updateProgress()
      if (isPaused) { draw(scrollAtPause); last = 0; return }
      const pastHero = !hero || hero.getBoundingClientRect().bottom < window.innerHeight
      if (!pastHero || document.hidden) { last = 0; return }
      if (last) elapsed += Math.min((now - last) / 1000, .1)
      last = now
      draw(window.scrollY)
      raf = requestAnimationFrame(frame)
    }
    const wake = () => { if (!raf) raf = requestAnimationFrame(frame) }

    const fit = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 1.5)
      canvas.width = Math.round(window.innerWidth * dpr)
      canvas.height = Math.round(window.innerHeight * dpr)
      // A phone's address bar changes the height mid-scroll; the ribs must not jump for it.
      if (window.innerWidth !== ribsWidth || window.innerHeight > ribsHeight + ADDRESS_BAR) {
        ribsWidth = window.innerWidth
        ribsHeight = window.innerHeight
        ribs = makeRibs(ribsWidth, ribsHeight)
      }
      draw(isPaused ? scrollAtPause : window.scrollY)
      wake()
    }

    const offMotion = onMotionChange(next => {
      isPaused = next
      scrollAtPause = window.scrollY
      page.dataset.motion = next ? 'paused' : 'running'
      wake()
    })
    setMotionPaused(reduced)

    window.addEventListener('scroll', wake, { passive: true })
    window.addEventListener('resize', fit)
    document.addEventListener('visibilitychange', wake)
    fit()

    return () => {
      cancelAnimationFrame(raf)
      offMotion()
      window.removeEventListener('scroll', wake)
      window.removeEventListener('resize', fit)
      document.removeEventListener('visibilitychange', wake)
    }
  }, [])

  return <>
    <canvas ref={canvasRef} className="lp-ribs" aria-hidden="true" />
    <MotionButton className="lp-motion" />
  </>
}
