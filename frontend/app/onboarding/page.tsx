'use client';

import { useState, useEffect, type FormEvent } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Lock, WarningCircle } from '@phosphor-icons/react';
import { profileApi } from '@/lib/profile-api';
import type { ProfileResponse } from '@/lib/types';

const STEPS = [
  { label: 'Basics', id: 'basics' },
  { label: 'Target role', id: 'target' },
  { label: 'Experience', id: 'experience' },
  { label: 'Goals', id: 'goals' },
] as const;

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
      } catch { /* fresh profile */ } finally { setLoading(false); }
    })();
  }, [status, session]);

  if (status === 'unauthenticated') {
    return (
      <div style={{ maxWidth: '480px', margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={36} style={{ color: 'var(--text-subtle)', marginBottom: '1.25rem' }} />
        <h2 style={{ fontSize: '1.375rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.75rem' }}>Sign in to set up your profile</h2>
        <Link href="/login" style={{ display: 'inline-block', padding: '0.625rem 1.25rem', background: 'var(--accent)', color: '#fff', borderRadius: 'var(--radius-sm)', fontSize: '0.9375rem', fontWeight: 500, textDecoration: 'none' }}>Sign in</Link>
      </div>
    );
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!session?.accessToken) return;
    setSaving(true); setError(null);
    try {
      await profileApi.upsert(session.accessToken as string, {
        full_name: fullName || undefined,
        headline: headline || undefined,
        target_roles: targetRoles ? targetRoles.split(',').map((s) => s.trim()).filter(Boolean) : undefined,
        target_locations: targetLocations ? targetLocations.split(',').map((s) => s.trim()).filter(Boolean) : undefined,
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
    } finally { setSaving(false); }
  };

  if (success) {
    return (
      <div style={{ maxWidth: '480px', margin: '6rem auto', textAlign: 'center' }}>
        <div style={{ width: '48px', height: '48px', borderRadius: '50%', background: 'var(--success-bg)', border: '1px solid rgba(22,163,74,0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 1.25rem' }}>
          <span style={{ fontSize: '1.5rem' }}>&#10003;</span>
        </div>
        <h2 style={{ fontSize: '1.375rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.75rem' }}>Profile saved</h2>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.75rem' }}>Your career profile is ready. Start discovering jobs tailored to you.</p>
        <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center' }}>
          <Link href="/search" style={{ display: 'inline-block', padding: '0.625rem 1.25rem', background: 'var(--accent)', color: '#fff', borderRadius: 'var(--radius-sm)', fontSize: '0.9375rem', fontWeight: 500, textDecoration: 'none' }}>Search jobs</Link>
          <Link href="/dashboard" style={{ display: 'inline-block', padding: '0.625rem 1.25rem', background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)', borderRadius: 'var(--radius-sm)', fontSize: '0.9375rem', fontWeight: 500, textDecoration: 'none' }}>Dashboard</Link>
        </div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: '680px', margin: '0 auto', paddingTop: '2.5rem' }}>
      <div style={{ marginBottom: '2.5rem' }}>
        <h1 style={{ fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--text)', marginBottom: '0.35rem' }}>
          {profile?.onboarding_completed ? 'Edit your profile' : 'Set up your profile'}
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9375rem' }}>Tell us about your career so we can find better matches.</p>
      </div>

      {/* Step indicator */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0', marginBottom: '2rem' }}>
        {STEPS.map((s, i) => (
          <button
            key={s.id}
            onClick={() => setStep(i)}
            style={{
              flex: 1, padding: '0.625rem 0.5rem', background: 'transparent', border: 'none',
              borderBottom: `2px solid ${i === step ? 'var(--accent)' : 'var(--border)'}`,
              cursor: 'pointer', fontSize: '0.875rem',
              fontWeight: i === step ? 500 : 400,
              color: i === step ? 'var(--text)' : i < step ? 'var(--accent)' : 'var(--text-muted)',
              transition: 'border-color 150ms, color 150ms',
              textAlign: 'center',
            }}
          >
            {i < step ? <span style={{ marginRight: '0.3rem' }}>&#10003;</span> : null}
            {s.label}
          </button>
        ))}
      </div>

      {loading ? (
        <p style={{ color: 'var(--text-subtle)', fontSize: '0.9375rem' }}>Loading...</p>
      ) : (
        <form onSubmit={handleSubmit}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
            {step === 0 && (
              <>
                <div>
                  <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Full name</label>
                  <input type="text" value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Priya Sharma" />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Professional headline</label>
                  <input type="text" value={headline} onChange={(e) => setHeadline(e.target.value)} placeholder="Senior Python Engineer, Backend and APIs" />
                </div>
              </>
            )}

            {step === 1 && (
              <>
                <div>
                  <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Target roles <span style={{ fontWeight: 400, color: 'var(--text-muted)', fontSize: '0.8125rem' }}>(comma-separated)</span></label>
                  <input type="text" value={targetRoles} onChange={(e) => setTargetRoles(e.target.value)} placeholder="Senior Python Engineer, Backend Lead" />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Preferred locations <span style={{ fontWeight: 400, color: 'var(--text-muted)', fontSize: '0.8125rem' }}>(comma-separated)</span></label>
                  <input type="text" value={targetLocations} onChange={(e) => setTargetLocations(e.target.value)} placeholder="Bangalore, Remote, Mumbai" />
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', cursor: 'pointer', fontSize: '0.9375rem', color: 'var(--text)' }}>
                  <input type="checkbox" checked={isRemote} onChange={(e) => setIsRemote(e.target.checked)} style={{ width: '16px', height: '16px', accentColor: 'var(--accent)', flexShrink: 0 }} />
                  Open to fully remote roles
                </label>
              </>
            )}

            {step === 2 && (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr auto', gap: '1rem', alignItems: 'end' }}>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Min salary</label>
                    <input type="number" value={salaryMin} onChange={(e) => setSalaryMin(e.target.value)} placeholder="2000000" />
                  </div>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Max salary</label>
                    <input type="number" value={salaryMax} onChange={(e) => setSalaryMax(e.target.value)} placeholder="4000000" />
                  </div>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Currency</label>
                    <select value={currency} onChange={(e) => setCurrency(e.target.value)} style={{ width: 'auto' }}>
                      <option value="INR">INR</option>
                      <option value="USD">USD</option>
                      <option value="EUR">EUR</option>
                      <option value="GBP">GBP</option>
                    </select>
                  </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Years of experience</label>
                    <input type="number" step="0.5" value={yearsExp} onChange={(e) => setYearsExp(e.target.value)} placeholder="5" />
                  </div>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Current role</label>
                    <input type="text" value={currentRole} onChange={(e) => setCurrentRole(e.target.value)} placeholder="Software Engineer at TechCorp" />
                  </div>
                </div>
              </>
            )}

            {step === 3 && (
              <div>
                <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Career goals</label>
                <textarea
                  value={careerGoals}
                  onChange={(e) => setCareerGoals(e.target.value)}
                  rows={6}
                  placeholder="What do you want to achieve in the next 1-2 years? e.g. become a staff engineer, move into AI infrastructure, work at a Series B startup..."
                  style={{ resize: 'vertical' }}
                />
              </div>
            )}
          </div>

          {error && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '1.25rem', padding: '0.75rem 1rem', background: 'var(--error-bg)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 'var(--radius-sm)', color: 'var(--error)', fontSize: '0.875rem' }}>
              <WarningCircle size={16} style={{ flexShrink: 0 }} />
              {error}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '1.75rem' }}>
            <div>
              {step > 0 && (
                <button type="button" onClick={() => setStep((s) => s - 1)} style={{ padding: '0.5rem 1rem', background: 'transparent', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', fontSize: '0.875rem', color: 'var(--text)', cursor: 'pointer' }}>
                  Back
                </button>
              )}
            </div>
            <div>
              {step < STEPS.length - 1 ? (
                <button type="button" onClick={() => setStep((s) => s + 1)} style={{ padding: '0.5rem 1.25rem', background: 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '0.875rem', fontWeight: 500, cursor: 'pointer' }}>
                  Next
                </button>
              ) : (
                <button type="submit" disabled={saving} style={{ padding: '0.5rem 1.25rem', background: saving ? 'var(--border-hover)' : 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '0.875rem', fontWeight: 500, cursor: saving ? 'not-allowed' : 'pointer' }}>
                  {saving ? 'Saving...' : 'Save profile'}
                </button>
              )}
            </div>
          </div>
        </form>
      )}
    </div>
  );
}
