'use client'

import { useEffect } from 'react'
import { track } from '@/lib/analytics'

/** Counts a public role view. Renders nothing; a no-op without an analytics key. */
export default function RoleViewBeacon({ state }: { state: string }) {
  useEffect(() => { track('public_role_view', { state }) }, [state])
  return null
}
