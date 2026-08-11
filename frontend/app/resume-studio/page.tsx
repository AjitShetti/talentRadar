'use client';

import { useState, type FormEvent } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Loader2, Lock, FileText, Target, BookOpen, Mail, Sparkles, ChevronRight, AlertCircle } from 'lucide-react';
import { resumesApi } from '@/lib/resumes-api';
import type { ResumeAnalyzeResponse, ResumeGapsResponse } from '@/lib/types';

type Tab = 'analyze' | 'tailor' | 'cover-letter' | 'gaps';
const TABS: { key: Tab; label: string; icon: typeof FileText }[] = [
  { key: 'analyze', label: 'ATS ANALYZE', icon: FileText },
  { key: 'tailor', label: 'TAILOR RESUME', icon: Target },
  { key: 'cover-letter', label: 'COVER LETTER', icon: Mail },
  { key: 'gaps', label: 'SKILL GAPS', icon: BookOpen },
];

export default function ResumeStudioPage() {
  const { data: session, status } = useSession();
  const [tab, setTab] = useState<Tab>('analyze');
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

  if (status === 'unauthenticated') {
    return (
      <div style={{ maxWidth: 480, margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={40} color="var(--color-fg-muted)" style={{ marginBottom: '1.5rem' }} />
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '1.5rem', marginBottom: '1rem' }}>SIGN IN TO CONTINUE</h2>
        <p style={{ color: 'var(--color-fg-muted)', marginBottom: '2rem' }}>Resume Studio helps you optimize your resume for any job.</p>
        <Link href="/login" className="btn btn-primary">Sign In</Link>
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
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', padding: '2rem 0' }}>
        <Loader2 size={24} className="animate-spin text-accent" /> PROCESSING...
      </div>
    );
    if (error) return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '1rem', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#fca5a5', fontFamily: 'var(--font-display)', fontSize: '0.85rem' }}>
        <AlertCircle size={18} /> {error}
      </div>
    );
    if (tab === 'analyze' && analysis) return (
      <div className="panel" style={{ marginTop: '2rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
          <Sparkles size={20} className="text-accent" />
          <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1.1rem' }}>ATS SCORE</h3>
        </div>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: '3.5rem', fontWeight: 800, color: analysis.ats_score >= 70 ? '#22c55e' : analysis.ats_score >= 50 ? 'var(--color-accent)' : '#ef4444', lineHeight: 1 }}>
          {analysis.ats_score}<span style={{ fontSize: '1.5rem', color: 'var(--color-fg-muted)' }}>/100</span>
        </div>
        {analysis.suggestions && analysis.suggestions.length > 0 && (
          <ul style={{ marginTop: '1.5rem', paddingLeft: '1.25rem' }}>
            {analysis.suggestions.map((s, i) => (
              <li key={i} style={{ marginBottom: '0.5rem', color: 'var(--color-fg-muted)' }}>{s}</li>
            ))}
          </ul>
        )}
      </div>
    );
    if (tab === 'tailor' && tailoredLatex) return (
      <div className="panel" style={{ marginTop: '2rem' }}>
        <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1.1rem', marginBottom: '1rem' }}>TAILORED LATEX</h3>
        <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'monospace', fontSize: '0.8rem', color: 'var(--color-fg-muted)', background: 'rgba(0,0,0,0.3)', padding: '1.5rem', border: '1px solid var(--color-border)', maxHeight: 500, overflow: 'auto' }}>{tailoredLatex}</pre>
      </div>
    );
    if (tab === 'cover-letter' && coverLetter) return (
      <div className="panel" style={{ marginTop: '2rem' }}>
        <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1.1rem', marginBottom: '1rem' }}>COVER LETTER</h3>
        <div style={{ whiteSpace: 'pre-wrap', color: 'var(--color-fg)', lineHeight: 1.8 }}>{coverLetter}</div>
      </div>
    );
    if (tab === 'gaps' && gaps) return (
      <div className="panel" style={{ marginTop: '2rem' }}>
        <div style={{ fontFamily: 'var(--font-display)', fontSize: '2.5rem', fontWeight: 800, color: gaps.match_percentage >= 70 ? '#22c55e' : 'var(--color-accent)', marginBottom: '1.5rem' }}>
          {gaps.match_percentage}% MATCH
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
          <div>
            <h4 style={{ fontFamily: 'var(--font-display)', fontSize: '0.8rem', letterSpacing: '0.08em', color: '#22c55e', marginBottom: '0.75rem' }}>MATCHING SKILLS</h4>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
              {gaps.matching_skills.map(s => (
                <span key={s} style={{ padding: '0.3rem 0.75rem', background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)', fontSize: '0.8rem', fontFamily: 'var(--font-display)' }}>{s}</span>
              ))}
            </div>
          </div>
          <div>
            <h4 style={{ fontFamily: 'var(--font-display)', fontSize: '0.8rem', letterSpacing: '0.08em', color: '#ef4444', marginBottom: '0.75rem' }}>MISSING SKILLS</h4>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
              {gaps.missing_skills.map(s => (
                <span key={s} style={{ padding: '0.3rem 0.75rem', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', fontSize: '0.8rem', fontFamily: 'var(--font-display)' }}>{s}</span>
              ))}
            </div>
          </div>
        </div>
      </div>
    );
    return null;
  };

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <div style={{ marginBottom: '2.5rem' }}>
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 'clamp(2rem, 5vw, 3rem)', fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.1 }}>
          RESUME<span style={{ color: 'var(--color-accent)' }}>_</span>STUDIO
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1.1rem', marginTop: '0.75rem' }}>Analyze, tailor, and optimize your resume for any job.</p>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: '0', marginBottom: '2rem', borderBottom: '1px solid var(--color-border)' }}>
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => { setTab(key); setError(null); }}
            style={{
              padding: '0.875rem 1.25rem',
              fontFamily: 'var(--font-display)',
              fontSize: '0.75rem',
              fontWeight: 600,
              letterSpacing: '0.06em',
              background: 'transparent',
              color: tab === key ? 'var(--color-accent)' : 'var(--color-fg-muted)',
              border: 'none',
              borderBottom: tab === key ? '2px solid var(--color-accent)' : '2px solid transparent',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
            }}
          >
            <Icon size={16} /> {label}
          </button>
        ))}
      </div>

      <form onSubmit={tab === 'analyze' ? handleAnalyze : tab === 'tailor' ? handleTailor : tab === 'cover-letter' ? handleCoverLetter : handleGaps}>
        <div className="panel" style={{ marginBottom: '1.5rem' }}>
          <div style={{ display: 'grid', gap: '1.25rem' }}>
            <label style={{ display: 'grid', gap: '0.4rem' }}>
              <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>YOUR RESUME TEXT</span>
              <textarea value={resumeText} onChange={e => setResumeText(e.target.value)} rows={6} placeholder="Paste your resume content here..." style={{ ...inputStyle, resize: 'vertical' }} />
            </label>
            <label style={{ display: 'grid', gap: '0.4rem' }}>
              <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>TARGET JOB DESCRIPTION</span>
              <textarea value={jobDescription} onChange={e => setJobDescription(e.target.value)} rows={6} placeholder="Paste the job description here..." style={{ ...inputStyle, resize: 'vertical' }} />
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.25rem' }}>
              <label style={{ display: 'grid', gap: '0.4rem' }}>
                <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>JOB TITLE (optional)</span>
                <input type="text" value={jobTitle} onChange={e => setJobTitle(e.target.value)} placeholder="Senior Python Engineer" style={inputStyle} />
              </label>
              {tab === 'cover-letter' && (
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>COMPANY</span>
                  <input type="text" value={company} onChange={e => setCompany(e.target.value)} placeholder="Acme Corp" style={inputStyle} />
                </label>
              )}
              {tab === 'cover-letter' && (
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>TONE</span>
                  <select value={tone} onChange={e => setTone(e.target.value)} style={inputStyle}>
                    <option value="professional">Professional</option>
                    <option value="enthusiastic">Enthusiastic</option>
                    <option value="formal">Formal</option>
                    <option value="conversational">Conversational</option>
                  </select>
                </label>
              )}
            </div>
          </div>
        </div>

        <button type="submit" disabled={loading || !resumeText || !jobDescription} className="btn btn-primary" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {loading ? <Loader2 size={16} className="animate-spin" /> : <ChevronRight size={16} />}
          {loading ? 'PROCESSING...' : tab === 'analyze' ? 'ANALYZE' : tab === 'tailor' ? 'TAILOR' : tab === 'cover-letter' ? 'GENERATE' : 'ANALYZE GAPS'}
        </button>
      </form>

      {renderResult()}
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
