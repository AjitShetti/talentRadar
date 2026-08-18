'use client';

import { useState, useRef, useEffect } from 'react';
import { ArrowRight, WarningCircle, Download, CheckCircle } from '@phosphor-icons/react';
import ReactMarkdown from 'react-markdown';

interface EvaluationResult {
  match_score: number;
  missing_skills: string[];
  reasoning: string;
}

interface MatchAnalyzerProps {
  token?: string;
}

export default function MatchAnalyzer({ token }: MatchAnalyzerProps) {
  const [resume, setResume] = useState('');
  const [jd, setJd] = useState('');
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState('');
  const [result, setResult] = useState<EvaluationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tailoring, setTailoring] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);
  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

  useEffect(() => () => { eventSourceRef.current?.close(); }, []);

  const startAnalysis = async () => {
    if (!resume.trim() || !jd.trim()) {
      setError('Please paste both your resume and the job description.');
      return;
    }
    setLoading(true); setError(null); setResult(null); setStatusText('Analyzing...');

    try {
      const res = await fetch(`${API_URL}/api/v1/match/evaluate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resume_text: resume, job_description: jd }),
      });
      if (!res.ok) throw new Error(`Analysis failed: ${res.statusText}`);
      const { task_id: taskId } = await res.json();

      const sse = new EventSource(`${API_URL}/api/v1/match/evaluate/stream/${taskId}`);
      eventSourceRef.current = sse;

      sse.addEventListener('processing', (e: MessageEvent) => {
        setStatusText(e.data && e.data !== 'PENDING' ? e.data : 'Evaluating...');
      });
      sse.addEventListener('success', (e: MessageEvent) => {
        try { setResult(JSON.parse(e.data)); setStatusText(''); } catch { setError('Failed to parse result.'); }
        setLoading(false); sse.close();
      });
      sse.addEventListener('error', (e: MessageEvent) => {
        setError((e as MessageEvent).data || 'Analysis failed.'); setLoading(false); sse.close();
      });
      sse.onerror = () => { setError('Connection lost.'); setLoading(false); sse.close(); };
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
      setLoading(false);
    }
  };

  const downloadTailored = async () => {
    setTailoring(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/match/tailor-resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resume_text: resume, job_description: jd }),
      });
      if (!res.ok) throw new Error('Failed to tailor resume');
      const cd = res.headers.get('Content-Disposition');
      const filename = cd?.includes('filename=') ? cd.split('filename=')[1].replace(/['"]/g, '') : 'Tailored_Resume.pdf';
      const url = window.URL.createObjectURL(await res.blob());
      const a = Object.assign(document.createElement('a'), { href: url, download: filename });
      document.body.appendChild(a); a.click(); a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Download failed');
    } finally {
      setTailoring(false);
    }
  };

  const scoreColor = result ? (result.match_score >= 0.8 ? 'var(--success)' : result.match_score >= 0.6 ? 'var(--warning)' : 'var(--error)') : 'var(--text)';

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: '1.5rem' }}>
      {/* Input panel */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div>
          <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>
            Job description
          </label>
          <textarea
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            placeholder="Paste the full job description here..."
            rows={7}
            style={{ resize: 'vertical' }}
          />
        </div>
        <div>
          <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>
            Your resume
          </label>
          <textarea
            value={resume}
            onChange={(e) => setResume(e.target.value)}
            placeholder="Paste your resume text here..."
            rows={7}
            style={{ resize: 'vertical' }}
          />
        </div>

        {error && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.75rem 1rem', background: 'var(--error-bg)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 'var(--radius-sm)', color: 'var(--error)', fontSize: '0.875rem' }}>
            <WarningCircle size={16} style={{ flexShrink: 0 }} />
            {error}
          </div>
        )}

        <button
          onClick={startAnalysis}
          disabled={loading}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem',
            padding: '0.625rem 1.25rem', background: loading ? 'var(--border-hover)' : 'var(--accent)',
            color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)',
            fontSize: '0.9375rem', fontWeight: 500, cursor: loading ? 'not-allowed' : 'pointer',
            transition: 'background 150ms ease',
          }}
          onMouseEnter={(e) => !loading && (e.currentTarget.style.background = 'var(--accent-hover)')}
          onMouseLeave={(e) => !loading && (e.currentTarget.style.background = 'var(--accent)')}
        >
          {loading ? statusText || 'Analyzing...' : 'Analyze match'}
          {!loading && <ArrowRight size={16} />}
        </button>
      </div>

      {/* Result panel */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        {result ? (
          <>
            {/* Score */}
            <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem', textAlign: 'center' }}>
              <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>Match score</p>
              <p style={{ fontSize: '3rem', fontWeight: 700, color: scoreColor, letterSpacing: '-0.03em', lineHeight: 1 }}>
                {Math.round(result.match_score * 100)}%
              </p>
            </div>

            {/* Missing skills */}
            {result.missing_skills?.length > 0 && (
              <div>
                <p style={{ fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.75rem' }}>Skill gaps</p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
                  {result.missing_skills.map((skill) => (
                    <span
                      key={skill}
                      style={{
                        padding: '0.25rem 0.625rem',
                        background: 'var(--warning-bg)',
                        border: '1px solid rgba(217,119,6,0.25)',
                        borderRadius: 'var(--radius-full)',
                        fontSize: '0.8125rem',
                        color: 'var(--warning)',
                      }}
                    >
                      {skill}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Reasoning */}
            {result.reasoning && (
              <div>
                <p style={{ fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.75rem' }}>Analysis</p>
                <div
                  className="markdown-content"
                  style={{
                    fontSize: '0.9rem',
                    color: 'var(--text-muted)',
                    maxHeight: '280px',
                    overflowY: 'auto',
                    padding: '1rem',
                    background: 'var(--bg-subtle)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                  }}
                >
                  <ReactMarkdown>{result.reasoning}</ReactMarkdown>
                </div>
              </div>
            )}

            {/* Download */}
            {resume && jd && (
              <button
                onClick={downloadTailored}
                disabled={tailoring}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem',
                  padding: '0.5rem 1rem', background: 'var(--surface)', border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)', fontSize: '0.875rem', color: 'var(--text)',
                  cursor: tailoring ? 'not-allowed' : 'pointer', transition: 'border-color 150ms ease',
                }}
                onMouseEnter={(e) => !tailoring && (e.currentTarget.style.borderColor = 'var(--border-hover)')}
                onMouseLeave={(e) => !tailoring && (e.currentTarget.style.borderColor = 'var(--border)')}
              >
                <Download size={15} />
                {tailoring ? 'Generating PDF...' : 'Download tailored resume'}
              </button>
            )}
          </>
        ) : (
          <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '3rem 1.5rem', textAlign: 'center', color: 'var(--text-subtle)', fontSize: '0.9375rem' }}>
            {loading ? (statusText || 'Analyzing your match...') : 'Paste your resume and a job description, then click Analyze.'}
          </div>
        )}
      </div>
    </div>
  );
}
