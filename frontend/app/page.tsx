'use client';

import Link from 'next/link';
import { Search, TrendingUp, Target, Zap, ArrowRight, Github } from 'lucide-react';

const features = [
  {
    icon: Search,
    title: 'Semantic Search',
    description: "Search jobs using natural language. Our AI understands what you're looking for.",
    href: '/search',
  },
  {
    icon: TrendingUp,
    title: 'Market Trends',
    description: 'Get real-time insights into skill demand, salary trends, and market opportunities.',
    href: '/trends',
  },
  {
    icon: Target,
    title: 'Smart Matching',
    description: 'Upload your profile and get personalized job recommendations with match scores.',
    href: '/match',
  },
];

export default function HomePage() {
  return (
    <div style={{ position: 'relative', overflow: 'hidden' }}>
      {/* Hero Section */}
      <section style={{ textAlign: 'center', marginBottom: '8rem' }}>
        <div style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.5rem 1rem',
          background: 'rgba(255,255,255,0.05)',
          border: '1px solid var(--color-border)',
          fontFamily: 'var(--font-display)',
          fontSize: '0.875rem',
          textTransform: 'uppercase',
          marginBottom: '2rem'
        }}>
          <Zap size={16} className="text-accent" />
          AI-Powered Job Intelligence
        </div>
        
        <h1 style={{ fontSize: '4rem', marginBottom: '1.5rem', lineHeight: 1.1 }}>
          FIND YOUR PERFECT JOB WITH<br/>
          <span className="text-accent">AI PRECISION</span>
        </h1>
        
        <p style={{ 
          fontSize: '1.25rem', 
          color: 'var(--color-fg-muted)',
          maxWidth: '600px',
          margin: '0 auto 3rem auto'
        }}>
          Search smarter with semantic understanding, discover market trends, and get personalized job matches powered by machine learning.
        </p>

        <div style={{ display: 'flex', justifyContent: 'center', gap: '1rem' }}>
          <Link href="/match" className="btn btn-primary">
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
        
        <div style={{ 
          display: 'grid', 
          gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', 
          gap: '2rem' 
        }}>
          {features.map((feature) => {
            const Icon = feature.icon;
            return (
              <Link key={feature.title} href={feature.href} style={{ textDecoration: 'none' }}>
                <div className="panel" style={{ height: '100%', transition: 'border-color var(--transition-speed) ease' }}
                     onMouseEnter={(e) => e.currentTarget.style.borderColor = 'var(--color-accent)'}
                     onMouseLeave={(e) => e.currentTarget.style.borderColor = 'var(--color-border)'}>
                  <div style={{ marginBottom: '1.5rem' }}>
                    <Icon size={32} className="text-accent" />
                  </div>
                  <h3 style={{ fontSize: '1.5rem', marginBottom: '1rem' }}>
                    {feature.title}
                  </h3>
                  <p style={{ color: 'var(--color-fg-muted)', marginBottom: '2rem' }}>
                    {feature.description}
                  </p>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--color-accent)', fontWeight: 600, fontFamily: 'var(--font-display)' }}>
                    INITIATE <ArrowRight size={16} />
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      </section>

      {/* Footer */}
      <footer style={{ 
        borderTop: '1px solid var(--color-border)', 
        padding: '3rem 0',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        color: 'var(--color-fg-muted)'
      }}>
        <div>© 2026 TalentRadar Systems.</div>
        <div style={{ display: 'flex', gap: '2rem' }}>
          <a href="/api/docs" style={{ color: 'inherit' }}>API DOCS</a>
          <a href="https://github.com" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'inherit' }}>
            <Github size={16} />
            GITHUB
          </a>
        </div>
      </footer>
    </div>
  );
}
