'use client'

import { usePathname } from 'next/navigation'
import Segmented from '@/components/Segmented'

const PREP = [
  { href: '/practice', label: 'Practice' },
  { href: '/interview', label: 'Mock interviews' },
]

/** The switcher across Prep's two pages. A contest or a design problem belongs to Practice. */
export default function PrepTabs() {
  const path = usePathname()
  const current = path.startsWith('/interview') ? '/interview' : '/practice'
  return <div className="prep-tabs"><Segmented label="Prep" links={PREP} value={current} /></div>
}
