'use client'

import Link from 'next/link'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { BriefcaseBusiness, ChevronDown, Dumbbell, FileText, Handshake, LogOut, Map as MapIcon, MessageSquare, Search, Trophy, UserRound, type LucideIcon } from 'lucide-react'
import { api, currentEmail, signOut } from '@/lib/api'

type NavLink = { label: string; href: string; icon: LucideIcon; blurb: string }
type NavEntry = { label: string; short?: string; href: string; items?: undefined } | { label: string; short?: string; href?: undefined; items: NavLink[] }

/**
 * The bar holds four things. Everything that gets someone hired sits under
 * "Job hunt"; everything that gets them ready sits under "Prep". Profile and
 * Resume Studio are about the user, not the search, so they live in the
 * account menu.
 */
const NAV: NavEntry[] = [
  { label: 'Overview', href: '/dashboard' },
  {
    label: 'Job hunt', short: 'Jobs', items: [
      { label: 'Find roles', href: '/search', icon: Search, blurb: 'Search live openings across the boards' },
      { label: 'Applications', href: '/applications', icon: BriefcaseBusiness, blurb: 'Every role, from saved to offer' },
      { label: 'Outreach', href: '/outreach', icon: Handshake, blurb: 'Who to reach, and when to follow up' },
      { label: 'Companies', href: '/company-intel', icon: MapIcon, blurb: 'Bengaluru tech employers on a map' },
    ],
  },
  {
    label: 'Prep', items: [
      { label: 'Practice', href: '/practice', icon: Dumbbell, blurb: 'Daily question, weekly take-home, contest' },
      { label: 'Mock interviews', href: '/interview', icon: MessageSquare, blurb: 'Voice or typed, scored answer by answer' },
      { label: 'Leaderboard', href: '/practice/leaderboard', icon: Trophy, blurb: 'Who is building and solving the most' },
    ],
  },
  { label: 'Events', href: '/events' },
]

/** Routes that belong to a group without being one of its menu items. */
const ALIASES: [string, string][] = [['/roles', '/search']]

const under = (path: string, href: string) => path === href || path.startsWith(href + '/')

/** The menu link the path belongs to: the longest matching href wins, so /practice/leaderboard is not Practice. */
function activeHref(path: string): string | null {
  const alias = ALIASES.find(([prefix]) => under(path, prefix))
  if (alias) return alias[1]
  const hrefs = NAV.flatMap(entry => entry.items ? entry.items.map(item => item.href) : [entry.href])
  return hrefs.filter(href => under(path, href)).sort((a, b) => b.length - a.length)[0] ?? null
}

const entryHas = (entry: NavEntry, href: string | null) => href !== null && (entry.items ? entry.items.some(item => item.href === href) : entry.href === href)

/**
 * `narrow` puts the page on a single measured column instead of the full
 * 1260px board width. Form-shaped routes (profile, interview setup) have
 * content that tops out around 700px, so on the wide wrap their heading rule
 * ran the full width while the fields hugged the left edge — all the leftover
 * space piled up on one side. Narrowing the wrap itself keeps the heading and
 * the content on the same left and right edges, and centres the pair.
 */
export default function AppShell({ children, narrow = false, bleed = false }: { children: React.ReactNode; narrow?: boolean; bleed?: boolean }) {
  const path = usePathname()
  const current = activeHref(path)

  // currentEmail() reads localStorage, which is absent during server render.
  // Reading it inline made the server emit "Guest user" while the client's
  // first render emitted the real address, desyncing hydration. Resolve it
  // after mount so both passes start from the same markup.
  const [email, setEmail] = useState<string | null>(null)
  useEffect(() => { setEmail(currentEmail()) }, [])

  const [openMenu, setOpenMenu] = useState<string | null>(null)

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
        else if (delta > 4) { setNavHidden(true); setOpenMenu(null) }
        else if (delta < -4) setNavHidden(false)
        lastY = y
        ticking = false
      })
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // A route change closes whatever menu took the user there.
  useEffect(() => { setOpenMenu(null) }, [path])

  return <div className="app-shell">
    <header className={`topnav ${navHidden ? 'topnav-hidden' : ''}`}>
      <Link className="brand" href="/dashboard"><span className="brand-mark">R</span><span>Talent<span>Radar</span></span></Link>
      <NavBar current={current} openMenu={openMenu} setOpenMenu={setOpenMenu} />
      <div className="top-actions">
        {email
          ? <UserMenu email={email} open={openMenu === 'account'} setOpen={open => setOpenMenu(open ? 'account' : null)} />
          : <Link className="small-link" href="/login">Sign in</Link>}
      </div>
    </header>
    <main className="main-content"><div className={`page-wrap ${narrow ? 'page-wrap-narrow' : ''} ${bleed ? 'page-wrap-bleed' : ''}`}>{children}</div></main>
  </div>
}

