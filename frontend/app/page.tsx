'use client';

import Link from 'next/link';
import { Search, TrendingUp, Target, Zap, ArrowRight, Github, X } from 'lucide-react';
import { useSession } from 'next-auth/react';
import { useState, useEffect } from 'react';

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
  const { data: session, status } = useSession();
  const [showPopup, setShowPopup] = useState(false);

  useEffect(() => {
    // If not loading and no session, set a timeout to show the popup
    if (status === 'unauthenticated') {
      const timer = setTimeout(() => {
        setShowPopup(true);
      }, 5000); // 5 seconds
      return () => clearTimeout(timer);
    }
  }, [status]);

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
          {status === 'unauthenticated' && (
            <button 
              onClick={() => setShowPopup(true)} 
              className="btn" 
              style={{ background: 'rgba(255,255,255,0.1)', borderColor: 'rgba(255,255,255,0.2)' }}
            >
              Log In / Sign Up
            </button>
          )}
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

      {/* Auth Reminder Popup */}
      {showPopup && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-in fade-in duration-300">
          <div className="relative bg-slate-900 border border-white/10 p-8 rounded-2xl shadow-2xl max-w-md w-full animate-in zoom-in-95 duration-300">
            <button 
              onClick={() => setShowPopup(false)}
              className="absolute top-4 right-4 text-slate-400 hover:text-white transition-colors"
            >
              <X size={20} />
            </button>
            
            <div className="text-center">
              <div className="mx-auto w-12 h-12 bg-gradient-to-br from-blue-500 to-purple-600 rounded-full flex items-center justify-center mb-4">
                <Zap className="text-white h-6 w-6" />
              </div>
              <h3 className="text-2xl font-bold mb-2">Unlock Full Potential</h3>
              <p className="text-slate-400 mb-6">
                Sign in to save your job searches, track market trends over time, and get personalized smart matches.
              </p>
              <div className="flex flex-col gap-3">
                <Link 
                  href="/signup" 
                  className="w-full py-3 px-4 rounded-xl font-bold text-white bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 transition-all shadow-lg"
                >
                  Create Free Account
                </Link>
                <Link 
                  href="/login" 
                  className="w-full py-3 px-4 rounded-xl font-semibold text-slate-300 bg-white/5 hover:bg-white/10 border border-white/10 transition-all"
                >
                  Log In
                </Link>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
