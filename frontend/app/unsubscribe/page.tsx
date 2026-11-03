'use client'

import Link from 'next/link'
import BrandLockup from '@/components/BrandLockup'
import { useEffect, useState } from 'react'
import MotionButton from '@/components/landing/MotionButton'
import PrismGround from '@/components/landing/PrismGround'
import { api } from '@/lib/api'

/** The link at the foot of a morning email: turns that email off, without signing in. */
function Unsubscribe() {
  const [state, setState] = useState<'working' | 'done' | 'failed'>('working')
  const [error, setError] = useState('')
  useEffect(() => {
    // Read after mount (not useSearchParams) to keep this page out of a Suspense boundary.
    const token = new URLSearchParams(window.location.search).get('token') || ''
    api.profile.unsubscribeDigest(token)
      .then(() => setState('done'))
      .catch(err => { setError(err instanceof Error ? err.message : 'Could not turn the email off.'); setState('failed') })
  }, [])
  if (state === 'working') return <div className="loading-state">Turning the morning email off…</div>
  if (state === 'failed') return <div className="auth-form"><p className="eyebrow">Morning email</p><h2>That did not work</h2><p className="form-error" role="alert">{error}</p><p className="auth-switch"><Link href="/settings">Open Settings</Link></p></div>
  return <div className="auth-form"><p className="eyebrow">Morning email</p><h2>You will not get it again</h2><p className="muted-copy">Turn it back on any time in Settings.</p><p className="auth-switch"><Link href="/settings">Open Settings</Link></p></div>
}

export default function UnsubscribePage() {
  return <main className="auth-page"><PrismGround across className="auth-prism" /><MotionButton className="auth-motion" /><section className="auth-aside"><Link className="brand" href="/"><BrandLockup /></Link><div><p className="eyebrow">Career intelligence, made personal</p><h1>Your inbox, your call<span>.</span></h1><p>TalentRadar mails only what you asked for.</p></div></section><section className="auth-form-wrap"><Link className="brand auth-mobile-brand" href="/"><BrandLockup /></Link><Unsubscribe /></section></main>
}
