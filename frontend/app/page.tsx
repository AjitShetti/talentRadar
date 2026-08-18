'use client';

import Link from 'next/link';
import Image from 'next/image';
import { ArrowRight, MagnifyingGlass, FileText, Microphone, Waveform } from '@phosphor-icons/react';
import { useSession } from 'next-auth/react';
import { useAuthModal } from '@/components/AuthModalProvider';
import { motion, useReducedMotion } from 'motion/react';

const EASE = 'easeOut' as const;
const DURATION = 0.55;

const PLATFORMS = [
  'Greenhouse', 'Lever', 'Ashby', 'LinkedIn', 'Naukri',
  'Workday', 'Greenhouse', 'Lever', 'Ashby', 'LinkedIn', 'Naukri', 'Workday',
];

export default function HomePage() {
  const { status } = useSession();
  const { openLogin } = useAuthModal();
  const reduce = useReducedMotion();

  const fadeIn = (delay = 0) =>
    reduce
      ? {}
      : {
          initial: { opacity: 0, y: 22 },
          animate: { opacity: 1, y: 0 },
          transition: { duration: DURATION, ease: EASE, delay },
        };

  const scrollReveal = reduce
    ? {}
    : {
        initial: { opacity: 0, y: 28 },
        whileInView: { opacity: 1, y: 0 },
        viewport: { once: true as const, amount: 0.15 },
        transition: { duration: DURATION, ease: EASE },
      };

  return (
    <div>
      {/* ── Hero ── */}
      <section
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '4rem',
          alignItems: 'center',
          paddingTop: '4.5rem',
          paddingBottom: '4.5rem',
        }}
        className="hero-grid"
      >
        {/* Left: Copy */}
        <div>
          <motion.h1
            {...fadeIn(0)}
            style={{
              fontSize: 'clamp(2.25rem, 4vw, 3.375rem)',
              fontWeight: 700,
              letterSpacing: '-0.04em',
              lineHeight: 1.08,
              color: 'var(--text)',
              marginBottom: '1.125rem',
            }}
          >
            The job search tool engineers actually use.
          </motion.h1>

          <motion.p
            {...fadeIn(0.08)}
            style={{
              fontSize: '1.0625rem',
              color: 'var(--text-muted)',
              lineHeight: 1.6,
              maxWidth: '42ch',
              marginBottom: '2rem',
            }}
          >
            Live ATS scraping, AI resume tailoring, voice interview practice.
          </motion.p>

          <motion.div
            {...fadeIn(0.15)}
            style={{ display: 'flex', alignItems: 'center', gap: '0.875rem', flexWrap: 'wrap' }}
          >
            <Link href="/search" className="btn-primary">
              Start searching
              <ArrowRight size={15} weight="bold" />
            </Link>

            {status !== 'authenticated' && (
              <button
                onClick={openLogin}
                style={{
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  fontSize: '0.9rem',
                  color: 'var(--text-muted)',
                  padding: '0.625rem 0.25rem',
                }}
              >
                Sign in
              </button>
            )}
          </motion.div>
        </div>

        {/* Right: Product screenshot */}
        <motion.div
          {...fadeIn(0.12)}
          style={{
            borderRadius: 'var(--radius-lg)',
            overflow: 'hidden',
            border: '1px solid var(--border)',
            boxShadow: 'var(--shadow-lg)',
          }}
        >
          <Image
            src="/screenshot-search.jpg"
            alt="TalentRadar search showing Senior Software Engineer at TechCorp with 94% match score"
            width={760}
            height={570}
            priority
            style={{ width: '100%', height: 'auto', display: 'block' }}
          />
        </motion.div>
      </section>

      {/* ── Marquee Strip ── */}
      <section
        style={{
          borderTop: '1px solid var(--border)',
          borderBottom: '1px solid var(--border)',
          padding: '1.125rem 0',
        }}
      >
        <div className="marquee-wrap">
          <div className="marquee-track">
            {[...PLATFORMS, ...PLATFORMS].map((name, i) => (
              <span
                key={i}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '1.25rem',
                  padding: '0 2rem',
                  fontSize: '0.8125rem',
                  color: 'var(--text-subtle)',
                  fontFamily: 'var(--font-geist-mono, monospace)',
                  whiteSpace: 'nowrap',
                  letterSpacing: '0.01em',
                }}
              >
                {name}
                <span style={{ color: 'var(--border-hover)', fontSize: '0.5rem' }}>&#9679;</span>
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* ── Capability 1: Search - Stat + Screenshot ── */}
      <motion.section
        {...scrollReveal}
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1.1fr',
          gap: '4rem',
          alignItems: 'center',
          paddingTop: '5.5rem',
          paddingBottom: '5.5rem',
        }}
        className="cap-grid"
      >
        <div>
          <div
            style={{
              fontSize: '4.5rem',
              fontWeight: 800,
              letterSpacing: '-0.05em',
              lineHeight: 1,
              color: 'var(--accent)',
              marginBottom: '0.5rem',
              fontFamily: 'var(--font-geist-mono, monospace)',
            }}
          >
            47
          </div>
          <div
            style={{
              fontSize: '0.8125rem',
              color: 'var(--text-subtle)',
              marginBottom: '1.5rem',
              letterSpacing: '0.01em',
            }}
          >
            live sources scraped per search
          </div>
          <h2
            style={{
              fontSize: 'clamp(1.5rem, 2.5vw, 2rem)',
              fontWeight: 700,
              letterSpacing: '-0.03em',
              color: 'var(--text)',
              marginBottom: '0.875rem',
            }}
          >
            No stale listings. Ever.
          </h2>
          <p
            style={{
              fontSize: '1rem',
              color: 'var(--text-muted)',
              lineHeight: 1.7,
              maxWidth: '42ch',
            }}
          >
            TalentRadar hits Greenhouse, Lever, Ashby, LinkedIn, and Naukri on demand. You get the freshest postings the moment you search, not three days later.
          </p>
        </div>

        <div
          style={{
            borderRadius: 'var(--radius-lg)',
            overflow: 'hidden',
            border: '1px solid var(--border)',
            boxShadow: 'var(--shadow-md)',
          }}
        >
          <Image
            src="/screenshot-search.jpg"
            alt="TalentRadar live job search results with AI match scores"
            width={760}
            height={570}
            style={{ width: '100%', height: 'auto', display: 'block' }}
          />
        </div>
      </motion.section>

      {/* ── Capability 2: Resume - Tinted Bento ── */}
      <motion.section
        {...scrollReveal}
        style={{
          background: 'var(--bg-subtle)',
          borderTop: '1px solid var(--border)',
          borderBottom: '1px solid var(--border)',
          paddingTop: '5.5rem',
          paddingBottom: '5.5rem',
        }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1.1fr 1fr',
            gap: '4rem',
            alignItems: 'center',
          }}
          className="cap-grid"
        >
          {/* Screenshot left */}
          <div
            style={{
              borderRadius: 'var(--radius-lg)',
              overflow: 'hidden',
              border: '1px solid var(--border)',
              boxShadow: 'var(--shadow-md)',
            }}
          >
            <Image
              src="/screenshot-resume.jpg"
              alt="TalentRadar resume ATS analysis showing 87 score with skill gap breakdown"
              width={760}
              height={570}
              style={{ width: '100%', height: 'auto', display: 'block' }}
            />
          </div>

          {/* Copy right */}
          <div>
            <h2
              style={{
                fontSize: 'clamp(1.5rem, 2.5vw, 2rem)',
                fontWeight: 700,
                letterSpacing: '-0.03em',
                color: 'var(--text)',
                marginBottom: '0.875rem',
              }}
            >
              Your resume, scored and fixed in seconds.
            </h2>
            <p
              style={{
                fontSize: '1rem',
                color: 'var(--text-muted)',
                lineHeight: 1.7,
                marginBottom: '1.75rem',
                maxWidth: '40ch',
              }}
            >
              Paste a job description. The AI scores your resume against ATS requirements, highlights skill gaps, rewrites weak bullet points, and generates a cover letter matched to the role.
            </p>

            {/* 3 stat pills */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {[
                { label: 'ATS Score', value: 'Instant', icon: FileText },
                { label: 'Skill gap analysis', value: 'Per role', icon: MagnifyingGlass },
                { label: 'Cover letter', value: 'Auto-generated', icon: ArrowRight },
              ].map(({ label, value, icon: Icon }) => (
                <div
                  key={label}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '0.75rem 1rem',
                    background: 'var(--surface)',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                  }}
                >
                  <span style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', fontSize: '0.875rem', color: 'var(--text)' }}>
                    <Icon size={14} style={{ color: 'var(--accent)' }} />
                    {label}
                  </span>
                  <span style={{ fontSize: '0.8125rem', color: 'var(--text-subtle)', fontFamily: 'var(--font-geist-mono, monospace)' }}>
                    {value}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </motion.section>

      {/* ── Capability 3: Interview - Stat + Quote ── */}
      <motion.section
        {...scrollReveal}
        style={{
          paddingTop: '5.5rem',
          paddingBottom: '5.5rem',
        }}
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '4rem',
            alignItems: 'start',
          }}
          className="cap-grid"
        >
          <div>
            <h2
              style={{
                fontSize: 'clamp(1.5rem, 2.5vw, 2rem)',
                fontWeight: 700,
                letterSpacing: '-0.03em',
                color: 'var(--text)',
                marginBottom: '0.875rem',
              }}
            >
              Practice interviews with voice AI, not flashcards.
            </h2>
            <p
              style={{
                fontSize: '1rem',
                color: 'var(--text-muted)',
                lineHeight: 1.7,
                marginBottom: '2rem',
                maxWidth: '40ch',
              }}
            >
              Technical and behavioral rounds with a voice AI that talks back, grades your answers in real time, and gives you a rubric breakdown when the session ends.
            </p>
            <Link href="/interview" className="btn-secondary">
              Browse interview tracks
            </Link>
          </div>

          {/* Right: waveform visual + rubric stat */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {/* Live waveform card */}
            <div
              style={{
                background: '#18181b',
                borderRadius: 'var(--radius-lg)',
                padding: '2rem',
                display: 'flex',
                flexDirection: 'column',
                gap: '1.25rem',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem' }}>
                <Microphone size={16} style={{ color: '#3b82f6' }} />
                <span style={{ fontSize: '0.8125rem', color: '#a1a1aa', fontFamily: 'var(--font-geist-mono, monospace)' }}>
                  session active
                </span>
                <span
                  style={{
                    width: '7px',
                    height: '7px',
                    borderRadius: '50%',
                    background: '#22c55e',
                    animation: 'pulse 1.8s ease-in-out infinite',
                    marginLeft: 'auto',
                  }}
                />
              </div>
              <Waveform size={40} style={{ color: '#3b82f6', opacity: 0.9 }} />
              <p style={{ fontSize: '0.9375rem', color: '#e4e4e7', lineHeight: 1.55, margin: 0 }}>
                "Walk me through how you'd design a rate limiter for a high-traffic API."
              </p>
            </div>

            {/* Rubric stat row */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3, 1fr)',
                gap: '0.625rem',
              }}
            >
              {[
                { label: 'Correctness', score: '9/10' },
                { label: 'Clarity', score: '8/10' },
                { label: 'Depth', score: '7/10' },
              ].map(({ label, score }) => (
                <div
                  key={label}
                  style={{
                    padding: '0.875rem',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-sm)',
                    background: 'var(--surface)',
                    textAlign: 'center',
                  }}
                >
                  <div
                    style={{
                      fontSize: '1.375rem',
                      fontWeight: 700,
                      color: 'var(--text)',
                      letterSpacing: '-0.02em',
                      fontFamily: 'var(--font-geist-mono, monospace)',
                    }}
                  >
                    {score}
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-subtle)', marginTop: '0.25rem' }}>
                    {label}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </motion.section>

      {/* ── CTA - Dark Inversion ── */}
      <motion.section
        {...scrollReveal}
        className="section-dark"
        style={{
          marginLeft: 'calc(-1.5rem)',
          marginRight: 'calc(-1.5rem)',
          padding: '5rem 1.5rem',
        }}
      >
        <div style={{ maxWidth: '1280px', margin: '0 auto' }}>
          <div style={{ maxWidth: '52ch', marginBottom: '2.25rem' }}>
            <h2
              style={{
                fontSize: 'clamp(1.75rem, 3vw, 2.5rem)',
                fontWeight: 700,
                letterSpacing: '-0.035em',
                lineHeight: 1.1,
                color: '#fafafa',
                marginBottom: '0.875rem',
              }}
            >
              Ready to run a smarter job search?
            </h2>
            <p style={{ fontSize: '1rem', color: '#71717a', lineHeight: 1.65 }}>
              Free to start. No credit card. Your first search takes 30 seconds.
            </p>
          </div>

          <div style={{ display: 'flex', gap: '0.875rem', flexWrap: 'wrap' }}>
            <Link
              href="/search"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.5rem',
                padding: '0.6875rem 1.375rem',
                background: '#2563eb',
                color: '#fff',
                borderRadius: 'var(--radius-sm)',
                fontSize: '0.9375rem',
                fontWeight: 500,
                textDecoration: 'none',
                transition: 'background 150ms ease, transform 100ms ease',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#1d4ed8')}
              onMouseLeave={(e) => (e.currentTarget.style.background = '#2563eb')}
            >
              Search live jobs
              <ArrowRight size={15} weight="bold" />
            </Link>
            <Link
              href="/resume-studio"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.5rem',
                padding: '0.6875rem 1.375rem',
                background: 'transparent',
                color: '#e4e4e7',
                border: '1px solid rgba(255,255,255,0.15)',
                borderRadius: 'var(--radius-sm)',
                fontSize: '0.9375rem',
                fontWeight: 500,
                textDecoration: 'none',
                transition: 'border-color 150ms ease, transform 100ms ease',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'rgba(255,255,255,0.35)')}
              onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'rgba(255,255,255,0.15)')}
            >
              Open resume studio
            </Link>
          </div>
        </div>
      </motion.section>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
        @media (max-width: 768px) {
          .hero-grid { grid-template-columns: 1fr !important; padding-top: 2.5rem !important; padding-bottom: 2.5rem !important; gap: 2rem !important; }
          .cap-grid { grid-template-columns: 1fr !important; gap: 2rem !important; }
        }
      `}</style>
    </div>
  );
}
