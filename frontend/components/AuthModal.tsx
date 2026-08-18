'use client';

import { useState, type FormEvent, useEffect } from 'react';
import { signIn } from 'next-auth/react';
import { X, Eye, EyeSlash, WarningCircle } from '@phosphor-icons/react';

function PasswordStrength({ password }: { password: string }) {
  if (!password) return null;
  const strength =
    password.length < 6 ? 0
    : password.length < 10 ? 1
    : /[A-Z]/.test(password) && /[0-9]/.test(password) ? 3
    : 2;

  const labels = ['Weak', 'Fair', 'Good', 'Strong'];
  const colors = ['#dc2626', '#d97706', '#16a34a', '#16a34a'];
  const widths = ['25%', '50%', '75%', '100%'];

  return (
    <div style={{ marginTop: '0.375rem' }}>
      <div style={{ height: '3px', background: 'var(--border)', borderRadius: '99px', overflow: 'hidden' }}>
        <div
          style={{
            height: '100%',
            width: widths[strength],
            background: colors[strength],
            borderRadius: '99px',
            transition: 'width 300ms ease, background 300ms ease',
          }}
        />
      </div>
      <p style={{ fontSize: '0.75rem', color: colors[strength], marginTop: '0.25rem' }}>
        {labels[strength]}
      </p>
    </div>
  );
}

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  defaultTab?: 'login' | 'signup';
}

export default function AuthModal({ isOpen, onClose, defaultTab = 'login' }: AuthModalProps) {
  const [tab, setTab] = useState<'login' | 'signup'>(defaultTab);
  const [showPass, setShowPass] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setTab(defaultTab);
  }, [defaultTab]);

  useEffect(() => {
    if (!isOpen) {
      setEmail(''); setPassword(''); setName(''); setError(null); setShowPass(false);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const isSignup = tab === 'signup';

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (isSignup) {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/v1/auth/register`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password, full_name: name }),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || 'Registration failed');
        }
      }
      const result = await signIn('credentials', { email, password, redirect: false });
      if (result?.error) throw new Error('Invalid email or password');
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '1rem',
        background: 'rgba(24,24,27,0.4)',
        backdropFilter: 'blur(4px)',
      }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-lg)',
          width: '100%',
          maxWidth: '420px',
          padding: '2rem',
        }}
        role="dialog"
        aria-modal="true"
        aria-label={isSignup ? 'Create account' : 'Sign in'}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.75rem' }}>
          <div>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 600, color: 'var(--text)', marginBottom: '0.25rem' }}>
              {isSignup ? 'Create your account' : 'Welcome back'}
            </h2>
            <p style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>
              {isSignup ? 'Start finding better jobs faster.' : 'Sign in to continue.'}
            </p>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-muted)',
              padding: '0.25rem',
              borderRadius: 'var(--radius-sm)',
              lineHeight: 0,
            }}
            aria-label="Close"
          >
            <X size={18} />
          </button>
        </div>

        {/* Tab switcher */}
        <div
          style={{
            display: 'flex',
            borderBottom: '1px solid var(--border)',
            marginBottom: '1.5rem',
            gap: '1.5rem',
          }}
        >
          {[
            { label: 'Sign in', mode: 'login' as const },
            { label: 'Create account', mode: 'signup' as const },
          ].map(({ label, mode: m }) => {
            const active = tab === m;
            return (
              <button
                key={label}
                type="button"
                onClick={() => { setTab(m); setError(null); }}
                style={{
                  paddingBottom: '0.75rem',
                  background: 'transparent',
                  border: 'none',
                  borderBottom: `2px solid ${active ? 'var(--accent)' : 'transparent'}`,
                  cursor: 'pointer',
                  fontSize: '0.875rem',
                  fontWeight: active ? 500 : 400,
                  color: active ? 'var(--text)' : 'var(--text-muted)',
                  marginBottom: '-1px',
                  transition: 'color 150ms ease',
                }}
              >
                {label}
              </button>
            );
          })}
        </div>

        <form onSubmit={handleSubmit}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {isSignup && (
              <div>
                <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>
                  Full name
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Priya Sharma"
                  required={isSignup}
                  autoComplete="name"
                />
              </div>
            )}

            <div>
              <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>
                Email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                required
                autoComplete="email"
              />
            </div>

            <div>
              <label style={{ display: 'block', fontSize: '0.875rem', fontWeight: 500, color: 'var(--text)', marginBottom: '0.375rem' }}>
                Password
              </label>
              <div style={{ position: 'relative' }}>
                <input
                  type={showPass ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={isSignup ? 'At least 8 characters' : 'Your password'}
                  required
                  autoComplete={isSignup ? 'new-password' : 'current-password'}
                  style={{ paddingRight: '2.75rem' }}
                />
                <button
                  type="button"
                  onClick={() => setShowPass((v) => !v)}
                  style={{
                    position: 'absolute',
                    right: '0.75rem',
                    top: '50%',
                    transform: 'translateY(-50%)',
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    color: 'var(--text-muted)',
                    lineHeight: 0,
                  }}
                  aria-label={showPass ? 'Hide password' : 'Show password'}
                >
                  {showPass ? <EyeSlash size={16} /> : <Eye size={16} />}
                </button>
              </div>
              {isSignup && <PasswordStrength password={password} />}
            </div>
          </div>

          {error && (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                marginTop: '1rem',
                padding: '0.75rem 1rem',
                background: 'var(--error-bg)',
                border: '1px solid rgba(220,38,38,0.2)',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--error)',
                fontSize: '0.875rem',
              }}
            >
              <WarningCircle size={16} style={{ flexShrink: 0 }} />
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              marginTop: '1.25rem',
              width: '100%',
              padding: '0.625rem',
              background: loading ? 'var(--border-hover)' : 'var(--accent)',
              color: '#fff',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              fontSize: '0.9375rem',
              fontWeight: 500,
              cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'background 150ms ease',
            }}
            onMouseEnter={(e) => !loading && (e.currentTarget.style.background = 'var(--accent-hover)')}
            onMouseLeave={(e) => !loading && (e.currentTarget.style.background = 'var(--accent)')}
          >
            {loading ? 'Please wait...' : isSignup ? 'Create account' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  );
}
