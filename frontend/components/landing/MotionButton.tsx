'use client'

import { useEffect, useState } from 'react'
import { Pause, Play } from 'lucide-react'
import { onMotionChange, setMotionPaused } from './motion'

/** The page's one pause control. A viewer who asked for reduced motion starts paused. */
export default function MotionButton({ className }: { className: string }) {
  const [paused, setPaused] = useState(false)

  useEffect(() => {
    const off = onMotionChange(setPaused)
    setMotionPaused(window.matchMedia('(prefers-reduced-motion: reduce)').matches)
    return off
  }, [])

  return (
    <button
      className={className}
      type="button"
      onClick={() => setMotionPaused(!paused)}
      aria-label={paused ? 'Play motion' : 'Pause motion'}
    >
      {paused ? <Play size={12} fill="currentColor" strokeWidth={0} /> : <Pause size={12} fill="currentColor" strokeWidth={0} />}
    </button>
  )
}
