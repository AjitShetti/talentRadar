'use client'

import { useEffect, useRef, useState } from 'react'

/**
 * Settles a block into place as it enters the viewport — a heavy fade-up
 * through a short blur, on a spring curve.
 *
 * Uses IntersectionObserver rather than a scroll listener: a scroll handler
 * fires on every frame and forces layout reads, which is what makes most
 * "animate on scroll" pages stutter on mid-range phones. The observer fires
 * once per element and then disconnects.
 *
 * If the API is missing (very old browsers, some crawlers), the element is
 * shown immediately rather than left invisible — the content must never be
 * hostage to the animation.
 */
export default function Reveal({
  children,
  delay = 0,
  className = '',
}: {
  children: React.ReactNode
  delay?: number
  className?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [shown, setShown] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (typeof IntersectionObserver === 'undefined') { setShown(true); return }
    const observer = new IntersectionObserver(
      entries => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          setShown(true)
          observer.disconnect()
        }
      },
      // Fire a little before the block reaches the fold so it has finished
      // settling by the time the reader's eye arrives.
      { rootMargin: '0px 0px -12% 0px', threshold: 0.08 },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  return (
    <div
      ref={ref}
      className={`lp-reveal ${shown ? 'is-in' : ''} ${className}`.trim()}
      style={{ '--d': `${delay}ms` } as React.CSSProperties}
    >
      {children}
    </div>
  )
}
