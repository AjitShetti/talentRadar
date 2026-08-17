'use client';

import Link from 'next/link';
import {
  Search,
  FileText,
  Target,
  ArrowRight,
  Github,
  Radio,
  Sparkles,
  Bot,
  Zap,
  Globe2,
  CheckCircle2,
  Lock,
  Layers,
} from 'lucide-react';
import { useSession } from 'next-auth/react';
import { useAuthModal } from '@/components/AuthModalProvider';

const PLATFORM_LOGOS = [
  { name: 'Greenhouse', symbol: 'GH' },
  { name: 'Lever', symbol: 'LV' },
  { name: 'Ashby', symbol: 'AS' },
  { name: 'LinkedIn', symbol: 'LI' },
  { name: 'Naukri', symbol: 'NK' },
  { name: 'Workday', symbol: 'WD' },
];

export default function HomePage() {
  const { status } = useSession();
  const { openLogin, openSignup } = useAuthModal();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '5.5rem' }}>
      {/* ─── 1. HERO SECTION (Asymmetric, Viewport Stable) ─── */}
      <section
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
          gap: '3.5rem',
          alignItems: 'center',
          paddingTop: '1.5rem',
          paddingBottom: '1rem',
        }}
      >
        {/* Left: Copy Stack (Max 4 text elements) */}
        <div>
          {/* 1. Eyebrow */}
          <div
            className="badge badge-accent"
            style={{
              marginBottom: '1.25rem',
              padding: '0.3rem 0.75rem',
              fontSize: '0.75rem',
              letterSpacing: '0.04em',
            }}
          >
            <span className="status-dot status-dot-active" />
            <span>REAL-TIME TALENT INTELLIGENCE</span>
          </div>

          {/* 2. Headline (Max 2 lines) */}
          <h1
            style={{
              fontSize: 'clamp(2.4rem, 4vw, 3.75rem)',
              lineHeight: 1.1,
              marginBottom: '1.25rem',
              letterSpacing: '-0.035em',
            }}
          >
            PRECISION JOB SEARCH FOR MODERN ENGINEERS
          </h1>

          {/* 3. Subtext (Max 20 words) */}
          <p
            style={{
              fontSize: '1.125rem',
              color: 'var(--color-fg-muted)',
              marginBottom: '2.25rem',
              maxWidth: '52ch',
              lineHeight: 1.6,
            }}
          >
            Scrape live ATS portals on demand, tailor ATS resumes with AI, and practice adaptive interview simulations.
          </p>

          {/* 4. CTAs (1 primary, 1 secondary) */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.85rem', flexWrap: 'wrap' }}>
            <Link href="/search" className="btn btn-primary btn-lg">
              <span>Start Job Radar</span>
              <ArrowRight size={17} />
            </Link>
            <Link href="/resume-studio" className="btn btn-secondary btn-lg">
              <span>Resume Studio</span>
            </Link>
          </div>
        </div>

        {/* Right: Live Interactive Telemetry Terminal */}
        <div
          className="glass-panel"
          style={{
            padding: '1.5rem',
            boxShadow: 'var(--shadow-lg)',
            border: '1px solid var(--color-border)',
            background: 'linear-gradient(180deg, rgba(20, 23, 34, 0.9) 0%, rgba(13, 15, 23, 0.95) 100%)',
          }}
        >
          {/* Terminal Window Header */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              paddingBottom: '1rem',
              marginBottom: '1.25rem',
              borderBottom: '1px solid var(--color-border-subtle)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <span style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#ef4444', opacity: 0.8 }} />
              <span style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#f59e0b', opacity: 0.8 }} />
              <span style={{ width: '10px', height: '10px', borderRadius: '50%', background: '#10b981', opacity: 0.8 }} />
              <span
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '0.75rem',
                  color: 'var(--color-fg-subtle)',
                  marginLeft: '0.5rem',
                }}
              >
                radar-telemetry.stream
              </span>
            </div>
            <div className="badge badge-emerald" style={{ fontSize: '0.6875rem' }}>
              <span className="status-dot status-dot-active" />
              <span>ACTIVE</span>
            </div>
          </div>

          {/* Live Mock Stream Rows */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.85rem' }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.75rem 1rem',
                background: 'rgba(255, 255, 255, 0.03)',
                borderRadius: 'var(--border-radius-sm)',
                border: '1px solid var(--color-border-subtle)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--color-accent)' }} />
                <div>
                  <div style={{ fontSize: '0.875rem', fontWeight: 600, color: 'var(--color-fg)' }}>
                    Staff Backend Engineer
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-subtle)' }}>Stripe · Ashby Portal · Remote</div>
                </div>
              </div>
              <div className="badge badge-accent" style={{ fontFamily: 'var(--font-mono)' }}>96% FIT</div>
            </div>

            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.75rem 1rem',
                background: 'rgba(255, 255, 255, 0.03)',
                borderRadius: 'var(--border-radius-sm)',
                border: '1px solid var(--color-border-subtle)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#38bdf8' }} />
                <div>
                  <div style={{ fontSize: '0.875rem', fontWeight: 600, color: 'var(--color-fg)' }}>
                    AI Infrastructure Architect
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-subtle)' }}>Scale AI · Greenhouse · SF / Remote</div>
                </div>
              </div>
              <div className="badge badge-emerald" style={{ fontFamily: 'var(--font-mono)' }}>92% FIT</div>
            </div>

            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '0.75rem 1rem',
                background: 'rgba(255, 255, 255, 0.03)',
                borderRadius: 'var(--border-radius-sm)',
                border: '1px solid var(--color-border-subtle)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#a855f7' }} />
                <div>
                  <div style={{ fontSize: '0.875rem', fontWeight: 600, color: 'var(--color-fg)' }}>
                    Full Stack Tech Lead
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-subtle)' }}>Vercel · Lever ATS · Remote</div>
                </div>
              </div>
              <div className="badge badge-info" style={{ fontFamily: 'var(--font-mono)' }}>89% FIT</div>
            </div>
          </div>

          {/* Telemetry bottom bar */}
          <div
            style={{
              marginTop: '1.25rem',
              paddingTop: '0.85rem',
              borderTop: '1px solid var(--color-border-subtle)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              fontSize: '0.75rem',
              color: 'var(--color-fg-muted)',
              fontFamily: 'var(--font-mono)',
            }}
          >
            <span>INGESTION: 180ms</span>
            <span style={{ color: '#10b981' }}>STREAM SECURE</span>
          </div>
        </div>
      </section>

      {/* ─── 2. SOURCE INTEGRATIONS LOGO STRIP (Under hero, Logos Only) ─── */}
      <section
        style={{
          borderTop: '1px solid var(--color-border)',
          borderBottom: '1px solid var(--color-border)',
          padding: '1.75rem 0',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '1rem',
        }}
      >
        <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-subtle)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
          MONITORING REAL-TIME OPENINGS ACROSS GLOBAL SOURCES
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '2.5rem',
            flexWrap: 'wrap',
          }}
        >
          {PLATFORM_LOGOS.map((item) => (
            <div
              key={item.name}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                color: 'var(--color-fg-muted)',
                fontWeight: 600,
                fontSize: '0.9375rem',
                fontFamily: 'var(--font-display)',
                letterSpacing: '-0.01em',
                opacity: 0.75,
                transition: 'opacity var(--transition-fast)',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.opacity = '1')}
              onMouseLeave={(e) => (e.currentTarget.style.opacity = '0.75')}
            >
              <div
                style={{
                  width: '24px',
                  height: '24px',
                  borderRadius: '4px',
                  background: 'rgba(255,255,255,0.06)',
                  border: '1px solid var(--color-border)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '0.6875rem',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--color-fg)',
                }}
              >
                {item.symbol}
              </div>
              <span>{item.name}</span>
            </div>
          ))}
        </div>
      </section>

      {/* ─── 3. CORE INTELLIGENCE PILLARS (Bento Grid with Visual Diversity) ─── */}
      <section>
        <h2 style={{ fontSize: '2rem', marginBottom: '2rem', textAlign: 'center' }}>
          INTELLIGENT CAREER SUITE
        </h2>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
            gap: '1.5rem',
          }}
        >
          {/* Bento Cell 1: Live Multi-Source Search */}
          <Link href="/search" style={{ textDecoration: 'none' }}>
            <div
              className="panel panel-interactive"
              style={{
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
                background: 'linear-gradient(135deg, rgba(20, 23, 34, 0.9) 0%, rgba(25, 30, 45, 0.4) 100%)',
              }}
            >
              <div>
                <div
                  style={{
                    width: '40px',
                    height: '40px',
                    borderRadius: 'var(--border-radius-sm)',
                    background: 'var(--color-accent-subtle)',
                    border: '1px solid var(--color-border-accent)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: 'var(--color-accent)',
                    marginBottom: '1.25rem',
                  }}
                >
                  <Search size={20} />
                </div>
                <h3 style={{ fontSize: '1.25rem', marginBottom: '0.5rem', color: '#ffffff' }}>
                  Live Multi-Source Search
                </h3>
                <p style={{ fontSize: '0.875rem', color: 'var(--color-fg-muted)', lineHeight: 1.6 }}>
                  Aggregate on-demand job listings across Greenhouse, Ashby, Lever, LinkedIn, and specialized engineering portals with real-time SSE streaming.
                </p>
              </div>

              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                  color: 'var(--color-accent)',
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                  marginTop: '1.5rem',
                }}
              >
                <span>Launch Search</span>
                <ArrowRight size={14} />
              </div>
            </div>
          </Link>

          {/* Bento Cell 2: ATS Resume Studio */}
          <Link href="/resume-studio" style={{ textDecoration: 'none' }}>
            <div
              className="panel panel-interactive"
              style={{
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
                background: 'linear-gradient(135deg, rgba(20, 23, 34, 0.9) 0%, rgba(16, 185, 129, 0.05) 100%)',
              }}
            >
              <div>
                <div
                  style={{
                    width: '40px',
                    height: '40px',
                    borderRadius: 'var(--border-radius-sm)',
                    background: 'var(--color-success-subtle)',
                    border: '1px solid rgba(16, 185, 129, 0.3)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#34d399',
                    marginBottom: '1.25rem',
                  }}
                >
                  <FileText size={20} />
                </div>
                <h3 style={{ fontSize: '1.25rem', marginBottom: '0.5rem', color: '#ffffff' }}>
                  Resume Studio & Gap Scoring
                </h3>
                <p style={{ fontSize: '0.875rem', color: 'var(--color-fg-muted)', lineHeight: 1.6 }}>
                  Vector-based token match scoring, instant ATS gap detection, and personalized PDF resume tailoring for every job opportunity.
                </p>
              </div>

              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                  color: '#34d399',
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                  marginTop: '1.5rem',
                }}
              >
                <span>Open Studio</span>
                <ArrowRight size={14} />
              </div>
            </div>
          </Link>

          {/* Bento Cell 3: AI Interview Simulator */}
          <Link href="/interview" style={{ textDecoration: 'none' }}>
            <div
              className="panel panel-interactive"
              style={{
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
                background: 'linear-gradient(135deg, rgba(20, 23, 34, 0.9) 0%, rgba(6, 182, 212, 0.05) 100%)',
              }}
            >
              <div>
                <div
                  style={{
                    width: '40px',
                    height: '40px',
                    borderRadius: 'var(--border-radius-sm)',
                    background: 'var(--color-info-subtle)',
                    border: '1px solid rgba(6, 182, 212, 0.3)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#22d3ee',
                    marginBottom: '1.25rem',
                  }}
                >
                  <Radio size={20} />
                </div>
                <h3 style={{ fontSize: '1.25rem', marginBottom: '0.5rem', color: '#ffffff' }}>
                  AI Interview Simulator
                </h3>
                <p style={{ fontSize: '0.875rem', color: 'var(--color-fg-muted)', lineHeight: 1.6 }}>
                  Conduct realistic mock technical and behavioral interviews with real-time speech synthesis, rubric grading, and actionable feedback.
                </p>
              </div>

              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                  color: '#22d3ee',
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                  marginTop: '1.5rem',
                }}
              >
                <span>Practice Interviews</span>
                <ArrowRight size={14} />
              </div>
            </div>
          </Link>
        </div>
      </section>

      {/* ─── 4. CTA BANNER ─── */}
      <section
        className="glass-panel"
        style={{
          padding: '3rem 2rem',
          textAlign: 'center',
          background: 'linear-gradient(180deg, rgba(20, 23, 34, 0.8) 0%, rgba(255, 87, 34, 0.05) 100%)',
          border: '1px solid var(--color-border)',
        }}
      >
        <h2 style={{ fontSize: '2rem', marginBottom: '0.75rem', color: '#ffffff' }}>
          ACCELERATE YOUR CAREER TRAJECTORY
        </h2>
        <p style={{ maxWidth: '50ch', margin: '0 auto 2rem auto', color: 'var(--color-fg-muted)', fontSize: '1.05rem' }}>
          Join thousands of developers using live telemetry to discover target roles and practice with AI coaches.
        </p>

        <div style={{ display: 'flex', justifyContent: 'center', gap: '1rem', flexWrap: 'wrap' }}>
          {status === 'authenticated' ? (
            <Link href="/search" className="btn btn-primary btn-lg">
              <span>Go to Job Radar</span>
              <ArrowRight size={16} />
            </Link>
          ) : (
            <button
              onClick={openSignup}
              className="btn btn-primary btn-lg"
            >
              <span>Create Free Account</span>
              <ArrowRight size={16} />
            </button>
          )}
        </div>
      </section>

      {/* ─── 5. FOOTER ─── */}
      <footer
        style={{
          borderTop: '1px solid var(--color-border)',
          paddingTop: '2.5rem',
          paddingBottom: '2.5rem',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          color: 'var(--color-fg-muted)',
          fontSize: '0.875rem',
          flexWrap: 'wrap',
          gap: '1.5rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
          <div
            style={{
              width: '20px',
              height: '20px',
              borderRadius: '4px',
              background: 'var(--color-accent-subtle)',
              border: '1px solid var(--color-border-accent)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--color-accent)',
            }}
          >
            <Zap size={12} />
          </div>
          <span>© 2026 TalentRadar Intelligence. All rights reserved.</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '1.5rem' }}>
          <Link href="/search" style={{ color: 'var(--color-fg-muted)' }}>
            Search
          </Link>
          <Link href="/resume-studio" style={{ color: 'var(--color-fg-muted)' }}>
            Resume Studio
          </Link>
          <Link href="/interview" style={{ color: 'var(--color-fg-muted)' }}>
            Interviews
          </Link>
          <a
            href="https://github.com"
            target="_blank"
            rel="noopener noreferrer"
            style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: 'var(--color-fg-muted)' }}
          >
            <Github size={15} />
            <span>GitHub</span>
          </a>
        </div>
      </footer>
    </div>
  );
}
