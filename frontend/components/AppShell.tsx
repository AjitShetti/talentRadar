'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { BriefcaseBusiness, FileText, LayoutDashboard, LogOut, MessageSquare, Search, Sparkles, UserRound } from 'lucide-react'
import { currentEmail, signOut } from '@/lib/api'

const nav = [
  ['Overview', '/dashboard', LayoutDashboard],
  ['Find roles', '/search', Search],
  ['Applications', '/applications', BriefcaseBusiness],
  ['Interview lab', '/interview', MessageSquare],
  ['Resume studio', '/resume-studio', FileText],
  ['Profile & goals', '/settings', UserRound],
  ['Company intel', '/company-intel', Sparkles],
] as const

/**
 * `narrow` puts the page on a single measured column instead of the full
 * 1260px board width. Form-shaped routes (profile, interview setup) have
 * content that tops out around 700px, so on the wide wrap their heading rule
 * ran the full width while the fields hugged the left edge — all the leftover
 * space piled up on one side. Narrowing the wrap itself keeps the heading and
 * the content on the same left and right edges, and centres the pair.
 */
export default function AppShell({ children, narrow = false }: { children: React.ReactNode; narrow?: boolean }) {
  const path = usePathname(); const router = useRouter()
  // currentEmail() reads localStorage, which is absent during server render.
  // Reading it inline made the server emit "Guest user" while the client's
  // first render emitted the real address, desyncing hydration. Resolve it
  // after mount so both passes start from the same markup.
  const [email, setEmail] = useState<string | null>(null)
  useEffect(() => { setEmail(currentEmail()) }, [])

  // Hide the top nav on scroll-down, bring it back on scroll-up (a few
  // pixels of slack so it doesn't flicker on tiny/trackpad jitter), and
  // always keep it visible near the top of the page.
  const [navHidden, setNavHidden] = useState(false)
  useEffect(() => {
    let lastY = window.scrollY
    let ticking = false
    const onScroll = () => {
      if (ticking) return
      ticking = true
      requestAnimationFrame(() => {
        const y = window.scrollY
        const delta = y - lastY
        if (y < 80) setNavHidden(false)
        else if (delta > 4) setNavHidden(true)
        else if (delta < -4) setNavHidden(false)
        lastY = y
        ticking = false
      })
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return <div className="app-shell">
    <header className={`topnav ${navHidden ? 'topnav-hidden' : ''}`}>
      <Link className="brand" href="/dashboard"><span className="brand-mark">R</span><span>Talent<span>Radar</span></span></Link>
      <nav className="topnav-links">{nav.map(([label, href, Icon]) => <Link key={href} className={`nav-item ${path === href ? 'active' : ''}`} href={href}><Icon size={14} /><span>{label}</span></Link>)}</nav>
      <div className="top-actions">
        {email
          ? <div className="user-chip"><div className="top-avatar">{email.slice(0, 2).toUpperCase()}</div><button className="logout-button" title="Sign out" onClick={() => { signOut(); router.push('/login') }}><LogOut size={15} /></button></div>
          : <Link className="small-link" href="/login">Sign in</Link>}
      </div>
    </header>
    <main className="main-content"><div className={`page-wrap ${narrow ? 'page-wrap-narrow' : ''}`}>{children}</div></main>
  </div>
}
