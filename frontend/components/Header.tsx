'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';
import { useSession, signOut } from 'next-auth/react';
import { List, X, SignOut, User } from '@phosphor-icons/react';
import { useAuthModal } from '@/components/AuthModalProvider';

const NAV_LINKS = [
  { href: '/search', label: 'Search' },
  { href: '/resume-studio', label: 'Resume' },
  { href: '/interview', label: 'Interview' },
  { href: '/applications', label: 'Applications' },
  { href: '/company-intel', label: 'Companies' },
];

export default function Header() {
  const pathname = usePathname();
  const { data: session } = useSession();
  const { openLogin } = useAuthModal();
  const [menuOpen, setMenuOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);

  const isActive = (href: string) => pathname === href || pathname.startsWith(href + '/');

  return (
    <>
      <header
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 50,
          height: '64px',
          background: 'rgba(250,250,250,0.85)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <div
          style={{
            maxWidth: '1280px',
            margin: '0 auto',
            padding: '0 1.5rem',
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            gap: '2rem',
          }}
        >
          {/* Wordmark */}
          <Link
            href="/"
            style={{
              fontSize: '1.0625rem',
              fontWeight: 600,
              color: 'var(--text)',
              letterSpacing: '-0.04em',
              flexShrink: 0,
              textDecoration: 'none',
            }}
          >
            TalentRadar
          </Link>

          {/* Desktop Nav */}
          <nav
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.25rem',
              flex: 1,
            }}
            className="desktop-nav"
          >
            {NAV_LINKS.map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                style={{
                  padding: '0.375rem 0.625rem',
                  borderRadius: 'var(--radius-sm)',
                  fontSize: '0.875rem',
                  fontWeight: isActive(href) ? 500 : 400,
                  color: isActive(href) ? 'var(--text)' : 'var(--text-muted)',
                  background: 'transparent',
                  borderBottom: isActive(href) ? '2px solid var(--accent)' : '2px solid transparent',
                  transition: 'color 150ms ease, border-color 150ms ease',
                  textDecoration: 'none',
                  paddingBottom: '0.25rem',
                }}
              >
                {label}
              </Link>
            ))}
          </nav>

          {/* Auth Controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexShrink: 0 }}>
            {session ? (
              <div style={{ position: 'relative' }}>
                <button
                  onClick={() => setProfileOpen((o) => !o)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    padding: '0.375rem 0.75rem 0.375rem 0.5rem',
                    border: '1px solid var(--border)',
                    borderRadius: 'var(--radius-full)',
                    background: 'var(--surface)',
                    cursor: 'pointer',
                    fontSize: '0.8125rem',
                    color: 'var(--text)',
                    transition: 'border-color 150ms ease',
                  }}
                >
                  <span
                    style={{
                      width: '24px',
                      height: '24px',
                      borderRadius: '50%',
                      background: 'var(--accent)',
                      color: '#fff',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '0.6875rem',
                      fontWeight: 600,
                      flexShrink: 0,
                    }}
                  >
                    {(session.user?.name || session.user?.email || 'U')[0].toUpperCase()}
                  </span>
                  <span>{session.user?.name?.split(' ')[0] || session.user?.email?.split('@')[0] || 'Account'}</span>
                </button>

                {profileOpen && (
                  <>
                    <div
                      style={{ position: 'fixed', inset: 0, zIndex: 10 }}
                      onClick={() => setProfileOpen(false)}
                    />
                    <div
                      style={{
                        position: 'absolute',
                        right: 0,
                        top: 'calc(100% + 8px)',
                        minWidth: '200px',
                        background: 'var(--surface)',
                        border: '1px solid var(--border)',
                        borderRadius: 'var(--radius)',
                        boxShadow: 'var(--shadow-lg)',
                        zIndex: 20,
                        overflow: 'hidden',
                      }}
                    >
                      {[
                        { href: '/dashboard', label: 'Dashboard' },
                        { href: '/agent', label: 'Career Agent' },
                        { href: '/onboarding', label: 'Profile' },
                        { href: '/interview/history', label: 'Interview History' },
                      ].map(({ href, label }) => (
                        <Link
                          key={href}
                          href={href}
                          onClick={() => setProfileOpen(false)}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '0.5rem',
                            padding: '0.625rem 1rem',
                            fontSize: '0.875rem',
                            color: 'var(--text)',
                            transition: 'background 100ms',
                          }}
                          onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-subtle)')}
                          onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                        >
                          <User size={14} style={{ color: 'var(--text-muted)' }} />
                          {label}
                        </Link>
                      ))}
                      <div style={{ borderTop: '1px solid var(--border)' }} />
                      <button
                        onClick={() => { setProfileOpen(false); signOut(); }}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '0.5rem',
                          padding: '0.625rem 1rem',
                          fontSize: '0.875rem',
                          color: 'var(--error)',
                          width: '100%',
                          background: 'transparent',
                          border: 'none',
                          cursor: 'pointer',
                          textAlign: 'left',
                          transition: 'background 100ms',
                        }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-subtle)')}
                        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                      >
                        <SignOut size={14} />
                        Sign out
                      </button>
                    </div>
                  </>
                )}
              </div>
            ) : (
              <>
                <button
                  onClick={openLogin}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    fontSize: '0.875rem',
                    color: 'var(--text-muted)',
                    padding: '0.375rem 0.5rem',
                  }}
                  className="desktop-nav"
                >
                  Sign in
                </button>
                <button
                  onClick={openLogin}
                  style={{
                    padding: '0.4375rem 1rem',
                    background: 'var(--accent)',
                    color: '#fff',
                    border: 'none',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '0.875rem',
                    fontWeight: 500,
                    cursor: 'pointer',
                    transition: 'background 150ms ease',
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--accent-hover)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--accent)')}
                >
                  Get started
                </button>
              </>
            )}

            {/* Mobile Hamburger */}
            <button
              onClick={() => setMenuOpen((o) => !o)}
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: 'var(--text-muted)',
                padding: '0.25rem',
                display: 'none',
              }}
              className="mobile-menu-btn"
              aria-label="Toggle menu"
            >
              {menuOpen ? <X size={22} /> : <List size={22} />}
            </button>
          </div>
        </div>
      </header>

      {/* Mobile Drawer */}
      {menuOpen && (
        <div
          style={{
            position: 'fixed',
            inset: '64px 0 0 0',
            background: 'var(--surface)',
            borderTop: '1px solid var(--border)',
            zIndex: 40,
            padding: '1rem 1.5rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.25rem',
          }}
        >
          {NAV_LINKS.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              onClick={() => setMenuOpen(false)}
              style={{
                padding: '0.75rem 1rem',
                borderRadius: 'var(--radius-sm)',
                fontSize: '1rem',
                fontWeight: isActive(href) ? 500 : 400,
                color: isActive(href) ? 'var(--text)' : 'var(--text-muted)',
                background: isActive(href) ? 'var(--bg-subtle)' : 'transparent',
              }}
            >
              {label}
            </Link>
          ))}
          {!session && (
            <button
              onClick={() => { setMenuOpen(false); openLogin(); }}
              style={{
                marginTop: '1rem',
                padding: '0.75rem',
                background: 'var(--accent)',
                color: '#fff',
                border: 'none',
                borderRadius: 'var(--radius-sm)',
                fontSize: '1rem',
                fontWeight: 500,
                cursor: 'pointer',
              }}
            >
              Sign in / Get started
            </button>
          )}
        </div>
      )}

      <style>{`
        @media (max-width: 768px) {
          .desktop-nav { display: none !important; }
          .mobile-menu-btn { display: flex !important; }
        }
      `}</style>
    </>
  );
}
