'use client'

import Link from 'next/link'
import { FormEvent, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowRight } from 'lucide-react'
import { api } from '@/lib/api'

export default function SignupPage() {
  const router = useRouter(); const [email, setEmail] = useState(''); const [password, setPassword] = useState(''); const [confirm, setConfirm] = useState(''); const [error, setError] = useState(''); const [loading, setLoading] = useState(false)
  async function submit(e: FormEvent) { e.preventDefault(); if (password !== confirm) { setError('Passwords do not match.'); return } setError(''); setLoading(true); try { await api.auth.signup(email, password); await api.auth.login(email, password); router.push('/onboarding') } catch (err) { setError(err instanceof Error ? err.message : 'Unable to create account.') } finally { setLoading(false) } }
  return <main className="auth-page"><section className="auth-aside"><Link className="brand" href="/"><span className="brand-mark">✳</span><span>Talent<span>Radar</span></span></Link><div><p className="eyebrow">YOUR JOB SEARCH OPERATING SYSTEM</p><h1>Build a career story that travels well.</h1><p>Start with your profile, then let TalentRadar connect the right roles, practice, and preparation.</p></div></section><section className="auth-form-wrap"><form className="auth-form" onSubmit={submit}><p className="eyebrow">CREATE YOUR ACCOUNT</p><h2>Start your career workspace</h2><label>Email<input type="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com" /></label><label>Password<input type="password" minLength={8} required value={password} onChange={e => setPassword(e.target.value)} placeholder="At least 8 characters" /></label><label>Confirm password<input type="password" required value={confirm} onChange={e => setConfirm(e.target.value)} placeholder="Repeat your password" /></label>{error && <p className="form-error">{error}</p>}<button className="primary-button auth-submit" disabled={loading}>{loading ? 'Creating account…' : <>Create account <ArrowRight size={16}/></>}</button><p className="auth-switch">Already have an account? <Link href="/login">Sign in</Link></p></form></section></main>
}
