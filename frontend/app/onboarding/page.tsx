'use client';

import { useState, useEffect, type FormEvent } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Loader2, Lock, User, Target, MapPin, DollarSign, Briefcase, CheckCircle2, ChevronRight } from 'lucide-react';
import { profileApi } from '@/lib/profile-api';
import type { ProfileResponse } from '@/lib/types';

const STEPS = ['BASICS', 'TARGET', 'EXPERIENCE', 'GOALS'] as const;

export default function OnboardingPage() {
  const { data: session, status } = useSession();
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [step, setStep] = useState(0);

  const [fullName, setFullName] = useState('');
  const [headline, setHeadline] = useState('');
  const [targetRoles, setTargetRoles] = useState('');
  const [targetLocations, setTargetLocations] = useState('');
  const [isRemote, setIsRemote] = useState(false);
  const [salaryMin, setSalaryMin] = useState('');
  const [salaryMax, setSalaryMax] = useState('');
  const [currency, setCurrency] = useState('INR');
  const [yearsExp, setYearsExp] = useState('');
  const [currentRole, setCurrentRole] = useState('');
  const [careerGoals, setCareerGoals] = useState('');

  useEffect(() => {
    if (status !== 'authenticated' || !session?.accessToken) return;
    (async () => {
      try {
        const res = await profileApi.get(session.accessToken as string);
        setProfile(res);
        if (res.profile) {
          setFullName(res.profile.full_name || '');
          setHeadline(res.profile.headline || '');
          setTargetRoles((res.profile.target_roles || []).join(', '));
          setTargetLocations((res.profile.target_locations || []).join(', '));
          setIsRemote(res.profile.is_remote_preferred);
          setSalaryMin(res.profile.target_salary_min?.toString() || '');
          setSalaryMax(res.profile.target_salary_max?.toString() || '');
          setCurrency(res.profile.salary_currency || 'INR');
          setYearsExp(res.profile.years_experience?.toString() || '');
          setCurrentRole(res.profile.current_role || '');
          setCareerGoals(res.profile.career_goals || '');
        }
      } catch {
        // no existing profile — start fresh
      } finally {
        setLoading(false);
      }
    })();
  }, [status, session]);

  if (status === 'unauthenticated') {
    return (
      <div style={{ maxWidth: 480, margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={40} color="var(--color-fg-muted)" style={{ marginBottom: '1.5rem' }} />
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '1.5rem', marginBottom: '1rem' }}>SIGN IN TO CONTINUE</h2>
        <p style={{ color: 'var(--color-fg-muted)', marginBottom: '2rem' }}>Complete your career profile to unlock personalized job matches.</p>
        <Link href="/login" className="btn btn-primary">Sign In</Link>
      </div>
    );
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!session?.accessToken) return;
    setSaving(true);
    setError(null);
    try {
      await profileApi.upsert(session.accessToken as string, {
        full_name: fullName || undefined,
        headline: headline || undefined,
        target_roles: targetRoles ? targetRoles.split(',').map(s => s.trim()).filter(Boolean) : undefined,
        target_locations: targetLocations ? targetLocations.split(',').map(s => s.trim()).filter(Boolean) : undefined,
        is_remote_preferred: isRemote,
        target_salary_min: salaryMin ? parseFloat(salaryMin) : undefined,
        target_salary_max: salaryMax ? parseFloat(salaryMax) : undefined,
        salary_currency: currency || undefined,
        years_experience: yearsExp ? parseFloat(yearsExp) : undefined,
        current_role: currentRole || undefined,
        career_goals: careerGoals || undefined,
      });
      setSuccess(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save profile');
    } finally {
      setSaving(false);
    }
  };

  if (success) {
    return (
      <div style={{ maxWidth: 560, margin: '6rem auto', textAlign: 'center' }}>
        <CheckCircle2 size={48} color="#22c55e" style={{ marginBottom: '1.5rem' }} />
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '1.75rem', marginBottom: '1rem' }}>PROFILE COMPLETE</h2>
        <p style={{ color: 'var(--color-fg-muted)', marginBottom: '2rem' }}>Your career profile is set up. Start discovering jobs tailored to you.</p>
        <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center' }}>
          <Link href="/search" className="btn btn-primary">Search Jobs</Link>
          <Link href="/dashboard" className="btn">Dashboard</Link>
        </div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 720, margin: '0 auto' }}>
      <div style={{ marginBottom: '3rem' }}>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.12em', color: 'var(--color-fg-muted)', marginBottom: '0.75rem' }}>
          {profile?.onboarding_completed ? 'EDIT PROFILE' : 'CAREER PROFILE SETUP'}
        </div>
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 'clamp(2rem, 5vw, 3rem)', fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.1 }}>
          TELL US ABOUT<span style={{ color: 'var(--color-accent)' }}>_</span>YOUR CAREER
        </h1>
      </div>

      {/* Step indicator */}
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '2.5rem' }}>
        {STEPS.map((label, i) => (
          <button
            key={label}
            onClick={() => setStep(i)}
            style={{
              flex: 1,
              padding: '0.75rem',
              fontFamily: 'var(--font-display)',
              fontSize: '0.7rem',
              fontWeight: 600,
              letterSpacing: '0.08em',
              background: i === step ? 'var(--color-accent)' : 'transparent',
              color: i === step ? '#fff' : 'var(--color-fg-muted)',
              border: `1px solid ${i === step ? 'var(--color-accent)' : 'var(--color-border)'}`,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '0.5rem',
            }}
          >
            {i < step && <CheckCircle2 size={14} />} {label}
          </button>
        ))}
      </div>

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', padding: '3rem 0' }}>
          <Loader2 size={24} className="animate-spin text-accent" /> LOADING...
        </div>
      ) : (
        <form onSubmit={handleSubmit}>
          {step === 0 && (
            <div className="panel" style={{ marginBottom: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
                <User size={20} className="text-accent" />
                <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1.1rem' }}>BASICS</h3>
              </div>
              <div style={{ display: 'grid', gap: '1.25rem' }}>
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>FULL NAME</span>
                  <input type="text" value={fullName} onChange={e => setFullName(e.target.value)} placeholder="Jane Smith" style={inputStyle} />
                </label>
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>HEADLINE</span>
                  <input type="text" value={headline} onChange={e => setHeadline(e.target.value)} placeholder="Senior Python Engineer | Backend & APIs" style={inputStyle} />
                </label>
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="panel" style={{ marginBottom: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
                <Target size={20} className="text-accent" />
                <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1.1rem' }}>TARGET ROLE</h3>
              </div>
              <div style={{ display: 'grid', gap: '1.25rem' }}>
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>DESIRED ROLES (comma-separated)</span>
                  <input type="text" value={targetRoles} onChange={e => setTargetRoles(e.target.value)} placeholder="Senior Python Engineer, Backend Lead" style={inputStyle} />
                </label>
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}><MapPin size={14} style={{ verticalAlign: 'middle', marginRight: 4 }} />PREFERRED LOCATIONS (comma-separated)</span>
                  <input type="text" value={targetLocations} onChange={e => setTargetLocations(e.target.value)} placeholder="Bangalore, Remote, Mumbai" style={inputStyle} />
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', cursor: 'pointer', fontFamily: 'var(--font-display)', fontSize: '0.85rem' }}>
                  <input type="checkbox" checked={isRemote} onChange={e => setIsRemote(e.target.checked)} style={{ width: 18, height: 18, accentColor: 'var(--color-accent)' }} />
                  Open to remote work
                </label>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="panel" style={{ marginBottom: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
                <DollarSign size={20} className="text-accent" />
                <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1.1rem' }}>COMPENSATION</h3>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: '1.25rem', alignItems: 'end' }}>
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>MIN SALARY</span>
                  <input type="number" value={salaryMin} onChange={e => setSalaryMin(e.target.value)} placeholder="2000000" style={inputStyle} />
                </label>
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>MAX SALARY</span>
                  <input type="number" value={salaryMax} onChange={e => setSalaryMax(e.target.value)} placeholder="4000000" style={inputStyle} />
                </label>
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>CURRENCY</span>
                  <select value={currency} onChange={e => setCurrency(e.target.value)} style={inputStyle}>
                    <option value="INR">INR</option>
                    <option value="USD">USD</option>
                    <option value="EUR">EUR</option>
                    <option value="GBP">GBP</option>
                  </select>
                </label>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem', marginTop: '1.25rem' }}>
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>YEARS OF EXPERIENCE</span>
                  <input type="number" step="0.5" value={yearsExp} onChange={e => setYearsExp(e.target.value)} placeholder="5" style={inputStyle} />
                </label>
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}><Briefcase size={14} style={{ verticalAlign: 'middle', marginRight: 4 }} />CURRENT ROLE</span>
                  <input type="text" value={currentRole} onChange={e => setCurrentRole(e.target.value)} placeholder="Software Engineer at Acme" style={inputStyle} />
                </label>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="panel" style={{ marginBottom: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
                <Target size={20} className="text-accent" />
                <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1.1rem' }}>CAREER GOALS</h3>
              </div>
              <label style={{ display: 'grid', gap: '0.4rem' }}>
                <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>WHAT DO YOU WANT TO ACHIEVE?</span>
                <textarea value={careerGoals} onChange={e => setCareerGoals(e.target.value)} rows={5} placeholder="Move into a staff engineer role, deepen system design expertise, transition to AI infrastructure..." style={{ ...inputStyle, resize: 'vertical', minHeight: 120 }} />
              </label>
            </div>
          )}

          {error && (
            <div style={{ padding: '1rem', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#fca5a5', marginBottom: '1.5rem', fontFamily: 'var(--font-display)', fontSize: '0.85rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem' }}>
              <span>{error}</span>
              {(error.toLowerCase().includes('expired') || error.toLowerCase().includes('unauthorized') || error.includes('401')) && (
                <Link href="/login" className="btn btn-primary" style={{ padding: '0.35rem 0.85rem', fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
                  Sign In Again
                </Link>
              )}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              {step > 0 && (
                <button type="button" onClick={() => setStep(s => s - 1)} className="btn">BACK</button>
              )}
            </div>
            <div style={{ display: 'flex', gap: '1rem' }}>
              {step < STEPS.length - 1 ? (
                <button type="button" onClick={() => setStep(s => s + 1)} className="btn btn-primary" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  NEXT <ChevronRight size={16} />
                </button>
              ) : (
                <button type="submit" disabled={saving} className="btn btn-primary" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  {saving ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
                  {saving ? 'SAVING...' : 'COMPLETE PROFILE'}
                </button>
              )}
            </div>
          </div>
        </form>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '0.75rem 1rem',
  background: 'rgba(0,0,0,0.3)',
  border: '1px solid var(--color-border)',
  color: 'var(--color-fg)',
  fontFamily: 'var(--font-body)',
  fontSize: '0.95rem',
  outline: 'none',
};
