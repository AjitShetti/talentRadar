'use client';

import { useState } from 'react';
import { useSession } from 'next-auth/react';
import Link from 'next/link';
import { Lock } from '@phosphor-icons/react';
import { useAuthModal } from '@/components/AuthModalProvider';
import MatchAnalyzer from '@/components/MatchAnalyzer';

const TABS = ['Match', 'ATS Analysis', 'Tailor Resume', 'Cover Letter', 'Skill Gaps'] as const;
type Tab = typeof TABS[number];

export default function ResumeStudioPage() {
  const { data: session, status } = useSession();
  const { openLogin } = useAuthModal();
  const [activeTab, setActiveTab] = useState<Tab>('Match');

  if (status === 'unauthenticated') {
    return (
      <div style={{ maxWidth: '480px', margin: '6rem auto', textAlign: 'center' }}>
        <Lock size={36} style={{ color: 'var(--text-subtle)', marginBottom: '1.25rem' }} />
        <h2 style={{ fontSize: '1.375rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.75rem' }}>
          Sign in to use Resume Studio
        </h2>
        <p style={{ color: 'var(--text-muted)', marginBottom: '1.75rem' }}>
          Resume tailoring, ATS scoring, and cover letter generation require an account.
        </p>
        <button
          onClick={openLogin}
          style={{
            padding: '0.625rem 1.25rem', background: 'var(--accent)', color: '#fff',
            border: 'none', borderRadius: 'var(--radius-sm)', fontSize: '0.9375rem',
            fontWeight: 500, cursor: 'pointer',
          }}
        >
          Sign in
        </button>
      </div>
    );
  }

  const token = session?.accessToken as string | undefined;

  return (
    <div style={{ paddingTop: '2.5rem' }}>
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--text)', marginBottom: '0.35rem' }}>
          Resume Studio
        </h1>
        <p style={{ fontSize: '0.9375rem', color: 'var(--text-muted)' }}>
          Match your resume to a job, fix ATS gaps, and generate tailored cover letters.
        </p>
      </div>

      {/* Tab nav */}
      <div style={{ display: 'flex', gap: '0', borderBottom: '1px solid var(--border)', marginBottom: '2rem' }}>
        {TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: '0.625rem 1rem',
              background: 'transparent',
              border: 'none',
              borderBottom: `2px solid ${activeTab === tab ? 'var(--accent)' : 'transparent'}`,
              cursor: 'pointer',
              fontSize: '0.875rem',
              fontWeight: activeTab === tab ? 500 : 400,
              color: activeTab === tab ? 'var(--text)' : 'var(--text-muted)',
              marginBottom: '-1px',
              transition: 'color 150ms ease',
              whiteSpace: 'nowrap',
            }}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div>
        {activeTab === 'Match' && token && <MatchAnalyzer token={token} />}
        {activeTab !== 'Match' && (
          <div style={{ padding: '3rem 0', textAlign: 'center', color: 'var(--text-subtle)' }}>
            <p style={{ fontSize: '0.9375rem' }}>
              {activeTab} - select a tab to get started.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
