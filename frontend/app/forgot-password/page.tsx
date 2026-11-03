'use client'

import Link from 'next/link'
import BrandLockup from '@/components/BrandLockup'
import { FormEvent, useState } from 'react'
import MotionButton from '@/components/landing/MotionButton'
import PrismGround from '@/components/landing/PrismGround'
import { api } from '@/lib/api'

/** Ask for a reset link. The reply is the same whether or not the address has an account. */
function ForgotForm() {
  const [email, setEmail] = useState(''); const [sent, setSent] = useState(''); const [error, setError] = useState(''); const [loading, setLoading] = useState(false)
  async function submit(e: FormEvent) { e.preventDefault(); setError(''); setLoading(true); try { setSent((await api.auth.forgotPassword(email)).message) } catch (err) { setError(err instanceof Error ? err.message : 'Could not send a reset link.') } finally { setLoading(false) } }
  if (sent) return <div className="auth-form"><p className="eyebrow">Check your mail</p><h2>Look for a reset link</h2><p className="success-message" role="status">{sent}</p><p className="auth-switch"><Link href="/login">Back to sign in</Link></p></div>
  return <form className="auth-form" onSubmit={submit}><p className="eyebrow">Forgot your password?</p><h2>Get a link to choose a new one</h2><label>Email<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com" /></label>{error && <p className="form-error" role="alert">{error}</p>}<button type="submit" className="primary-button auth-submit" disabled={loading}>{loading ? 'Sending…' : 'Send reset link'}</button><p className="auth-switch">Remembered it? <Link href="/login">Sign in</Link></p></form>
}

export default function ForgotPasswordPage() {
  return <main className="auth-page"><PrismGround across className="auth-prism" /><MotionButton className="auth-motion" /><section className="auth-aside"><Link className="brand" href="/"><BrandLockup /></Link><div><p className="eyebrow">Career intelligence, made personal</p><h1>Back in, in a minute<span>.</span></h1><p>We mail a link that works for 30 minutes, once.</p></div></section><section className="auth-form-wrap"><Link className="brand auth-mobile-brand" href="/"><BrandLockup /></Link><ForgotForm /></section></main>
}
