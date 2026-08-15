'use client';

import { useState, useEffect, type FormEvent } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Loader2, Lock, Bot, Lightbulb, Brain, AlertCircle, Sparkles, Clock } from 'lucide-react';
import { agentApi } from '@/lib/agent-api';
import type { AgentMemory, AgentNextAction } from '@/lib/types';

const MEMORY_TYPES = ['preference', 'goal', 'note', 'application', 'weakness', 'achievement'] as const;

export default function AgentPage() {
  const { data: session, status } = useSession();
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<AgentNextAction | null>(null);
  const [memories, setMemories] = useState<AgentMemory[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [memoryType, setMemoryType] = useState<string>('note');
  const [memoryContent, setMemoryContent] = useState('');
  const [savingMemory, setSavingMemory] = useState(false);

  useEffect(() => {
    if (status !== 'authenticated' || !session?.accessToken) return;
    const token = session.accessToken as string;
    (async () => {
      try {
        const [actionRes, memRes] = await Promise.all([
          agentApi.nextAction(token).catch(() => null),
          agentApi.listMemories(token).catch(() => ({ memories: [], count: 0 })),
        ]);
        setAction(actionRes);
        setMemories(memRes.memories);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load agent data');
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
        <p style={{ color: 'var(--color-fg-muted)', marginBottom: '2rem' }}>Your personal career agent is ready to help.</p>
        <Link href="/login" className="btn btn-primary">Sign In</Link>
      </div>
    );
  }

  const handleRemember = async (e: FormEvent) => {
    e.preventDefault();
    if (!session?.accessToken || !memoryContent.trim()) return;
    setSavingMemory(true);
    setError(null);
    try {
      await agentApi.remember(session.accessToken, memoryType, memoryContent.trim());
      setMemoryContent('');
      // refresh memories
      const res = await agentApi.listMemories(session.accessToken);
      setMemories(res.memories);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to store memory');
    } finally {
      setSavingMemory(false);
    }
  };

  const priorityColor = (p: string) => p === 'high' ? '#ef4444' : p === 'medium' ? 'var(--color-accent)' : '#22c55e';

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <div style={{ marginBottom: '2.5rem' }}>
        <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 'clamp(2rem, 5vw, 3rem)', fontWeight: 800, letterSpacing: '-0.03em', lineHeight: 1.1 }}>
          YOUR<span style={{ color: 'var(--color-accent)' }}>_</span>AGENT
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1.1rem', marginTop: '0.75rem' }}>AI-powered career guidance based on your profile and goals.</p>
      </div>

      {error && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '1rem', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#fca5a5', fontFamily: 'var(--font-display)', fontSize: '0.85rem', marginBottom: '1.5rem' }}>
          <AlertCircle size={18} /> {error}
        </div>
      )}

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', padding: '3rem 0' }}>
          <Loader2 size={24} className="animate-spin text-accent" /> LOADING YOUR AGENT...
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem', alignItems: 'start' }}>
          {/* Left: Next Action */}
          <div className="panel" style={{ gridColumn: '1 / -1' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
              <Lightbulb size={20} className="text-accent" />
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1rem' }}>NEXT BEST ACTION</h3>
            </div>
            {action ? (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem' }}>
                  <span style={{ padding: '0.3rem 0.75rem', background: `${priorityColor(action.priority)}22`, border: `1px solid ${priorityColor(action.priority)}55`, color: priorityColor(action.priority), fontFamily: 'var(--font-display)', fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.06em' }}>
                    {(action.priority || 'MEDIUM').toUpperCase()} PRIORITY
                  </span>
                </div>
                <p style={{ fontSize: '1.1rem', lineHeight: 1.6, marginBottom: '0.75rem' }}>{action.action}</p>
                {action.reason && <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.9rem' }}>{action.reason}</p>}
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: '2rem' }}>
                <Bot size={32} color="var(--color-fg-muted)" style={{ marginBottom: '0.75rem' }} />
                <p style={{ color: 'var(--color-fg-muted)' }}>Complete your profile to get personalized recommendations.</p>
                <Link href="/onboarding" className="btn btn-primary" style={{ marginTop: '1rem', display: 'inline-flex', alignItems: 'center', gap: '0.5rem' }}>
                  <Sparkles size={16} /> SET UP PROFILE
                </Link>
              </div>
            )}
          </div>

          {/* Add Memory */}
          <div className="panel">
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
              <Brain size={20} className="text-accent" />
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1rem' }}>REMEMBER</h3>
            </div>
            <form onSubmit={handleRemember}>
              <div style={{ display: 'grid', gap: '1rem' }}>
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>TYPE</span>
                  <select value={memoryType} onChange={e => setMemoryType(e.target.value)} style={inputStyle}>
                    {MEMORY_TYPES.map(t => <option key={t} value={t}>{t.toUpperCase()}</option>)}
                  </select>
                </label>
                <label style={{ display: 'grid', gap: '0.4rem' }}>
                  <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.75rem', letterSpacing: '0.06em', color: 'var(--color-fg-muted)' }}>CONTENT</span>
                  <textarea value={memoryContent} onChange={e => setMemoryContent(e.target.value)} rows={4} placeholder="e.g. I prefer remote-first companies with strong engineering culture..." style={{ ...inputStyle, resize: 'vertical' }} />
                </label>
                <button type="submit" disabled={savingMemory || !memoryContent.trim()} className="btn btn-primary" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem' }}>
                  {savingMemory ? <Loader2 size={16} className="animate-spin" /> : <Brain size={16} />}
                  {savingMemory ? 'SAVING...' : 'REMEMBER'}
                </button>
              </div>
            </form>
          </div>

          {/* Memories list */}
          <div className="panel">
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
              <Clock size={20} className="text-accent" />
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1rem' }}>MEMORIES ({memories.length})</h3>
            </div>
            {memories.length === 0 ? (
              <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.9rem', textAlign: 'center', padding: '2rem 0' }}>No memories yet. Add one to get started.</p>
            ) : (
              <div style={{ display: 'grid', gap: '0.75rem', maxHeight: 400, overflow: 'auto' }}>
                {memories.map(m => (
                  <div key={m.id} style={{ padding: '0.875rem 1rem', background: 'rgba(0,0,0,0.2)', border: '1px solid var(--color-border)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.4rem' }}>
                      <span style={{ fontFamily: 'var(--font-display)', fontSize: '0.7rem', letterSpacing: '0.06em', color: 'var(--color-accent)' }}>{(m.memory_type || 'note').toUpperCase()}</span>
                      <span style={{ fontSize: '0.7rem', color: 'var(--color-fg-muted)' }}>{m.created_at?.slice(0, 10)}</span>
                    </div>
                    <p style={{ fontSize: '0.85rem', color: 'var(--color-fg-muted)', lineHeight: 1.5 }}>{m.content}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
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
