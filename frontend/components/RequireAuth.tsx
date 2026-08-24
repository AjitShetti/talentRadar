'use client'

import { useEffect, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { signedIn } from '@/lib/api'

/**
 * Gate a page behind a signed-in session.
 *
 * The token lives in localStorage, which does not exist while the page is
 * server-rendered. Calling signedIn() during render therefore returned false
 * on the server and true in the browser, so the server sent the "redirecting"
 * markup while the client's first render produced the full page. React saw two
 * different trees and threw hydration errors (#418/#423/#425) on every guarded
 * route. Resolve auth after mount instead, and render the same placeholder on
 * both sides until then.
 */
export default function RequireAuth({ children }: { children: React.ReactNode }) {
  const router = useRouter(); const pathname = usePathname()
  const [status, setStatus] = useState<'pending' | 'in' | 'out'>('pending')

  useEffect(() => {
    if (signedIn()) { setStatus('in'); return }
    setStatus('out')
    router.replace(`/login?next=${encodeURIComponent(pathname)}`)
  }, [pathname, router])

  if (status === 'pending') return <div className="loading-state">Loading…</div>
  if (status === 'out') return <div className="loading-state">Taking you to sign in…</div>
  return <>{children}</>
}
