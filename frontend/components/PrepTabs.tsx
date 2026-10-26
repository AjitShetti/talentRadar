'use client'

import { usePathname } from 'next/navigation'
import Segmented from '@/components/Segmented'

const PREP = [
  { href: '/practice', label: 'Practice' },
  { href: '/interview', label: 'Mock interviews' },
  { href: '/practice/leaderboard', label: 'Leaderboard' },
]

/** The switcher across Prep's three pages. A challenge brief belongs to Practice. */
export default function PrepTabs() {
  const path = usePathname()
  const current = path.startsWith('/practice/leaderboard') ? '/practice/leaderboard' : path.startsWith('/interview') ? '/interview' : '/practice'
  return <div className="prep-tabs"><Segmented label="Prep" links={PREP} value={current} /></div>
}
