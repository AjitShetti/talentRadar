'use client';

import { useState, useRef, useEffect } from 'react';
import { Target, Loader2, FileText, Briefcase, Zap, AlertTriangle, Download } from 'lucide-react';
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
      setError('Please provide both Resume and Job Description.');
      return;
    }

    setLoading(true);
    setError(null);
    setResult(null);
    setStatus('Initializing evaluation engine...');

    try {
      // 1. Dispatch Task
      const res = await fetch(`${API_URL}/api/v1/match/evaluate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resume_text: resume, job_description: jd })
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
        setStatus(event.data === 'PENDING' || !event.data ? 'Analyzing tokens...' : event.data);
      });

      sse.addEventListener('success', (event: any) => {
        try {
          const result = JSON.parse(event.data);
          setResult(result);
          setStatus('Analysis complete.');
        } catch (e) {
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
        body: JSON.stringify({ resume_text: resume, job_description: jd })
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
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))', gap: '2rem' }}>
      
      {/* Input Panel */}
      <div className="panel" style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
        <h2 style={{ fontSize: '1.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <FileText className="text-accent" /> INPUT TELEMETRY
        </h2>
        
        <div>
          <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 600 }}>Job Description</label>
          <textarea 
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            placeholder="Paste Job Description here..."
            style={{
              width: '100%', minHeight: '150px', background: 'rgba(0,0,0,0.5)', 
              border: '1px solid var(--color-border)', color: 'var(--color-fg)',
              padding: '1rem', fontFamily: 'var(--font-body)', resize: 'vertical'
            }}
          />
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 600 }}>Candidate Resume</label>
          <textarea 
            value={resume}
            onChange={(e) => setResume(e.target.value)}
            placeholder="Paste Resume text here..."
            style={{
              width: '100%', minHeight: '150px', background: 'rgba(0,0,0,0.5)', 
              border: '1px solid var(--color-border)', color: 'var(--color-fg)',
              padding: '1rem', fontFamily: 'var(--font-body)', resize: 'vertical'
            }}
          />
        </div>

        <button 
          className="btn btn-primary" 
          onClick={startAnalysis} 
          disabled={loading}
          style={{ justifyContent: 'center' }}
        >
          {loading ? (
            <><Loader2 className="animate-spin text-accent" /> INITIALIZING...</>
          ) : (
            <><Zap size={18} /> INITIATE MATCH ENGINE</>
          )}
        </button>

        {error && (
          <div style={{ padding: '1rem', border: '1px solid #ef4444', background: 'rgba(239, 68, 68, 0.1)', color: '#fca5a5', display: 'flex', gap: '0.5rem' }}>
            <AlertTriangle /> {error}
          </div>
        )}
      </div>

      {/* Output Panel */}
      <div className="panel" style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
        <h2 style={{ fontSize: '1.5rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Target className="text-accent" /> ANALYSIS RESULTS
        </h2>

        {loading && (
          <div style={{ textAlign: 'center', padding: '3rem 0', color: 'var(--color-fg-muted)' }}>
            <Loader2 size={48} className="animate-spin text-accent" style={{ margin: '0 auto 1rem auto' }} />
            <div style={{ fontFamily: 'var(--font-display)', letterSpacing: '0.1em' }}>{status}</div>
          </div>
        )}

        {result && !loading && (
          <div style={{ animation: 'fadeIn 0.5s ease-out' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '2rem', marginBottom: '2rem' }}>
              <div style={{ 
                width: '120px', height: '120px', 
                borderRadius: '50%', 
                border: `4px solid ${result.match_score >= 70 ? 'var(--color-accent)' : '#52525b'}`,
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                boxShadow: result.match_score >= 70 ? '0 0 20px rgba(255, 69, 0, 0.2)' : 'none'
              }}>
                <span style={{ fontSize: '2.5rem', fontWeight: 700, fontFamily: 'var(--font-display)', lineHeight: 1 }}>
                  {result.match_score}
                </span>
                <span style={{ fontSize: '0.75rem', color: 'var(--color-fg-muted)', textTransform: 'uppercase' }}>Score</span>
              </div>
              <div style={{ flex: 1 }}>
                <h3 style={{ marginBottom: '0.5rem', color: 'var(--color-accent)' }}>ENGINE REASONING</h3>
                <p style={{ fontSize: '0.9rem', color: 'var(--color-fg-muted)' }}>{result.reasoning}</p>
              </div>
            </div>

            {result.missing_skills.length > 0 && (
              <div style={{ marginBottom: '2rem' }}>
                <h3 style={{ marginBottom: '1rem', fontSize: '1rem', textTransform: 'uppercase', borderBottom: '1px solid var(--color-border)', paddingBottom: '0.5rem' }}>
                  Missing Competencies
                </h3>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1.5rem' }}>
                  {result.missing_skills.map((skill, idx) => (
                    <span key={idx} style={{ 
                      padding: '0.25rem 0.75rem', 
                      background: 'rgba(255,255,255,0.05)', 
                      border: '1px solid var(--color-border)',
                      fontSize: '0.85rem'
                    }}>
                      {skill}
                    </span>
                  ))}
                </div>

                {!learningPath && (
                  <button 
                    className="btn" 
                    onClick={async () => {
                      setLoadingPath(true);
                      try {
                        const res = await fetch(`${API_URL}/api/v1/recommend/learning-path`, {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ missing_skills: result.missing_skills, target_role: 'Software Engineer' })
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
                    style={{ width: '100%', justifyContent: 'center' }}
                  >
                    {loadingPath ? <><Loader2 size={16} className="animate-spin text-accent" /> GENERATING ROADMAP...</> : <><Zap size={16} className="text-accent" /> GENERATE SKILL ROADMAP</>}
                  </button>
                )}
                {learningPath && (
                  <div style={{ marginTop: '1.5rem', padding: '1.5rem', background: 'rgba(255,255,255,0.02)', border: '1px solid var(--color-border)' }}>
                    <h4 style={{ marginBottom: '1rem', color: 'var(--color-accent)', textTransform: 'uppercase', fontSize: '0.85rem' }}>Personalized Learning Path</h4>
                    <div style={{ fontSize: '0.9rem', lineHeight: 1.6, color: 'var(--color-fg)' }}>
                      <ReactMarkdown>{learningPath}</ReactMarkdown>
                    </div>
                  </div>
                )}
              </div>
            )}

            <div style={{ marginTop: '2rem', paddingTop: '2rem', borderTop: '1px solid var(--color-border)' }}>
              <button 
                className="btn btn-primary" 
                onClick={downloadTailoredResume}
                disabled={tailoringResume}
                style={{ width: '100%', justifyContent: 'center' }}
              >
                {tailoringResume ? <Loader2 className="animate-spin" /> : <Download size={18} />}
                EXPORT TAILORED RESUME (.PDF)
              </button>
            </div>
          </div>
        )}

        {!result && !loading && (
          <div style={{ textAlign: 'center', padding: '3rem 0', color: 'var(--color-border)' }}>
            <Target size={64} style={{ margin: '0 auto 1rem auto', opacity: 0.5 }} />
            <p>AWAITING INPUT DATA</p>
          </div>
        )}
      </div>

      <style jsx>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
