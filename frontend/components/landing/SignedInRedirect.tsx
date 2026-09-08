'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { signedIn } from '@/lib/api'

/**
 * Sends an already-signed-in visitor straight to their board.
 *
 * TalentRadar is a daily-use tool, so someone typing the bare domain almost
 * always means "open my workspace", not "read the marketing page". The check
 * runs after mount for the same reason RequireAuth's does — localStorage does
 * not exist during SSR — so the landing page renders normally for everyone
 * first and the redirect happens on the next frame. LandingNav's CTA swap
 * covers that frame, and stays correct if this redirect is ever removed.
 */
export default function SignedInRedirect() {
  const router = useRouter()
  useEffect(() => { if (signedIn()) router.replace('/dashboard') }, [router])
  return null
}
