'use client';

import { useState, useRef, useEffect } from 'react';
import { Target, Loader2, FileText, Sparkles, AlertTriangle, Download, ArrowRight, CheckCircle2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

interface EvaluationResult {
  match_score: number;
  missing_skills: string[];
  reasoning: string;
}

export default function MatchAnalyzer() {
  const [resume, setResume] = useState('');
  const [jd, setJd] = useState('');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<string>('');
  const [result, setResult] = useState<EvaluationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Resume Download
  const [tailoringResume, setTailoringResume] = useState(false);
  const [learningPath, setLearningPath] = useState<string | null>(null);
  const [loadingPath, setLoadingPath] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);

  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  const startAnalysis = async () => {
    if (!resume.trim() || !jd.trim()) {
      setError('Please provide both candidate resume and job description.');
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);
    setStatus('Initializing AI evaluation engine...');

    try {
      // 1. Dispatch Task
      const res = await fetch(`${API_URL}/api/v1/match/evaluate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resume_text: resume, job_description: jd }),
      });

      if (!res.ok) {
        throw new Error(`Evaluation failed: ${res.statusText}`);
      }

      const data = await res.json();
      const taskId = data.task_id;

      // 2. Connect to SSE Stream
      const sse = new EventSource(`${API_URL}/api/v1/match/evaluate/stream/${taskId}`);
      eventSourceRef.current = sse;

      sse.addEventListener('processing', (event: any) => {
        setStatus(event.data === 'PENDING' || !event.data ? 'Analyzing vector embeddings...' : event.data);
      });

      sse.addEventListener('success', (event: any) => {
        try {
          const resultData = JSON.parse(event.data);
          setResult(resultData);
          setStatus('Analysis complete.');
        } catch {
          setError('Failed to parse evaluation results.');
        }
        setLoading(false);
        sse.close();
      });

      sse.addEventListener('error', (event: any) => {
        setError(event.data || 'An error occurred during evaluation.');
        setLoading(false);
        sse.close();
      });

      sse.onerror = (err) => {
        console.error('SSE Error:', err);
        setError('Lost connection to evaluation engine.');
        setLoading(false);
        sse.close();
      };
    } catch (err: any) {
      setError(err.message);
      setLoading(false);
    }
  };

  const downloadTailoredResume = async () => {
    setTailoringResume(true);
    try {
      const res = await fetch(`${API_URL}/api/v1/match/tailor-resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resume_text: resume, job_description: jd }),
      });
      if (!res.ok) throw new Error('Failed to tailor resume');

      const contentDisposition = res.headers.get('Content-Disposition');
      let filename = 'Tailored_Resume.pdf';
      if (contentDisposition && contentDisposition.includes('filename=')) {
        filename = contentDisposition.split('filename=')[1].replace(/["']/g, '');
      }

      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setTailoringResume(false);
    }
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: '2rem' }}>
      {/* Input Panel */}
      <div className="panel" style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
          <div
            style={{
              width: '32px',
              height: '32px',
              borderRadius: 'var(--border-radius-sm)',
              background: 'var(--color-accent-subtle)',
              border: '1px solid var(--color-border-accent)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--color-accent)',
            }}
          >
            <FileText size={16} />
          </div>
          <div>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 700, margin: 0 }}>Input Context</h2>
            <p style={{ fontSize: '0.8125rem', color: 'var(--color-fg-muted)', margin: 0 }}>
              Provide the target job description and your resume text
            </p>
          </div>
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--color-fg-muted)' }}>
            Job Description
          </label>
          <textarea
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            placeholder="Paste target job requirements, qualifications, and role description..."
            style={{
              width: '100%',
              minHeight: '140px',
              background: 'var(--color-surface-elevated)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--border-radius)',
              color: 'var(--color-fg)',
              padding: '0.85rem',
              fontFamily: 'var(--font-body)',
              fontSize: '0.875rem',
              resize: 'vertical',
            }}
          />
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--color-fg-muted)' }}>
            Candidate Resume
          </label>
          <textarea
            value={resume}
            onChange={(e) => setResume(e.target.value)}
            placeholder="Paste current resume plain text or profile summary..."
            style={{
              width: '100%',
              minHeight: '140px',
              background: 'var(--color-surface-elevated)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--border-radius)',
              color: 'var(--color-fg)',
              padding: '0.85rem',
              fontFamily: 'var(--font-body)',
              fontSize: '0.875rem',
              resize: 'vertical',
            }}
          />
        </div>

        <button
          className="btn btn-primary"
          onClick={startAnalysis}
          disabled={loading}
          style={{ width: '100%', padding: '0.8rem' }}
        >
          {loading ? (
            <>
              <Loader2 size={16} className="status-dot-pulse" />
              <span>Analyzing Match Telemetry...</span>
            </>
          ) : (
            <>
              <Sparkles size={16} />
              <span>Evaluate Match & Gap Analysis</span>
            </>
          )}
        </button>

        {error && (
          <div className="auth-error">
            <AlertTriangle size={16} style={{ flexShrink: 0 }} />
            <span>{error}</span>
          </div>
        )}
      </div>

      {/* Output Panel */}
      <div className="panel panel-elevated" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
          <div
            style={{
              width: '32px',
              height: '32px',
              borderRadius: 'var(--border-radius-sm)',
              background: 'var(--color-info-subtle)',
              border: '1px solid rgba(6, 182, 212, 0.3)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#22d3ee',
            }}
          >
            <Target size={16} />
          </div>
          <div>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 700, margin: 0 }}>Fit Telemetry</h2>
            <p style={{ fontSize: '0.8125rem', color: 'var(--color-fg-muted)', margin: 0 }}>
              AI breakdown of candidate alignment and skill readiness
            </p>
          </div>
        </div>

        {loading && (
          <div style={{ textAlign: 'center', padding: '4rem 1rem', color: 'var(--color-fg-muted)' }}>
            <div
              style={{
                width: '56px',
                height: '56px',
                borderRadius: '50%',
                border: '3px solid var(--color-border)',
                borderTopColor: 'var(--color-accent)',
                animation: 'spin 1s linear infinite',
                margin: '0 auto 1.25rem auto',
              }}
            />
            <div style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--color-fg)' }}>{status}</div>
          </div>
        )}

        {result && !loading && (
          <div>
            {/* Score Radial Card */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '1.5rem',
                padding: '1.25rem',
                background: 'var(--color-surface)',
                border: '1px solid var(--color-border)',
                borderRadius: 'var(--border-radius)',
                marginBottom: '1.5rem',
              }}
            >
              <div
                style={{
                  width: '90px',
                  height: '90px',
                  borderRadius: '50%',
                  border: `3px solid ${result.match_score >= 70 ? 'var(--color-accent)' : '#64748b'}`,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                  boxShadow: result.match_score >= 70 ? '0 0 20px var(--color-accent-glow)' : 'none',
                }}
              >
                <span
                  style={{
                    fontSize: '1.875rem',
                    fontWeight: 800,
                    fontFamily: 'var(--font-mono)',
                    lineHeight: 1,
                    color: '#ffffff',
                  }}
                >
                  {result.match_score}
                </span>
                <span style={{ fontSize: '0.6875rem', color: 'var(--color-fg-muted)', textTransform: 'uppercase', marginTop: '2px' }}>
                  Percent Fit
                </span>
              </div>

              <div>
                <div style={{ fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', color: 'var(--color-accent)', letterSpacing: '0.04em' }}>
                  Model Assessment
                </div>
                <p style={{ fontSize: '0.875rem', color: 'var(--color-fg)', marginTop: '0.25rem', lineHeight: 1.5 }}>
                  {result.reasoning}
                </p>
              </div>
            </div>

            {/* Missing Skills */}
            {result.missing_skills.length > 0 && (
              <div style={{ marginBottom: '1.5rem' }}>
                <div style={{ fontSize: '0.8125rem', fontWeight: 600, color: 'var(--color-fg)', marginBottom: '0.75rem' }}>
                  Identified Skill Gaps
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '1.25rem' }}>
                  {result.missing_skills.map((skill, idx) => (
                    <span
                      key={idx}
                      style={{
                        padding: '0.25rem 0.65rem',
                        background: 'rgba(255, 255, 255, 0.04)',
                        border: '1px solid var(--color-border)',
                        borderRadius: 'var(--border-radius-sm)',
                        fontSize: '0.8125rem',
                        color: 'var(--color-fg-muted)',
                        fontFamily: 'var(--font-mono)',
                      }}
                    >
                      {skill}
                    </span>
                  ))}
                </div>

                {!learningPath && (
                  <button
                    className="btn btn-secondary"
                    onClick={async () => {
                      setLoadingPath(true);
                      try {
                        const res = await fetch(`${API_URL}/api/v1/recommend/learning-path`, {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ missing_skills: result.missing_skills, target_role: 'Software Engineer' }),
                        });
                        if (!res.ok) throw new Error('Failed to generate learning path');
                        const data = await res.json();
                        setLearningPath(data.learning_path);
                      } catch (err: any) {
                        setError(err.message);
                      } finally {
                        setLoadingPath(false);
                      }
                    }}
                    disabled={loadingPath}
                    style={{ width: '100%' }}
                  >
                    {loadingPath ? (
                      <>
                        <Loader2 size={15} className="status-dot-pulse" />
                        <span>Generating Roadmap...</span>
                      </>
                    ) : (
                      <>
                        <Sparkles size={15} className="text-accent" />
                        <span>Generate Skill Upgrade Roadmap</span>
                      </>
                    )}
                  </button>
                )}

                {learningPath && (
                  <div
                    style={{
                      marginTop: '1rem',
                      padding: '1.25rem',
                      background: 'var(--color-surface)',
                      border: '1px solid var(--color-border)',
                      borderRadius: 'var(--border-radius)',
                    }}
                  >
                    <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--color-accent)', textTransform: 'uppercase', marginBottom: '0.5rem' }}>
                      Recommended Action Roadmap
                    </div>
                    <div className="markdown-content" style={{ fontSize: '0.875rem' }}>
                      <ReactMarkdown>{learningPath}</ReactMarkdown>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Export Tailored Resume */}
            <div style={{ paddingTop: '1.25rem', borderTop: '1px solid var(--color-border-subtle)' }}>
              <button
                className="btn btn-primary"
                onClick={downloadTailoredResume}
                disabled={tailoringResume}
                style={{ width: '100%' }}
              >
                {tailoringResume ? (
                  <>
                    <Loader2 size={16} className="status-dot-pulse" />
                    <span>Compiling PDF...</span>
                  </>
                ) : (
                  <>
                    <Download size={16} />
                    <span>Export Tailored Resume (PDF)</span>
                  </>
                )}
              </button>
            </div>
          </div>
        )}

        {!result && !loading && (
          <div style={{ textAlign: 'center', padding: '4rem 1rem', color: 'var(--color-fg-subtle)' }}>
            <Target size={48} style={{ margin: '0 auto 1rem auto', opacity: 0.3 }} />
            <p style={{ fontSize: '0.875rem' }}>Ready for analysis. Input job & resume telemetry to begin.</p>
          </div>
        )}
      </div>

      <style jsx>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
