'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession, signOut } from 'next-auth/react';
import { Zap, User, LogOut, History, ChevronDown } from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import AuthModal from './AuthModal';

export default function Header() {
  const pathname = usePathname();
  const { status } = useSession();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [authModalOpen, setAuthModalOpen] = useState(false);
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

  return (
    <>
      <AuthModal isOpen={authModalOpen} onClose={() => setAuthModalOpen(false)} initialMode="login" />
    <header style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '1.5rem 0',
      borderBottom: '1px solid var(--color-border)',
      marginBottom: '3rem'
    }}>
      <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', textDecoration: 'none' }}>
        <Zap className="text-accent" />
        <span style={{ fontSize: '1.25rem', fontWeight: 700, fontFamily: 'var(--font-display)', color: 'var(--color-fg)' }}>
          TALENT_RADAR
        </span>
      </Link>
      <nav style={{ display: 'flex', gap: '2rem', alignItems: 'center', fontFamily: 'var(--font-display)', fontWeight: 600 }}>
        <Link 
          href="/search" 
          style={{ textDecoration: 'none', color: pathname === '/search' ? 'var(--color-accent)' : 'var(--color-fg-muted)', transition: 'color var(--transition-speed) ease' }}
        >
          SEARCH
        </Link>
        <Link 
          href="/trends" 
          style={{ textDecoration: 'none', color: pathname === '/trends' ? 'var(--color-accent)' : 'var(--color-fg-muted)', transition: 'color var(--transition-speed) ease' }}
        >
          TRENDS
        </Link>
        <Link 
          href="/match" 
          style={{ textDecoration: 'none', color: pathname === '/match' ? 'var(--color-accent)' : 'var(--color-fg-muted)', transition: 'color var(--transition-speed) ease' }}
        >
          MATCH ENGINE
        </Link>
        <Link 
          href="/interview" 
          style={{ textDecoration: 'none', color: pathname.startsWith('/interview') ? 'var(--color-accent)' : 'var(--color-fg-muted)', transition: 'color var(--transition-speed) ease' }}
        >
          INTERVIEWS
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
            onClick={() => setAuthModalOpen(true)}
            style={{ 
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--color-accent)',
              display: 'flex',
              alignItems: 'center',
              gap: '0.5rem',
              fontFamily: 'inherit',
              fontWeight: 600,
              fontSize: '1rem',
              padding: 0
            }}
          >
            <User size={18} />
            SIGN IN
          </button>
        )}
      </nav>
    </header>
    </>
  );
}
