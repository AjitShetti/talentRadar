'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useSession, signOut } from 'next-auth/react';
import { Zap, LogOut, LogIn } from 'lucide-react';
import { useAuthModal } from './AuthModalProvider';

export default function Header() {
  const pathname = usePathname();
  const { status } = useSession();
  const { openLogin } = useAuthModal();

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

        {status === 'authenticated' ? (
          <button
            onClick={() => signOut()}
            title="Sign out"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
              textDecoration: 'none',
              color: 'var(--color-fg-muted)',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              fontFamily: 'inherit',
              fontWeight: 'inherit',
              fontSize: 'inherit',
            }}
          >
            <LogOut size={15} />
            SIGN OUT
          </button>
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
