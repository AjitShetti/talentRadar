'use client'

import Link from 'next/link'
import BrandLockup from '@/components/BrandLockup'
import { FormEvent, Suspense, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { safeNextPath } from '@/lib/safe-url'
import { ArrowRight, Sparkles } from 'lucide-react'
import { api } from '@/lib/api'

/** Someone who never finished onboarding is new to us: they get it once more, everyone else their Overview. */
async function landing(): Promise<string> {
  try { return (await api.profile.get()).onboarding_completed ? '/dashboard' : '/onboarding' } catch { return '/dashboard' }
}

function LoginForm() {
  const router = useRouter(); const params = useSearchParams(); const [email, setEmail] = useState(''); const [password, setPassword] = useState(''); const [error, setError] = useState(''); const [loading, setLoading] = useState(false)
  async function submit(e: FormEvent) { e.preventDefault(); setError(''); setLoading(true); try { await api.auth.login(email, password); router.push(safeNextPath(params.get('next')) || await landing()) } catch (err) { setError(err instanceof Error ? err.message : 'Unable to sign in.') } finally { setLoading(false) } }
  return <form className="auth-form" onSubmit={submit}><p className="eyebrow">Welcome back</p><h2>Sign in to your workspace</h2><label>Email<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com" /></label><label>Password<input type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} placeholder="Your password" /></label>{error && <p className="form-error" role="alert">{error}</p>}<button type="submit" className="primary-button auth-submit" disabled={loading}>{loading ? 'Signing in…' : <>Sign in <ArrowRight size={16}/></>}</button><p className="auth-switch">New to TalentRadar? <Link href="/signup">Create an account</Link></p></form>
}

export default function LoginPage() {
  return <main className="auth-page"><section className="auth-aside"><Link className="brand" href="/"><BrandLockup /></Link><div><p className="eyebrow">Career intelligence, made personal</p><h1>Make your next move with conviction<span>.</span></h1><p>One workspace for roles, applications, interview practice, and better resumes.</p></div><div className="auth-quote"><Sparkles size={18}/><span>Find the opportunities that fit—not merely the ones that appear first.</span></div></section><section className="auth-form-wrap"><Link className="brand auth-mobile-brand" href="/"><BrandLockup /></Link><Suspense fallback={<div className="loading-state">Preparing sign in…</div>}><LoginForm /></Suspense></section></main>
}
