'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { ArrowUpRight } from 'lucide-react'
import { signedIn } from '@/lib/api'

const SECTIONS = [
  { href: '#how', label: 'How it works', index: '01' },
  { href: '#features', label: 'Features', index: '02' },
  { href: '#trust', label: 'How it stays honest', index: '03' },
  { href: '#faq', label: 'Questions', index: '04' },
]

/**
 * A detached island rather than an edge-to-edge bar: the landing page's chrome
 * is deliberately not the app's `.topnav` (a solid instrument strip), because
 * this surface is the outside of the product, not the inside of it.
 *
 * The signed-in CTA swap resolves after mount for the same reason RequireAuth
 * does it there — the token lives in localStorage, which does not exist during
 * SSR, so reading it during render would produce two different trees and
 * hydration errors. Both sides render the signed-out label until we know.
 */
export default function LandingNav() {
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const [hasSession, setHasSession] = useState(false)

  useEffect(() => { setHasSession(signedIn()) }, [])

  useEffect(() => {
    // The only scroll listener on the page, and it writes a boolean rather
    // than a style — passive, and it cannot force layout.
    const onScroll = () => setScrolled(window.scrollY > 24)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Lock the page behind the full-screen menu, and let Escape close it.
  useEffect(() => {
    if (!open) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => { document.body.style.overflow = previous; window.removeEventListener('keydown', onKey) }
  }, [open])

  const ctaHref = hasSession ? '/dashboard' : '/signup'
  const ctaLabel = hasSession ? 'Open workspace' : 'Get started'

  return (
    <>
      <header className={`lp-nav ${scrolled ? 'lp-nav-scrolled' : ''}`}>
        <nav className="lp-nav-pill" aria-label="Main">
          <Link className="lp-nav-brand" href="/">
            <span className="brand-mark" aria-hidden="true">R</span>
            <span>Talent<span>Radar</span></span>
          </Link>

          <div className="lp-nav-links">
            {SECTIONS.map(section => (
              <a key={section.href} className="lp-nav-link" href={section.href}>{section.label}</a>
            ))}
          </div>

          <Link className="lp-nav-cta" href={ctaHref}>
            {ctaLabel}
            <span className="lp-nav-cta-icon" aria-hidden="true"><ArrowUpRight size={13} strokeWidth={1.5} /></span>
          </Link>

          <button
            className={`lp-burger ${open ? 'lp-burger-open' : ''}`}
            onClick={() => setOpen(v => !v)}
            aria-label={open ? 'Close menu' : 'Open menu'}
            aria-expanded={open}
          >
            <i /><i />
          </button>
        </nav>
      </header>

      {/* Kept mounted so the overlay can transition, but taken out of the
          accessibility tree and the tab order while it is closed. */}
      <div className={`lp-menu ${open ? 'lp-menu-open' : ''}`} aria-hidden={!open}>
        {SECTIONS.map(section => (
          <a key={section.href} href={section.href} tabIndex={open ? 0 : -1} onClick={() => setOpen(false)}>
            <small>{section.index}</small>{section.label}
          </a>
        ))}
        <a href={ctaHref} tabIndex={open ? 0 : -1} onClick={() => setOpen(false)}>
          <small>05</small>{ctaLabel}
        </a>
        {!hasSession && (
          <a href="/login" tabIndex={open ? 0 : -1} onClick={() => setOpen(false)}>
            <small>06</small>Sign in
          </a>
        )}
      </div>
    </>
  )
}
