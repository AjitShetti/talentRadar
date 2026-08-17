'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession, signOut } from 'next-auth/react';
import {
  User,
  LogOut,
  History,
  ChevronDown,
  LogIn,
  Menu,
  X,
  Sparkles,
  Bot,
  LayoutDashboard,
  Search,
  FileText,
  Radio,
  Building2,
  BookmarkCheck,
} from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import { useAuthModal } from './AuthModalProvider';

const NAV_ITEMS = [
  { href: '/search', label: 'Search', icon: Search },
  { href: '/resume-studio', label: 'Resume Studio', icon: FileText },
  { href: '/interview', label: 'Interviews', icon: Radio },
  { href: '/applications', label: 'Applications', icon: BookmarkCheck },
  { href: '/company-intel', label: 'Companies', icon: Building2 },
];

export default function Header() {
  const pathname = usePathname();
  const { data: session, status } = useSession();
  const { openLogin } = useAuthModal();
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
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

  // Close mobile menu on route change
  useEffect(() => {
    setMobileMenuOpen(false);
    setDropdownOpen(false);
  }, [pathname]);

  const isActive = (href: string) => {
    if (href === '/') return pathname === '/';
    return pathname.startsWith(href);
  };

  return (
    <header
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 100,
        width: '100%',
        background: 'rgba(9, 10, 15, 0.85)',
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        borderBottom: '1px solid var(--color-border)',
        marginBottom: '2.5rem',
      }}
    >
      <div
        className="container"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          height: '68px',
        }}
      >
        {/* Brand Logo */}
        <Link
          href="/"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.65rem',
            textDecoration: 'none',
            outline: 'none',
          }}
          aria-label="TalentRadar Home"
        >
          {/* Custom SVG Radar Lockup */}
          <div
            style={{
              width: '34px',
              height: '34px',
              borderRadius: '8px',
              background: 'linear-gradient(135deg, rgba(255,87,34,0.18) 0%, rgba(255,87,34,0.06) 100%)',
              border: '1px solid rgba(255,87,34,0.35)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              position: 'relative',
              boxShadow: '0 0 16px rgba(255,87,34,0.15)',
            }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="9" stroke="var(--color-accent)" strokeWidth="1.5" strokeOpacity="0.4" />
              <circle cx="12" cy="12" r="5" stroke="var(--color-accent)" strokeWidth="1.5" strokeOpacity="0.7" />
              <circle cx="12" cy="12" r="2" fill="var(--color-accent)" />
              <path
                d="M12 12L19 5"
                stroke="var(--color-accent)"
                strokeWidth="1.75"
                strokeLinecap="round"
              />
            </svg>
          </div>

          <div style={{ display: 'flex', alignItems: 'baseline', gap: '2px' }}>
            <span
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 800,
                fontSize: '1.125rem',
                letterSpacing: '-0.02em',
                color: '#ffffff',
              }}
            >
              TALENT
            </span>
            <span
              style={{
                fontFamily: 'var(--font-display)',
                fontWeight: 800,
                fontSize: '1.125rem',
                letterSpacing: '-0.02em',
                color: 'var(--color-accent)',
              }}
            >
              RADAR
            </span>
          </div>

          {/* Telemetry live status pill */}
          <div
            className="badge badge-emerald"
            style={{
              marginLeft: '0.4rem',
              fontSize: '0.6875rem',
              padding: '0.15rem 0.5rem',
              display: 'none',
            }}
          >
            <span className="status-dot status-dot-active" />
            <span>LIVE</span>
          </div>
        </Link>

        {/* Desktop Navigation Links */}
        <nav
          style={{
            display: 'none',
            alignItems: 'center',
            gap: '0.35rem',
          }}
          className="desktop-nav"
        >
          {NAV_ITEMS.map((item) => {
            const active = isActive(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                style={{
                  padding: '0.45rem 0.85rem',
                  fontSize: '0.875rem',
                  fontWeight: active ? 600 : 500,
                  color: active ? '#ffffff' : 'var(--color-fg-muted)',
                  background: active ? 'rgba(255, 255, 255, 0.06)' : 'transparent',
                  border: active ? '1px solid var(--color-border-hover)' : '1px solid transparent',
                  borderRadius: 'var(--border-radius-sm)',
                  transition: 'all var(--transition-fast)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.4rem',
                }}
                onMouseEnter={(e) => {
                  if (!active) {
                    e.currentTarget.style.color = '#ffffff';
                    e.currentTarget.style.background = 'rgba(255, 255, 255, 0.04)';
                  }
                }}
                onMouseLeave={(e) => {
                  if (!active) {
                    e.currentTarget.style.color = 'var(--color-fg-muted)';
                    e.currentTarget.style.background = 'transparent';
                  }
                }}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* Auth & Actions */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          {status === 'authenticated' ? (
            <div style={{ position: 'relative' }} ref={dropdownRef}>
              <button
                onClick={() => setDropdownOpen(!dropdownOpen)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                  padding: '0.4rem 0.85rem',
                  borderRadius: 'var(--border-radius-sm)',
                  color: 'var(--color-fg)',
                  cursor: 'pointer',
                  fontSize: '0.8125rem',
                  fontWeight: 600,
                  transition: 'border-color var(--transition-fast)',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--color-border-hover)')}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--color-border)')}
              >
                <div
                  style={{
                    width: '24px',
                    height: '24px',
                    borderRadius: '50%',
                    background: 'var(--color-accent-subtle)',
                    border: '1px solid var(--color-border-accent)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: 'var(--color-accent)',
                  }}
                >
                  <User size={13} />
                </div>
                <span style={{ maxWidth: '110px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {session?.user?.email?.split('@')[0] || 'Account'}
                </span>
                <ChevronDown size={14} style={{ color: 'var(--color-fg-muted)' }} />
              </button>

              {dropdownOpen && (
                <div
                  style={{
                    position: 'absolute',
                    top: 'calc(100% + 8px)',
                    right: 0,
                    background: 'var(--color-surface)',
                    border: '1px solid var(--color-border)',
                    borderRadius: 'var(--border-radius)',
                    padding: '0.4rem',
                    minWidth: '220px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '2px',
                    zIndex: 200,
                    boxShadow: 'var(--shadow-lg)',
                    animation: 'auth-fade-in 150ms ease both',
                  }}
                >
                  <div
                    style={{
                      padding: '0.5rem 0.75rem',
                      borderBottom: '1px solid var(--color-border-subtle)',
                      marginBottom: '0.25rem',
                    }}
                  >
                    <div style={{ fontSize: '0.75rem', color: 'var(--color-fg-subtle)' }}>SIGNED IN AS</div>
                    <div
                      style={{
                        fontSize: '0.8125rem',
                        fontWeight: 600,
                        color: 'var(--color-fg)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {session?.user?.email}
                    </div>
                  </div>

                  <Link
                    href="/dashboard"
                    onClick={() => setDropdownOpen(false)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      padding: '0.55rem 0.75rem',
                      fontSize: '0.8125rem',
                      color: 'var(--color-fg)',
                      borderRadius: 'var(--border-radius-sm)',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.05)')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  >
                    <LayoutDashboard size={15} className="text-accent" />
                    Dashboard
                  </Link>

                  <Link
                    href="/interview/history"
                    onClick={() => setDropdownOpen(false)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      padding: '0.55rem 0.75rem',
                      fontSize: '0.8125rem',
                      color: 'var(--color-fg)',
                      borderRadius: 'var(--border-radius-sm)',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.05)')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  >
                    <History size={15} className="text-accent" />
                    Interview History
                  </Link>

                  <Link
                    href="/agent"
                    onClick={() => setDropdownOpen(false)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      padding: '0.55rem 0.75rem',
                      fontSize: '0.8125rem',
                      color: 'var(--color-fg)',
                      borderRadius: 'var(--border-radius-sm)',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.05)')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  >
                    <Bot size={15} className="text-accent" />
                    Career Agent
                  </Link>

                  <Link
                    href="/onboarding"
                    onClick={() => setDropdownOpen(false)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      padding: '0.55rem 0.75rem',
                      fontSize: '0.8125rem',
                      color: 'var(--color-fg)',
                      borderRadius: 'var(--border-radius-sm)',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.05)')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  >
                    <User size={15} className="text-accent" />
                    My Profile
                  </Link>

                  <div style={{ height: '1px', background: 'var(--color-border-subtle)', margin: '0.25rem 0' }} />

                  <button
                    onClick={() => {
                      setDropdownOpen(false);
                      signOut();
                    }}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                      padding: '0.55rem 0.75rem',
                      background: 'transparent',
                      border: 'none',
                      color: '#f87171',
                      cursor: 'pointer',
                      width: '100%',
                      fontSize: '0.8125rem',
                      fontWeight: 500,
                      borderRadius: 'var(--border-radius-sm)',
                      textAlign: 'left',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--color-error-subtle)')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  >
                    <LogOut size={15} />
                    Sign Out
                  </button>
                </div>
              )}
            </div>
          ) : (
            <button
              id="header-sign-in-btn"
              onClick={openLogin}
              className="btn btn-primary btn-sm"
              title="Sign in or create account"
            >
              <LogIn size={14} />
              Sign In
            </button>
          )}

          {/* Mobile menu toggle */}
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="btn-icon mobile-menu-btn"
            style={{
              display: 'none',
              background: 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              color: 'var(--color-fg)',
              cursor: 'pointer',
            }}
            aria-label={mobileMenuOpen ? 'Close navigation' : 'Open navigation'}
          >
            {mobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>

      {/* Mobile Navigation Drawer */}
      {mobileMenuOpen && (
        <div
          style={{
            borderTop: '1px solid var(--color-border)',
            background: 'rgba(9, 10, 15, 0.98)',
            padding: '1rem 1.5rem 1.5rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.5rem',
          }}
        >
          {NAV_ITEMS.map((item) => {
            const active = isActive(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMobileMenuOpen(false)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.75rem',
                  padding: '0.75rem 1rem',
                  borderRadius: 'var(--border-radius-sm)',
                  fontSize: '0.9375rem',
                  fontWeight: active ? 600 : 500,
                  color: active ? '#ffffff' : 'var(--color-fg-muted)',
                  background: active ? 'rgba(255, 255, 255, 0.08)' : 'transparent',
                }}
              >
                <Icon size={18} className={active ? 'text-accent' : ''} />
                {item.label}
              </Link>
            );
          })}
        </div>
      )}

      <style jsx>{`
        @media (min-width: 768px) {
          .desktop-nav {
            display: flex !important;
          }
          .mobile-menu-btn {
            display: none !important;
          }
        }
        @media (max-width: 767px) {
          .mobile-menu-btn {
            display: flex !important;
          }
        }
      `}</style>
    </header>
  );
}
