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

export default function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname(); const router = useRouter()
  // currentEmail() reads localStorage, which is absent during server render.
  // Reading it inline made the server emit "Guest user" while the client's
  // first render emitted the real address, desyncing hydration. Resolve it
  // after mount so both passes start from the same markup.
  const [email, setEmail] = useState<string | null>(null)
  useEffect(() => { setEmail(currentEmail()) }, [])
  return <div className="app-shell">
    <header className="topnav">
      <Link className="brand" href="/dashboard"><span className="brand-mark">✳</span><span>Talent<span>Radar</span></span></Link>
      <nav className="topnav-links">{nav.map(([label, href, Icon]) => <Link key={href} className={`nav-item ${path === href ? 'active' : ''}`} href={href}><Icon size={15} /><span>{label}</span></Link>)}</nav>
      <div className="top-actions">
        {email
          ? <div className="user-chip"><div className="top-avatar">{email.slice(0, 2).toUpperCase()}</div><button className="logout-button" title="Sign out" onClick={() => { signOut(); router.push('/login') }}><LogOut size={15} /></button></div>
          : <Link className="small-link" href="/login">Sign in</Link>}
      </div>
    </header>
    <main className="main-content"><div className="page-wrap">{children}</div></main>
  </div>
}