/**
 * The centre pill. One indicator glides between items: it rests on the
 * current section and follows the pointer while it is over the bar.
 */
function NavBar({ current, openMenu, setOpenMenu }: { current: string | null; openMenu: string | null; setOpenMenu: (label: string | null) => void }) {
  const barRef = useRef<HTMLElement>(null)
  const itemRefs = useRef<(HTMLElement | null)[]>([])
  const gliderRef = useRef<HTMLSpanElement>(null)
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // A menu hover opened is pinned by a click, not closed by it: the pointer was already there to open it.
  const openedByHover = useRef(false)
  const [hot, setHot] = useState<number | null>(null)
  const restIndex = NAV.findIndex(entry => entryHas(entry, current))
  const target = hot ?? (restIndex >= 0 ? restIndex : null)

  const place = useCallback(() => {
    const glider = gliderRef.current, bar = barRef.current
    const el = target === null ? null : itemRefs.current[target]
    if (!glider || !bar) return
    if (!el) { glider.style.opacity = '0'; return }
    // Measured against the bar, not offsetLeft: a group's button sits inside its own positioned wrapper.
    const box = el.getBoundingClientRect()
    glider.style.width = `${box.width}px`
    glider.style.transform = `translateX(${box.left - bar.getBoundingClientRect().left}px)`
    glider.style.opacity = '1'
  }, [target])

  useLayoutEffect(() => { place() }, [place])
  useEffect(() => {
    window.addEventListener('resize', place)
    // Widths change once the web font arrives.
    document.fonts?.ready.then(place).catch(() => {})
    return () => window.removeEventListener('resize', place)
  }, [place])

  // Outside click and Escape close an open group menu.
  useEffect(() => {
    if (!openMenu || openMenu === 'account') return
    const onDown = (e: MouseEvent) => { if (!barRef.current?.contains(e.target as Node)) setOpenMenu(null) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpenMenu(null) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [openMenu, setOpenMenu])

  useEffect(() => () => { if (hoverTimer.current) clearTimeout(hoverTimer.current) }, [])

  // Hover opens a group on a fine pointer only, after a beat, so sweeping across the bar does not flash menus.
  const fine = () => typeof window !== 'undefined' && window.matchMedia('(hover: hover) and (pointer: fine)').matches
  const hoverOpen = (label: string | null) => {
    if (!fine()) return
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
    hoverTimer.current = setTimeout(() => { openedByHover.current = label !== null; setOpenMenu(label) }, label ? 90 : 160)
  }

  return <nav className="topnav-links" ref={barRef} aria-label="Main" onMouseLeave={() => { setHot(null); if (openMenu !== 'account') hoverOpen(null) }}>
    <span className="nav-glider" ref={gliderRef} aria-hidden="true" />
    {NAV.map((entry, i) => {
      const active = entryHas(entry, current)
      if (!entry.items) {
        return <Link
          key={entry.label}
          ref={el => { itemRefs.current[i] = el }}
          className={`nav-item ${active ? 'active' : ''}`}
          href={entry.href}
          aria-current={active ? 'page' : undefined}
          onMouseEnter={() => { setHot(i); hoverOpen(null) }}
        >{entry.label}</Link>
      }
      const open = openMenu === entry.label
      const menuId = `nav-menu-${i}`
      return <div className="nav-group" key={entry.label} onMouseEnter={() => { setHot(i); hoverOpen(entry.label) }}>
        <button
          ref={el => { itemRefs.current[i] = el }}
          type="button"
          className={`nav-item ${active ? 'active' : ''}`}
          aria-expanded={open}
          aria-haspopup="true"
          aria-controls={menuId}
          onClick={() => {
            if (hoverTimer.current) clearTimeout(hoverTimer.current)
            const close = open && !openedByHover.current
            openedByHover.current = false
            setOpenMenu(close ? null : entry.label)
          }}
          onKeyDown={e => {
            if (e.key === 'ArrowDown') {
              e.preventDefault(); setOpenMenu(entry.label)
              requestAnimationFrame(() => document.querySelector<HTMLElement>(`#${menuId} a`)?.focus())
            }
          }}
        >
          {entry.short ? <><span className="nav-label-long">{entry.label}</span><span className="nav-label-short">{entry.short}</span></> : entry.label}
          <ChevronDown className="nav-chev" size={13} />
        </button>
        <div className="nav-menu" id={menuId} data-open={open ? 'true' : undefined} onKeyDown={e => menuKeys(e, () => setOpenMenu(null))}>
          {entry.items.map(item => {
            const Icon = item.icon
            return <Link className="nav-menu-item" key={item.href} href={item.href} tabIndex={open ? 0 : -1} data-active={item.href === current ? 'true' : undefined} aria-current={item.href === current ? 'page' : undefined}>
              <span className="nav-menu-icon"><Icon size={17} strokeWidth={1.9} /></span>
              <span><strong>{item.label}</strong><small>{item.blurb}</small></span>
            </Link>
          })}
        </div>
      </div>
    })}
  </nav>
}

/** Up/Down move between a menu's links; Escape closes it. */
function menuKeys(e: React.KeyboardEvent<HTMLElement>, close: () => void) {
  const links = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('a, button'))
  const at = links.indexOf(document.activeElement as HTMLElement)
  if (e.key === 'ArrowDown') { e.preventDefault(); links[(at + 1) % links.length]?.focus() }
  else if (e.key === 'ArrowUp') { e.preventDefault(); links[(at - 1 + links.length) % links.length]?.focus() }
  else if (e.key === 'Escape') { close() }
}

const nameKey = (email: string) => `tr.name:${email}`

/** The account menu: who is signed in, their profile and resume, and sign out. */
function UserMenu({ email, open, setOpen }: { email: string; open: boolean; setOpen: (open: boolean) => void }) {
  const router = useRouter()
  const rootRef = useRef<HTMLDivElement>(null)
  const [name, setName] = useState<string | null>(null)

  // The name is one profile read, remembered per address so it is not refetched on every route.
  useEffect(() => {
    let cached: string | null = null
    try { cached = localStorage.getItem(nameKey(email)) } catch { /* storage blocked */ }
    if (cached) { setName(cached); return }
    api.profile.get().then(result => {
      const full = typeof result.profile?.full_name === 'string' ? result.profile.full_name.trim() : ''
      if (!full) return
      setName(full)
      try { localStorage.setItem(nameKey(email), full) } catch { /* storage blocked */ }
    }).catch(() => {})
  }, [email])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (!rootRef.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open, setOpen])

  const display = name || email.split('@')[0]
  const initials = (name ? name.split(/\s+/).map(part => part[0]).join('') : email.slice(0, 2)).slice(0, 2).toUpperCase()
  const go = (href: string) => { setOpen(false); router.push(href) }
  const leave = () => {
    setOpen(false)
    try { localStorage.removeItem(nameKey(email)) } catch { /* storage blocked */ }
    signOut(); router.push('/login')
  }

  return <div className="user-menu" ref={rootRef}>
    <button
      type="button"
      className="user-trigger"
      aria-label={`Account menu for ${display}`}
      aria-expanded={open}
      aria-haspopup="menu"
      aria-controls="user-panel"
      onClick={() => setOpen(!open)}
      onKeyDown={e => {
        if (e.key === 'ArrowDown') {
          e.preventDefault(); setOpen(true)
          requestAnimationFrame(() => document.querySelector<HTMLElement>('#user-panel button')?.focus())
        }
      }}
    >
      <span className="top-avatar" aria-hidden="true">{initials}</span>
      <span className="user-name">{display.split(' ')[0]}</span>
      <ChevronDown size={14} />
    </button>
    <div className="user-panel" id="user-panel" role="menu" aria-label="Account" data-open={open ? 'true' : undefined} onKeyDown={e => menuKeys(e, () => setOpen(false))}>
      <div className="user-head">
        <span className="top-avatar" aria-hidden="true">{initials}</span>
        <div><strong>{display}</strong><small>{email}</small></div>
      </div>
      <hr className="user-sep" />
      <button type="button" role="menuitem" tabIndex={open ? 0 : -1} className="user-item" onClick={() => go('/settings')}><span><UserRound size={15} />Profile &amp; goals</span></button>
      <button type="button" role="menuitem" tabIndex={open ? 0 : -1} className="user-item" onClick={() => go('/resume-studio')}><span><FileText size={15} />Resume studio</span></button>
      <hr className="user-sep" />
      <button type="button" role="menuitem" tabIndex={open ? 0 : -1} className="user-item" data-danger="true" onClick={leave}><span><LogOut size={15} />Sign out</span></button>
    </div>
  </div>
}
