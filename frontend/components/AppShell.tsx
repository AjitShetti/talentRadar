'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { BriefcaseBusiness, FileText, LayoutDashboard, LogOut, MessageSquare, Search, Settings2, Sparkles, UserRound } from 'lucide-react'
import { currentEmail, signOut } from '@/lib/api'

const nav = [
  ['Overview', '/dashboard', LayoutDashboard], ['Find roles', '/search', Search], ['Applications', '/applications', BriefcaseBusiness], ['Interview lab', '/interview', MessageSquare], ['Resume studio', '/resume-studio', FileText], ['Profile & goals', '/settings', UserRound],
] as const

export default function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname(); const router = useRouter()
  // currentEmail() reads localStorage, which is absent during server render.
  // Reading it inline made the server emit "Guest user" while the client's
  // first render emitted the real address, desyncing hydration. Resolve it
  // after mount so both passes start from the same markup.
  const [email, setEmail] = useState<string | null>(null)
  useEffect(() => { setEmail(currentEmail()) }, [])
  return <div className="app-shell"><aside className="sidebar"><Link className="brand" href="/dashboard"><span className="brand-mark">✳</span><span>Talent<span>Radar</span></span></Link><div className="workspace-label">WORKSPACE</div><nav>{nav.map(([label, href, Icon]) => <Link key={href} className={`nav-item ${path === href ? 'active' : ''}`} href={href}><Icon size={18}/><span>{label}</span></Link>)}</nav><div className="sidebar-bottom"><Link className="nav-item" href="/company-intel"><Sparkles size={18}/><span>Company intel</span></Link><Link className="nav-item" href="/agent"><Settings2 size={18}/><span>Career copilot</span></Link><div className="upgrade-card"><Sparkles size={16}/><strong>Your career, in focus</strong><p>Search smarter, practice deliberately, and keep every opportunity moving.</p></div><div className="user-row"><div className="avatar">{email?.slice(0, 2).toUpperCase() || 'TR'}</div><div><strong>{email || 'Guest user'}</strong><span>{email ? 'Signed in' : 'Sign in to sync'}</span></div>{email && <button className="logout-button" title="Sign out" onClick={() => { signOut(); router.push('/login') }}><LogOut size={15}/></button>}</div></div></aside><main className="main-content"><header className="topbar"><div className="crumb"><span>TalentRadar</span><span>·</span><strong>{nav.find(([, href]) => href === path)?.[0] || 'Workspace'}</strong></div><div className="top-actions">{email ? <div className="top-avatar">{email.slice(0, 2).toUpperCase()}</div> : <Link className="small-link" href="/login">Sign in</Link>}</div></header><div className="page-wrap">{children}</div></main></div>
}
