'use client'

import { useEffect } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { signedIn } from '@/lib/api'

export default function RequireAuth({ children }: { children: React.ReactNode }) {
  const router = useRouter(); const pathname = usePathname()
  useEffect(() => { if (!signedIn()) router.replace(`/login?next=${encodeURIComponent(pathname)}`) }, [pathname, router])
  if (!signedIn()) return <div className="loading-state">Taking you to sign in…</div>
  return <>{children}</>
}
