'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession, signOut } from 'next-auth/react';
import { Zap, User, LogOut, History, ChevronDown, LogIn } from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import { useAuthModal } from './AuthModalProvider';

export default function Header() {
  const pathname = usePathname();
  const { status } = useSession();
  const { openLogin } = useAuthModal();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const navLinkStyle = (href: string) => ({
    textDecoration: 'none',
    color: pathname === href ? 'var(--color-accent)' : 'var(--color-fg-muted)',
    transition: 'color var(--transition-speed) ease',
  });

  return (
    <header
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '1.5rem 0',
        borderBottom: '1px solid var(--color-border)',
        marginBottom: '3rem',
      }}
    >
      {/* Logo */}
      <Link
        href="/"
        style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', textDecoration: 'none' }}
      >
        <Zap className="text-accent" />
        <span
          style={{
            fontSize: '1.25rem',
            fontWeight: 700,
            fontFamily: 'var(--font-display)',
            color: 'var(--color-fg)',
          }}
        >
          TALENT_RADAR
        </span>
      </Link>

      {/* Nav links */}
      <nav
        style={{
          display: 'flex',
          gap: '2rem',
          alignItems: 'center',
          fontFamily: 'var(--font-display)',
          fontWeight: 600,
        }}
      >
        <Link href="/search" style={navLinkStyle('/search')}>
          SEARCH
        </Link>
        <Link href="/trends" style={navLinkStyle('/trends')}>
          TRENDS
        </Link>
        <Link href="/match" style={navLinkStyle('/match')}>
          MATCH ENGINE
        </Link>
        <Link 
          href="/interview" 
          style={navLinkStyle('/interview')}
        >
          INTERVIEWS
        </Link>
        <Link 
          href="/applications" 
          style={navLinkStyle('/applications')}
        >
          APPLICATIONS
        </Link>
        <Link 
          href="/resume-studio" 
          style={navLinkStyle('/resume-studio')}
        >
          RESUME STUDIO
        </Link>
        <Link 
          href="/company-intel" 
          style={navLinkStyle('/company-intel')}
        >
          COMPANIES
        </Link>


        {status === 'authenticated' ? (
          <div style={{ position: 'relative' }} ref={dropdownRef}>
            <button 
              onClick={() => setDropdownOpen(!dropdownOpen)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.5rem',
                background: 'rgba(255,255,255,0.05)',
                border: '1px solid var(--color-border)',
                padding: '0.5rem 1rem',
                borderRadius: '0.5rem',
                color: 'var(--color-fg)',
                cursor: 'pointer',
                fontFamily: 'inherit',
                fontWeight: 600,
              }}
            >
              <User size={18} className="text-accent" />
              PROFILE
              <ChevronDown size={16} />
            </button>
            {dropdownOpen && (
              <div style={{
                position: 'absolute',
                top: '100%',
                right: 0,
                marginTop: '0.5rem',
                background: 'var(--color-bg)',
                border: '1px solid var(--color-border)',
                borderRadius: '0.5rem',
                padding: '0.5rem',
                minWidth: '200px',
                display: 'flex',
                flexDirection: 'column',
                gap: '0.25rem',
                zIndex: 50,
                boxShadow: '0 10px 25px rgba(0,0,0,0.5)'
              }}>
                <Link
                  href="/interview/history"
                  onClick={() => setDropdownOpen(false)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    padding: '0.75rem',
                    textDecoration: 'none',
                    color: 'var(--color-fg)',
                    borderRadius: '0.25rem',
                    transition: 'background var(--transition-speed) ease'
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.05)'}
                  onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                >
                  <History size={16} className="text-accent" />
                  Interview History
                </Link>
                <Link 
                  href="/onboarding" 
                  onClick={() => setDropdownOpen(false)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    padding: '0.75rem',
                    textDecoration: 'none',
                    color: 'var(--color-fg)',
                    borderRadius: '0.25rem',
                    transition: 'background var(--transition-speed) ease'
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.05)'}
                  onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                >
                  <User size={16} className="text-accent" />
                  My Profile
                </Link>
                <Link 
                  href="/agent" 
                  onClick={() => setDropdownOpen(false)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    padding: '0.75rem',
                    textDecoration: 'none',
                    color: 'var(--color-fg)',
                    borderRadius: '0.25rem',
                    transition: 'background var(--transition-speed) ease'
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.05)'}
                  onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                >
                  <Zap size={16} className="text-accent" />
                  Career Agent
                </Link>
                <Link 
                  href="/dashboard" 
                  onClick={() => setDropdownOpen(false)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    padding: '0.75rem',
                    textDecoration: 'none',
                    color: 'var(--color-fg)',
                    borderRadius: '0.25rem',
                    transition: 'background var(--transition-speed) ease'
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.05)'}
                  onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                >
                  <Zap size={16} className="text-accent" />
                  Dashboard
                </Link>
                <div style={{ height: '1px', background: 'var(--color-border)', margin: '0.25rem 0' }} />
                <button 
                  onClick={() => {
                    setDropdownOpen(false);
                    signOut();
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    padding: '0.75rem',
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--color-error, #ff4d4f)',
                    cursor: 'pointer',
                    textAlign: 'left',
                    width: '100%',
                    fontFamily: 'inherit',
                    fontWeight: 600,
                    borderRadius: '0.25rem',
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255,77,79,0.1)'}
                  onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                >
                  <LogOut size={16} />
                  Sign Out
                </button>
              </div>
            )}
          </div>
        ) : (
          <button
            id="header-sign-in-btn"
            onClick={openLogin}
            title="Sign in or create account"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
              background: 'var(--color-accent)',
              border: '1px solid var(--color-accent)',
              color: '#fff',
              cursor: 'pointer',
              fontFamily: 'var(--font-display)',
              fontWeight: 700,
              fontSize: '0.75rem',
              letterSpacing: '0.08em',
              padding: '0.5rem 1rem',
              textTransform: 'uppercase',
              transition: 'background var(--transition-speed) ease, box-shadow var(--transition-speed) ease',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'var(--color-accent-hover)';
              e.currentTarget.style.boxShadow = '0 0 16px rgba(255,69,0,0.4)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'var(--color-accent)';
              e.currentTarget.style.boxShadow = 'none';
            }}
          >
            <LogIn size={15} />
            SIGN IN
          </button>
        )}
      </nav>
    </header>
  );
}
