'use client';

import { useState, useEffect, Suspense, type FormEvent } from 'react';
import { useSession } from 'next-auth/react';
import { useSearchParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { Loader2, Lock, FileText, Target, BookOpen, Mail, Sparkles, ChevronRight, AlertCircle, ArrowRight } from 'lucide-react';
import { resumesApi } from '@/lib/resumes-api';
import type { ResumeAnalyzeResponse, ResumeGapsResponse } from '@/lib/types';
import MatchAnalyzer from '@/components/MatchAnalyzer';
import { useAuthModal } from '@/components/AuthModalProvider';

type Tab = 'match' | 'analyze' | 'tailor' | 'cover-letter' | 'gaps';
const TABS: { key: Tab; label: string; icon: typeof FileText }[] = [
  { key: 'match', label: 'Match Engine', icon: Target },
  { key: 'analyze', label: 'ATS Analysis', icon: FileText },
  { key: 'tailor', label: 'Tailor Resume', icon: Sparkles },
  { key: 'cover-letter', label: 'Cover Letter', icon: Mail },
  { key: 'gaps', label: 'Skill Gaps', icon: BookOpen },
];

function ResumeStudioContent() {
  const { data: session, status } = useSession();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { openLogin } = useAuthModal();

  const initialTab = (searchParams.get('tab') as Tab) || 'match';
  const [tab, setTab] = useState<Tab>(
    ['match', 'analyze', 'tailor', 'cover-letter', 'gaps'].includes(initialTab) ? initialTab : 'match'
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [resumeText, setResumeText] = useState('');
  const [jobDescription, setJobDescription] = useState('');
  const [jobTitle, setJobTitle] = useState('');
  const [company, setCompany] = useState('');
  const [tone, setTone] = useState('professional');

  const [analysis, setAnalysis] = useState<ResumeAnalyzeResponse | null>(null);
  const [tailoredLatex, setTailoredLatex] = useState('');
  const [coverLetter, setCoverLetter] = useState('');
  const [gaps, setGaps] = useState<ResumeGapsResponse | null>(null);

  useEffect(() => {
    const urlTab = searchParams.get('tab') as Tab;
    if (urlTab && ['match', 'analyze', 'tailor', 'cover-letter', 'gaps'].includes(urlTab)) {
      setTab(urlTab);
    }
  }, [searchParams]);

  const switchTab = (newTab: Tab) => {
    setTab(newTab);
    setError(null);
    router.replace(`/resume-studio?tab=${newTab}`, { scroll: false });
  };

  if (status === 'unauthenticated' && tab !== 'match') {
    return (
      <div className="panel" style={{ maxWidth: 460, margin: '5rem auto', textAlign: 'center', padding: '3rem 2rem' }}>
        <div
          style={{
            width: '48px',
            height: '48px',
            borderRadius: '50%',
            background: 'var(--color-surface-elevated)',
            border: '1px solid var(--color-border)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 1.5rem auto',
            color: 'var(--color-fg-muted)',
          }}
        >
          <Lock size={22} />
        </div>
        <h2 style={{ fontSize: '1.4rem', marginBottom: '0.75rem' }}>Sign In to Resume Studio</h2>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.875rem', marginBottom: '1.75rem', lineHeight: 1.6 }}>
          Resume Studio features require authentication to save profiles and analyze application readiness.
        </p>
        <button onClick={openLogin} className="btn btn-primary" style={{ width: '100%' }}>
          <span>Sign In / Create Account</span>
          <ArrowRight size={15} />
        </button>
      </div>
    );
  }

  const handleAnalyze = async (e: FormEvent) => {
    e.preventDefault();
    if (!session?.accessToken) return;
    setLoading(true);
    setError(null);
    setAnalysis(null);
    try {
      const res = await resumesApi.analyze(session.accessToken, { resume_text: resumeText, job_description: jobDescription, job_title: jobTitle || undefined });
      setAnalysis(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Analysis failed');
    } finally {
      setLoading(false);
    }
  };

  const handleTailor = async (e: FormEvent) => {
    e.preventDefault();
    if (!session?.accessToken) return;
    setLoading(true);
    setError(null);
    setTailoredLatex('');
    try {
      const res = await resumesApi.tailor(session.accessToken, { resume_text: resumeText, job_description: jobDescription, job_title: jobTitle || undefined });
      setTailoredLatex(res.latex || res.tailored_text || '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Tailoring failed');
    } finally {
      setLoading(false);
    }
  };

  const handleCoverLetter = async (e: FormEvent) => {
    e.preventDefault();
    if (!session?.accessToken) return;
    setLoading(true);
    setError(null);
    setCoverLetter('');
    try {
      const res = await resumesApi.coverLetter(session.accessToken, {
        resume_text: resumeText, job_description: jobDescription, job_title: jobTitle || 'Role', company: company || 'Your Company', tone,
      });
      setCoverLetter(res.content);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Cover letter generation failed');
    } finally {
      setLoading(false);
    }
  };

  const handleGaps = async (e: FormEvent) => {
    e.preventDefault();
    if (!session?.accessToken) return;
    setLoading(true);
    setError(null);
    setGaps(null);
    try {
      const res = await resumesApi.gaps(session.accessToken, { resume_text: resumeText, job_description: jobDescription });
      setGaps(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Skill gap analysis failed');
    } finally {
      setLoading(false);
    }
  };

  const renderResult = () => {
    if (loading) return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: 'var(--color-fg-muted)', padding: '2rem 0' }}>
        <Loader2 size={20} className="status-dot-pulse" />
        <span>Processing with AI Engine...</span>
      </div>
    );
    if (error) return (
      <div className="auth-error">
        <AlertCircle size={16} />
        <span>{error}</span>
      </div>
    );
    if (tab === 'analyze' && analysis) return (
      <div className="panel panel-elevated" style={{ marginTop: '2rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem', marginBottom: '1.25rem' }}>
          <Sparkles size={18} className="text-accent" />
          <h3 style={{ fontSize: '1.125rem' }}>ATS Alignment Score</h3>
        </div>
        <div style={{ fontSize: '3rem', fontWeight: 800, color: analysis.ats_score >= 70 ? '#10b981' : analysis.ats_score >= 50 ? 'var(--color-accent)' : '#ef4444', lineHeight: 1, fontFamily: 'var(--font-mono)' }}>
          {analysis.ats_score}<span style={{ fontSize: '1.25rem', color: 'var(--color-fg-muted)' }}>/100</span>
        </div>
        {analysis.suggestions && analysis.suggestions.length > 0 && (
          <ul style={{ marginTop: '1.5rem', paddingLeft: '1.25rem', color: 'var(--color-fg-muted)', fontSize: '0.875rem' }}>
            {analysis.suggestions.map((s, i) => (
              <li key={i} style={{ marginBottom: '0.4rem' }}>{s}</li>
            ))}
          </ul>
        )}
      </div>
    );
    if (tab === 'tailor' && tailoredLatex) return (
      <div className="panel panel-elevated" style={{ marginTop: '2rem' }}>
        <h3 style={{ fontSize: '1.125rem', marginBottom: '0.75rem' }}>Tailored LaTeX Output</h3>
        <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'var(--font-mono)', fontSize: '0.8125rem', color: 'var(--color-fg-muted)', background: 'var(--color-surface)', padding: '1.25rem', border: '1px solid var(--color-border)', borderRadius: 'var(--border-radius-sm)', maxHeight: 500, overflow: 'auto' }}>{tailoredLatex}</pre>
      </div>
    );
    if (tab === 'cover-letter' && coverLetter) return (
      <div className="panel panel-elevated" style={{ marginTop: '2rem' }}>
        <h3 style={{ fontSize: '1.125rem', marginBottom: '0.75rem' }}>Generated Cover Letter</h3>
        <div style={{ whiteSpace: 'pre-wrap', color: 'var(--color-fg)', lineHeight: 1.7, fontSize: '0.9375rem' }}>{coverLetter}</div>
      </div>
    );
    if (tab === 'gaps' && gaps) return (
      <div className="panel panel-elevated" style={{ marginTop: '2rem' }}>
        <div style={{ fontSize: '2rem', fontWeight: 800, color: gaps.match_percentage >= 70 ? '#10b981' : 'var(--color-accent)', marginBottom: '1.25rem', fontFamily: 'var(--font-mono)' }}>
          {gaps.match_percentage}% MATCH
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '1.5rem' }}>
          <div>
            <div style={{ fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: '#34d399', marginBottom: '0.65rem' }}>
              Matching Competencies
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
              {gaps.matching_skills.map((s) => (
                <span key={s} className="badge badge-emerald" style={{ fontFamily: 'var(--font-mono)' }}>{s}</span>
              ))}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: '#f87171', marginBottom: '0.65rem' }}>
              Skill Gaps to Address
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
              {gaps.missing_skills.map((s) => (
                <span key={s} className="badge" style={{ borderColor: 'rgba(239,68,68,0.3)', color: '#f87171', fontFamily: 'var(--font-mono)' }}>{s}</span>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
    return null;
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      <div>
        <div className="badge badge-accent" style={{ marginBottom: '0.75rem' }}>
          <span className="status-dot status-dot-active" />
          <span>AI RESUME WORKBENCH</span>
        </div>
        <h1 style={{ fontSize: '2.25rem', marginBottom: '0.4rem' }}>
          Resume Studio & Career Tools
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1rem', maxWidth: '65ch' }}>
          Analyze ATS match rates, identify skill gaps, and tailor resume documents for targeted opportunities.
        </p>
      </div>

      {/* Tab Navigation */}
      <div
        style={{
          display: 'flex',
          gap: '0.5rem',
          borderBottom: '1px solid var(--color-border)',
          paddingBottom: '0.5rem',
          overflowX: 'auto',
        }}
      >
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => switchTab(t.key)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.45rem',
                padding: '0.55rem 0.95rem',
                fontSize: '0.8125rem',
                fontWeight: active ? 600 : 500,
                color: active ? '#ffffff' : 'var(--color-fg-muted)',
                background: active ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
                border: active ? '1px solid var(--color-border-hover)' : '1px solid transparent',
                borderRadius: 'var(--border-radius-sm)',
                cursor: 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              <Icon size={15} className={active ? 'text-accent' : ''} />
              <span>{t.label}</span>
            </button>
          );
        })}
      </div>

      {/* Tab Content */}
      {tab === 'match' && <MatchAnalyzer />}

      {tab !== 'match' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: '2rem' }}>
          <div className="panel" style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
            <form onSubmit={tab === 'analyze' ? handleAnalyze : tab === 'tailor' ? handleTailor : tab === 'cover-letter' ? handleCoverLetter : handleGaps}>
              <div style={{ marginBottom: '1.15rem' }}>
                <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--color-fg-muted)', marginBottom: '0.4rem' }}>
                  Candidate Resume Text
                </label>
                <textarea
                  value={resumeText}
                  onChange={(e) => setResumeText(e.target.value)}
                  required
                  placeholder="Paste your full resume text..."
                  style={{ width: '100%', minHeight: 120 }}
                />
              </div>

              <div style={{ marginBottom: '1.15rem' }}>
                <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--color-fg-muted)', marginBottom: '0.4rem' }}>
                  Target Job Description
                </label>
                <textarea
                  value={jobDescription}
                  onChange={(e) => setJobDescription(e.target.value)}
                  required
                  placeholder="Paste target job description and requirements..."
                  style={{ width: '100%', minHeight: 120 }}
                />
              </div>

              {(tab === 'analyze' || tab === 'tailor' || tab === 'cover-letter') && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '1.15rem' }}>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', color: 'var(--color-fg-muted)', marginBottom: '0.4rem' }}>
                      Job Title
                    </label>
                    <input
                      type="text"
                      value={jobTitle}
                      onChange={(e) => setJobTitle(e.target.value)}
                      placeholder="e.g. Senior Frontend Engineer"
                    />
                  </div>
                  {tab === 'cover-letter' && (
                    <div>
                      <label style={{ display: 'block', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', color: 'var(--color-fg-muted)', marginBottom: '0.4rem' }}>
                        Company
                      </label>
                      <input
                        type="text"
                        value={company}
                        onChange={(e) => setCompany(e.target.value)}
                        placeholder="e.g. Stripe"
                      />
                    </div>
                  )}
                </div>
              )}

              <button
                type="submit"
                disabled={loading}
                className="btn btn-primary"
                style={{ width: '100%' }}
              >
                {loading ? (
                  <>
                    <Loader2 size={16} className="status-dot-pulse" />
                    <span>Processing...</span>
                  </>
                ) : (
                  <>
                    <Sparkles size={16} />
                    <span>Execute {TABS.find((t) => t.key === tab)?.label}</span>
                  </>
                )}
              </button>
            </form>
          </div>

          <div>
            {renderResult() || (
              <div className="panel" style={{ textAlign: 'center', padding: '4rem 1rem', color: 'var(--color-fg-subtle)' }}>
                <FileText size={48} style={{ margin: '0 auto 1rem auto', opacity: 0.3 }} />
                <h3 style={{ color: 'var(--color-fg)', marginBottom: '0.5rem' }}>Awaiting Execution</h3>
                <p style={{ fontSize: '0.875rem' }}>Submit the form on the left to run ATS optimization.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function ResumeStudioPage() {
  return (
    <Suspense fallback={<div style={{ padding: '4rem 0', textAlign: 'center', color: 'var(--color-fg-muted)' }}>Loading Resume Studio...</div>}>
      <ResumeStudioContent />
    </Suspense>
  );
}
