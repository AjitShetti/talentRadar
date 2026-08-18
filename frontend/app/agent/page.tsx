'use client';

import { useState, useEffect, type FormEvent } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Lock, Brain, Lightbulb, Clock, WarningCircle } from '@phosphor-icons/react';
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
      } finally { setLoading(false); }
    })();
  }, [status, session]);

  if (status === 'unauthenticated') {
    return (
      <div style={{ maxWidth: '480px', margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={36} style={{ color: 'var(--text-subtle)', marginBottom: '1.25rem' }} />
        <h2 style={{ fontSize: '1.375rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.75rem' }}>Sign in to use Career Agent</h2>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.75rem' }}>Your personal career agent helps you decide what to do next.</p>
        <Link href="/login" style={{ display: 'inline-block', padding: '0.625rem 1.25rem', background: 'var(--accent)', color: '#fff', borderRadius: 'var(--radius-sm)', fontSize: '0.9375rem', fontWeight: 500, textDecoration: 'none' }}>Sign in</Link>
      </div>
    );
  }

  const handleRemember = async (e: FormEvent) => {
    e.preventDefault();
    if (!session?.accessToken || !memoryContent.trim()) return;
    setSavingMemory(true); setError(null);
    try {
      await agentApi.remember(session.accessToken, memoryType, memoryContent.trim());
      setMemoryContent('');
      const res = await agentApi.listMemories(session.accessToken);
      setMemories(res.memories);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to store memory');
    } finally { setSavingMemory(false); }
  };

  const priorityColor = (p: string) =>
    p === 'high' ? 'var(--error)' : p === 'medium' ? 'var(--warning)' : 'var(--success)';

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', paddingTop: '2.5rem' }}>
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--text)', marginBottom: '0.35rem' }}>
          Career Agent
        </h1>
        <p style={{ fontSize: '0.9375rem', color: 'var(--text-muted)' }}>
          AI guidance based on your profile, goals, and applications.
        </p>
      </div>

      {error && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.75rem 1rem', background: 'var(--error-bg)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 'var(--radius-sm)', color: 'var(--error)', fontSize: '0.875rem', marginBottom: '1.5rem' }}>
          <WarningCircle size={16} />
          {error}
        </div>
      )}

      {loading ? (
        <p style={{ color: 'var(--text-subtle)', fontSize: '0.9375rem' }}>Loading...</p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem', alignItems: 'start' }} className="agent-grid">
          {/* Next action - full width */}
          <div style={{ gridColumn: '1 / -1', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem', background: 'var(--surface)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '1.25rem' }}>
              <Lightbulb size={18} style={{ color: 'var(--accent)' }} />
              <h2 style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--text)' }}>Recommended next action</h2>
            </div>
            {action ? (
              <div>
                <span
                  style={{
                    display: 'inline-block', marginBottom: '0.875rem',
                    padding: '0.2rem 0.625rem', borderRadius: 'var(--radius-full)',
                    fontSize: '0.75rem', fontWeight: 500,
                    background: `color-mix(in srgb, ${priorityColor(action.priority)} 12%, transparent)`,
                    color: priorityColor(action.priority),
                    border: `1px solid color-mix(in srgb, ${priorityColor(action.priority)} 30%, transparent)`,
                  }}
                >
                  {action.priority} priority
                </span>
                <p style={{ fontSize: '1.0625rem', color: 'var(--text)', lineHeight: 1.55, marginBottom: '0.5rem' }}>{action.action}</p>
                {action.reason && <p style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>{action.reason}</p>}
              </div>
            ) : (
              <div style={{ textAlign: 'center', padding: '1.5rem 0', color: 'var(--text-subtle)' }}>
                <p style={{ marginBottom: '1rem', fontSize: '0.9375rem' }}>Complete your profile to get personalized recommendations.</p>
                <Link href="/onboarding" style={{ display: 'inline-block', padding: '0.5rem 1.125rem', background: 'var(--accent)', color: '#fff', borderRadius: 'var(--radius-sm)', fontSize: '0.875rem', fontWeight: 500, textDecoration: 'none' }}>Set up profile</Link>
              </div>
            )}
          </div>

          {/* Add memory */}
          <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem', background: 'var(--surface)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '1.25rem' }}>
              <Brain size={18} style={{ color: 'var(--accent)' }} />
              <h2 style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--text)' }}>Add a memory</h2>
            </div>
            <form onSubmit={handleRemember} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div>
                <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Type</label>
                <select value={memoryType} onChange={(e) => setMemoryType(e.target.value)}>
                  {MEMORY_TYPES.map((t) => (
                    <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                  ))}
                </select>
              </div>
              <div>
                <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>Memory</label>
                <textarea
                  value={memoryContent}
                  onChange={(e) => setMemoryContent(e.target.value)}
                  rows={4}
                  placeholder="e.g. I prefer remote-first companies with strong engineering culture..."
                  style={{ resize: 'vertical' }}
                />
              </div>
              <button
                type="submit"
                disabled={savingMemory || !memoryContent.trim()}
                style={{ padding: '0.5rem 1rem', background: (savingMemory || !memoryContent.trim()) ? 'var(--border-hover)' : 'var(--accent)', color: '#fff', border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '0.875rem', fontWeight: 500, cursor: (savingMemory || !memoryContent.trim()) ? 'not-allowed' : 'pointer' }}
              >
                {savingMemory ? 'Saving...' : 'Save memory'}
              </button>
            </form>
          </div>

          {/* Memory list */}
          <div style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '1.5rem', background: 'var(--surface)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '1.25rem' }}>
              <Clock size={18} style={{ color: 'var(--text-muted)' }} />
              <h2 style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--text)' }}>Memories ({memories.length})</h2>
            </div>
            {memories.length === 0 ? (
              <p style={{ color: 'var(--text-subtle)', fontSize: '0.9rem', textAlign: 'center', padding: '1.5rem 0' }}>
                No memories yet.
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0', maxHeight: '360px', overflowY: 'auto' }}>
                {memories.map((m, i) => (
                  <div
                    key={m.id}
                    style={{
                      padding: '0.875rem 0',
                      borderTop: i > 0 ? '1px solid var(--border)' : 'none',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                      <span style={{ fontSize: '0.75rem', fontWeight: 500, color: 'var(--accent)' }}>
                        {m.memory_type || 'note'}
                      </span>
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-subtle)' }}>
                        {m.created_at?.slice(0, 10)}
                      </span>
                    </div>
                    <p style={{ fontSize: '0.875rem', color: 'var(--text-muted)', lineHeight: 1.55 }}>{m.content}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <style>{`
        @media (max-width: 640px) { .agent-grid { grid-template-columns: 1fr !important; } }
      `}</style>
    </div>
  );
}
