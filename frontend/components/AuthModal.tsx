'use client';

import { useState, useEffect, useCallback } from 'react';
import { signIn } from 'next-auth/react';
import { useRouter } from 'next/navigation';
import {
  X,
  Mail,
  Lock,
  ArrowRight,
  Loader2,
  AlertCircle,
  Eye,
  EyeOff,
  CheckCircle2,
} from 'lucide-react';

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  defaultTab?: 'login' | 'signup';
}

export default function AuthModal({ isOpen, onClose, defaultTab = 'login' }: AuthModalProps) {
  const [tab, setTab] = useState<'login' | 'signup'>(defaultTab);
  const router = useRouter();

  // Login state
  const [loginEmail, setLoginEmail] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [loginError, setLoginError] = useState('');
  const [loginLoading, setLoginLoading] = useState(false);

  // Signup state
  const [signupEmail, setSignupEmail] = useState('');
  const [signupPassword, setSignupPassword] = useState('');
  const [signupConfirm, setSignupConfirm] = useState('');
  const [signupError, setSignupError] = useState('');
  const [signupLoading, setSignupLoading] = useState(false);
  const [signupSuccess, setSignupSuccess] = useState(false);

  // Show/hide password toggles
  const [showLoginPass, setShowLoginPass] = useState(false);
  const [showSignupPass, setShowSignupPass] = useState(false);
  const [showConfirmPass, setShowConfirmPass] = useState(false);

  // Reset state when tab changes
  useEffect(() => {
    setLoginError('');
    setSignupError('');
    setSignupSuccess(false);
  }, [tab]);

  // Sync defaultTab when modal opens
  useEffect(() => {
    if (isOpen) {
      setTab(defaultTab);
    }
  }, [isOpen, defaultTab]);

  // Close on Escape key
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    },
    [onClose]
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';
    };
  }, [isOpen, handleKeyDown]);

  // Password strength helper
  const getPasswordStrength = (pass: string): { label: string; color: string; width: string } => {
    if (!pass) return { label: '', color: 'transparent', width: '0%' };
    if (pass.length < 6) return { label: 'Weak', color: '#ef4444', width: '25%' };
    if (pass.length < 8) return { label: 'Fair', color: '#f59e0b', width: '50%' };
    if (pass.length < 12 || !/[A-Z]/.test(pass) || !/[0-9]/.test(pass))
      return { label: 'Good', color: '#06b6d4', width: '75%' };
    return { label: 'Strong', color: '#10b981', width: '100%' };
  };

  const pwStrength = getPasswordStrength(signupPassword);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoginLoading(true);
    setLoginError('');
    try {
      const res = await signIn('credentials', {
        redirect: false,
        email: loginEmail,
        password: loginPassword,
      });
      if (res?.error) {
        setLoginError('Invalid email or password. Please check your credentials.');
      } else {
        onClose();
        router.refresh();
      }
    } catch {
      setLoginError('An unexpected network error occurred.');
    } finally {
      setLoginLoading(false);
    }
  };

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setSignupError('');

    if (signupPassword !== signupConfirm) {
      setSignupError('Passwords do not match.');
      return;
    }
    if (signupPassword.length < 8) {
      setSignupError('Password must be at least 8 characters.');
      return;
    }

    setSignupLoading(true);
    try {
      const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      const res = await fetch(`${API_URL}/api/auth/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: signupEmail, password: signupPassword }),
      });

      if (!res.ok) {
        const data = await res.json();
        setSignupError(data.detail || 'Registration failed. Please try again.');
        setSignupLoading(false);
        return;
      }

      const signInRes = await signIn('credentials', {
        redirect: false,
        email: signupEmail,
        password: signupPassword,
      });

      if (signInRes?.error) {
        setSignupSuccess(true);
        setTimeout(() => {
          setTab('login');
        }, 1500);
      } else {
        onClose();
        router.refresh();
        router.push('/search');
      }
    } catch {
      setSignupError('Could not connect to server. Please check your connection.');
    } finally {
      setSignupLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Account Authentication"
      className="auth-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="auth-modal-container">
        {/* Close button */}
        <button
          onClick={onClose}
          className="auth-close-btn"
          aria-label="Close authentication modal"
        >
          <X size={16} />
        </button>

        {/* Header with bespoke radar brand mark */}
        <div className="auth-modal-header">
          <div className="auth-logo-badge">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="9" stroke="var(--color-accent)" strokeWidth="1.5" strokeOpacity="0.4" />
              <circle cx="12" cy="12" r="5" stroke="var(--color-accent)" strokeWidth="1.5" strokeOpacity="0.7" />
              <circle cx="12" cy="12" r="2" fill="var(--color-accent)" />
              <path d="M12 12L19 5" stroke="var(--color-accent)" strokeWidth="1.75" strokeLinecap="round" />
            </svg>
          </div>
          <div>
            <div className="auth-logo-text">TALENT RADAR</div>
            <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-muted)' }}>Career Intelligence Platform</div>
          </div>
        </div>

        {/* Tab switcher */}
        <div className="auth-tabs-segmented" role="tablist">
          <button
            role="tab"
            aria-selected={tab === 'login'}
            className={`auth-tab-btn ${tab === 'login' ? 'active' : ''}`}
            onClick={() => setTab('login')}
          >
            Sign In
          </button>
          <button
            role="tab"
            aria-selected={tab === 'signup'}
            className={`auth-tab-btn ${tab === 'signup' ? 'active' : ''}`}
            onClick={() => setTab('signup')}
          >
            Create Account
          </button>
        </div>

        {/* ─── LOGIN PANEL ─── */}
        {tab === 'login' && (
          <div role="tabpanel" aria-label="Sign In">
            {loginError && (
              <div className="auth-error" role="alert">
                <AlertCircle size={15} style={{ flexShrink: 0 }} />
                <span>{loginError}</span>
              </div>
            )}

            <form onSubmit={handleLogin}>
              <div className="auth-field">
                <label htmlFor="login-email" className="auth-label">Email Address</label>
                <div className="input-wrap">
                  <Mail size={16} className="input-icon" aria-hidden="true" />
                  <input
                    id="login-email"
                    type="email"
                    required
                    autoComplete="email"
                    value={loginEmail}
                    onChange={(e) => setLoginEmail(e.target.value)}
                    placeholder="name@company.com"
                    className="input-with-icon"
                  />
                </div>
              </div>

              <div className="auth-field">
                <label htmlFor="login-password" className="auth-label">Password</label>
                <div className="input-wrap">
                  <Lock size={16} className="input-icon" aria-hidden="true" />
                  <input
                    id="login-password"
                    type={showLoginPass ? 'text' : 'password'}
                    required
                    autoComplete="current-password"
                    value={loginPassword}
                    onChange={(e) => setLoginPassword(e.target.value)}
                    placeholder="Enter your password"
                    className="input-with-icon"
                    style={{ paddingRight: '2.5rem' }}
                  />
                  <button
                    type="button"
                    className="auth-eye-btn"
                    onClick={() => setShowLoginPass((v) => !v)}
                    aria-label={showLoginPass ? 'Hide password' : 'Show password'}
                  >
                    {showLoginPass ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>

              <button
                type="submit"
                disabled={loginLoading}
                className="btn btn-primary auth-submit-btn"
              >
                {loginLoading ? (
                  <Loader2 size={16} className="status-dot-pulse" />
                ) : (
                  <>
                    <span>Sign In</span>
                    <ArrowRight size={15} />
                  </>
                )}
              </button>
            </form>

            <p className="auth-switch-text">
              Don't have an account?{' '}
              <button className="auth-switch-link" onClick={() => setTab('signup')}>
                Create an account
              </button>
            </p>
          </div>
        )}

        {/* ─── SIGNUP PANEL ─── */}
        {tab === 'signup' && (
          <div role="tabpanel" aria-label="Create Account">
            {signupError && (
              <div className="auth-error" role="alert">
                <AlertCircle size={15} style={{ flexShrink: 0 }} />
                <span>{signupError}</span>
              </div>
            )}

            {signupSuccess && (
              <div className="auth-success" role="status">
                <CheckCircle2 size={15} style={{ flexShrink: 0 }} />
                <span>Account created successfully. Signing in...</span>
              </div>
            )}

            <form onSubmit={handleSignup}>
              <div className="auth-field">
                <label htmlFor="signup-email" className="auth-label">Email Address</label>
                <div className="input-wrap">
                  <Mail size={16} className="input-icon" aria-hidden="true" />
                  <input
                    id="signup-email"
                    type="email"
                    required
                    autoComplete="email"
                    value={signupEmail}
                    onChange={(e) => setSignupEmail(e.target.value)}
                    placeholder="name@company.com"
                    className="input-with-icon"
                  />
                </div>
              </div>

              <div className="auth-field">
                <label htmlFor="signup-password" className="auth-label">Password</label>
                <div className="input-wrap">
                  <Lock size={16} className="input-icon" aria-hidden="true" />
                  <input
                    id="signup-password"
                    type={showSignupPass ? 'text' : 'password'}
                    required
                    autoComplete="new-password"
                    value={signupPassword}
                    onChange={(e) => setSignupPassword(e.target.value)}
                    placeholder="At least 8 characters"
                    className="input-with-icon"
                    style={{ paddingRight: '2.5rem' }}
                  />
                  <button
                    type="button"
                    className="auth-eye-btn"
                    onClick={() => setShowSignupPass((v) => !v)}
                    aria-label={showSignupPass ? 'Hide password' : 'Show password'}
                  >
                    {showSignupPass ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
                {/* Password strength meter */}
                {signupPassword && (
                  <div className="auth-pw-strength" aria-live="polite">
                    <div className="auth-pw-bar">
                      <div
                        className="auth-pw-fill"
                        style={{ width: pwStrength.width, backgroundColor: pwStrength.color }}
                      />
                    </div>
                    <span className="auth-pw-label" style={{ color: pwStrength.color }}>
                      {pwStrength.label}
                    </span>
                  </div>
                )}
              </div>

              <div className="auth-field">
                <label htmlFor="signup-confirm" className="auth-label">Confirm Password</label>
                <div className="input-wrap">
                  <Lock size={16} className="input-icon" aria-hidden="true" />
                  <input
                    id="signup-confirm"
                    type={showConfirmPass ? 'text' : 'password'}
                    required
                    autoComplete="new-password"
                    value={signupConfirm}
                    onChange={(e) => setSignupConfirm(e.target.value)}
                    placeholder="Confirm your password"
                    className="input-with-icon"
                    style={{ paddingRight: '2.5rem' }}
                  />
                  <button
                    type="button"
                    className="auth-eye-btn"
                    onClick={() => setShowConfirmPass((v) => !v)}
                    aria-label={showConfirmPass ? 'Hide confirm password' : 'Show confirm password'}
                  >
                    {showConfirmPass ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>

              <button
                type="submit"
                disabled={signupLoading || signupSuccess}
                className="btn btn-primary auth-submit-btn"
              >
                {signupLoading ? (
                  <Loader2 size={16} className="status-dot-pulse" />
                ) : (
                  <>
                    <span>Create Account</span>
                    <ArrowRight size={15} />
                  </>
                )}
              </button>
            </form>

            <p className="auth-switch-text">
              Already have an account?{' '}
              <button className="auth-switch-link" onClick={() => setTab('login')}>
                Sign in
              </button>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
