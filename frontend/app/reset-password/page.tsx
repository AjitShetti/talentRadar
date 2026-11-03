'use client'

import Link from 'next/link'
import BrandLockup from '@/components/BrandLockup'
import { FormEvent, useEffect, useState } from 'react'
import MotionButton from '@/components/landing/MotionButton'
import PrismGround from '@/components/landing/PrismGround'
import { api } from '@/lib/api'

/** Choose a new password from the link in the reset mail. */
function ResetForm() {
  // Read after mount (not useSearchParams) to keep this page out of a Suspense boundary.
  const [token, setToken] = useState<string | null>(null)
  useEffect(() => { setToken(new URLSearchParams(window.location.search).get('token') || '') }, [])
  const [password, setPassword] = useState(''); const [confirm, setConfirm] = useState(''); const [done, setDone] = useState(false); const [error, setError] = useState(''); const [loading, setLoading] = useState(false)
  async function submit(e: FormEvent) { e.preventDefault(); if (password !== confirm) { setError('Passwords do not match.'); return } setError(''); setLoading(true); try { await api.auth.resetPassword(token || '', password); setDone(true) } catch (err) { setError(err instanceof Error ? err.message : 'Could not reset your password.') } finally { setLoading(false) } }
  if (token === null) return <div className="loading-state">Reading your link…</div>
  if (token === '') return <div className="auth-form"><p className="eyebrow">Reset your password</p><h2>This link is incomplete</h2><p className="muted-copy">Open the link from the mail again, or ask for a new one.</p><p className="auth-switch"><Link href="/forgot-password">Send a new link</Link></p></div>
  if (done) return <div className="auth-form"><p className="eyebrow">All set</p><h2>Your password is changed</h2><p className="auth-switch"><Link href="/login">Sign in</Link></p></div>
  return <form className="auth-form" onSubmit={submit}><p className="eyebrow">Reset your password</p><h2>Choose a new password</h2><label>New password<input type="password" autoComplete="new-password" minLength={8} required value={password} onChange={e => setPassword(e.target.value)} placeholder="At least 8 characters" /></label><label>Confirm password<input type="password" autoComplete="new-password" required value={confirm} onChange={e => setConfirm(e.target.value)} placeholder="Repeat your password" /></label>{error && <p className="form-error" role="alert">{error}</p>}<button type="submit" className="primary-button auth-submit" disabled={loading}>{loading ? 'Saving…' : 'Set new password'}</button><p className="auth-switch">Link expired? <Link href="/forgot-password">Send a new one</Link></p></form>
}

export default function ResetPasswordPage() {
  return <main className="auth-page"><PrismGround across className="auth-prism" /><MotionButton className="auth-motion" /><section className="auth-aside"><Link className="brand" href="/"><BrandLockup /></Link><div><p className="eyebrow">Career intelligence, made personal</p><h1>Back in, in a minute<span>.</span></h1><p>A reset link works for 30 minutes, once.</p></div></section><section className="auth-form-wrap"><Link className="brand auth-mobile-brand" href="/"><BrandLockup /></Link><ResetForm /></section></main>
}
