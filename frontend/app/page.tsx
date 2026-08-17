'use client';

import Link from 'next/link';
import { Search, FileText, Target, Zap, ArrowRight, Github, X } from 'lucide-react';
import { useSession } from 'next-auth/react';
import { useState, useEffect } from 'react';
import { useAuthModal } from '@/components/AuthModalProvider';

const features = [
  {
    icon: Search,
    title: 'Real-Time Multi-Source Search',
    description: 'Scrapes live job openings across Indian boards, Greenhouse, Ashby, Lever, and global remote portals.',
    href: '/search',
  },
  {
    icon: FileText,
    title: 'Resume Studio',
    description: 'Instant ATS gap analysis, LLM scoring, and tailored PDF resume generation for any job.',
    href: '/resume-studio',
  },
  {
    icon: Target,
    title: 'Smart Matching',
    description: 'Upload your profile and get personalized job recommendations with fit scores.',
    href: '/resume-studio?tab=match',
  },
];

export default function HomePage() {
  const { data: session, status } = useSession();
  const [showPopup, setShowPopup] = useState(false);
  const { openLogin, openSignup } = useAuthModal();

  useEffect(() => {
    // Show auth nudge popup after 5 s if not authenticated
    if (status === 'unauthenticated') {
      const timer = setTimeout(() => {
        setShowPopup(true);
      }, 5000);
      return () => clearTimeout(timer);
    }
  }, [status]);

  return (
    <div style={{ position: 'relative', overflow: 'hidden' }}>
      {/* Hero Section */}
      <section style={{ textAlign: 'center', marginBottom: '8rem' }}>
        <div
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.5rem',
            padding: '0.5rem 1rem',
            background: 'rgba(255,255,255,0.05)',
            border: '1px solid var(--color-border)',
            fontFamily: 'var(--font-display)',
            fontSize: '0.875rem',
            textTransform: 'uppercase',
            marginBottom: '2rem',
          }}
        >
          <Zap size={16} className="text-accent" />
          AI-Powered Job Intelligence
        </div>

        <h1 style={{ fontSize: '4rem', marginBottom: '1.5rem', lineHeight: 1.1 }}>
          FIND YOUR PERFECT JOB WITH<br />
          <span className="text-accent">AI PRECISION</span>
        </h1>

        <p
          style={{
            fontSize: '1.25rem',
            color: 'var(--color-fg-muted)',
            maxWidth: '600px',
            margin: '0 auto 3rem auto',
          }}
        >
          Search smarter with live real-time scraping, practice AI interviews, and get personalized
          job matches powered by machine learning.
        </p>

        <div style={{ display: 'flex', justifyContent: 'center', gap: '1rem', flexWrap: 'wrap' }}>
          <Link href="/resume-studio?tab=match" className="btn btn-primary">
            Start Matching
            <ArrowRight size={18} />
          </Link>
          <Link href="/search" className="btn">
            Explore Jobs
          </Link>
        </div>
      </section>

      {/* Features Section */}
      <section style={{ marginBottom: '8rem' }}>
        <h2 style={{ fontSize: '2rem', marginBottom: '3rem', textAlign: 'center' }}>
          CORE TELEMETRY
        </h2>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
            gap: '2rem',
          }}
        >
          {features.map((feature) => {
            const Icon = feature.icon;
            return (
              <Link key={feature.title} href={feature.href} style={{ textDecoration: 'none' }}>
                <div
                  className="panel"
                  style={{ height: '100%', transition: 'border-color var(--transition-speed) ease' }}
                  onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--color-accent)')}
                  onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
                >
                  <div style={{ marginBottom: '1.5rem' }}>
                    <Icon size={32} className="text-accent" />
                  </div>
                  <h3 style={{ fontSize: '1.5rem', marginBottom: '1rem' }}>{feature.title}</h3>
                  <p style={{ color: 'var(--color-fg-muted)', marginBottom: '2rem' }}>
                    {feature.description}
                  </p>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      color: 'var(--color-accent)',
                      fontWeight: 600,
                      fontFamily: 'var(--font-display)',
                    }}
                  >
                    INITIATE <ArrowRight size={16} />
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      </section>

      {/* Footer */}
      <footer
        style={{
          borderTop: '1px solid var(--color-border)',
          padding: '3rem 0',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          color: 'var(--color-fg-muted)',
        }}
      >
        <div>© 2026 TalentRadar Systems.</div>
        <div style={{ display: 'flex', gap: '2rem' }}>
          <a href="/api/docs" style={{ color: 'inherit' }}>
            API DOCS
          </a>
          <a
            href="https://github.com"
            style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'inherit' }}
          >
            <Github size={16} />
            GITHUB
          </a>
        </div>
      </footer>

      {/* Auth Nudge Popup */}
      {showPopup && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Sign in prompt"
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 8000,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '1rem',
            background: 'rgba(0,0,0,0.6)',
            backdropFilter: 'blur(4px)',
          }}
          onClick={(e) => { if (e.target === e.currentTarget) setShowPopup(false); }}
        >
          <div
            style={{
              position: 'relative',
              background: '#0f0f11',
              border: '1px solid var(--color-border)',
              padding: '2.5rem 2rem',
              maxWidth: '400px',
              width: '100%',
              textAlign: 'center',
              animation: 'auth-modal-in 250ms cubic-bezier(0.22,1,0.36,1) both',
            }}
          >
            {/* Orange corner accents */}
            <span className="auth-corner auth-corner-tl" aria-hidden="true" />
            <span className="auth-corner auth-corner-br" aria-hidden="true" />

            <button
              onClick={() => setShowPopup(false)}
              className="auth-close-btn"
              aria-label="Dismiss"
            >
              <X size={18} />
            </button>

            <div
              style={{
                width: 48,
                height: 48,
                background: 'var(--color-accent)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                margin: '0 auto 1.25rem',
              }}
            >
              <Zap size={22} color="#fff" />
            </div>

            <h3
              style={{
                fontFamily: 'var(--font-display)',
                fontSize: '1.4rem',
                fontWeight: 700,
                marginBottom: '0.75rem',
                letterSpacing: '-0.02em',
              }}
            >
              UNLOCK FULL POTENTIAL
            </h3>
            <p
              style={{
                color: 'var(--color-fg-muted)',
                fontSize: '0.9rem',
                marginBottom: '1.75rem',
                lineHeight: 1.6,
              }}
            >
              Save searches, tailor resumes on the fly, and get personalized smart matches.
            </p>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <button
                id="nudge-create-account-btn"
                className="auth-submit-btn"
                onClick={() => { setShowPopup(false); openSignup(); }}
              >
                CREATE FREE ACCOUNT
                <ArrowRight size={16} className="auth-btn-arrow" />
              </button>
              <button
                id="nudge-login-btn"
                onClick={() => { setShowPopup(false); openLogin(); }}
                style={{
                  width: '100%',
                  padding: '0.75rem',
                  background: 'rgba(255,255,255,0.05)',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-fg-muted)',
                  fontFamily: 'var(--font-display)',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  letterSpacing: '0.08em',
                  textTransform: 'uppercase',
                  cursor: 'pointer',
                  transition: 'border-color var(--transition-speed) ease, color var(--transition-speed) ease',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = 'var(--color-accent)';
                  e.currentTarget.style.color = 'var(--color-accent)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = 'var(--color-border)';
                  e.currentTarget.style.color = 'var(--color-fg-muted)';
                }}
              >
                SIGN IN
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
