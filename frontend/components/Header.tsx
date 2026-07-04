'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Zap } from 'lucide-react';

export default function Header() {
  const pathname = usePathname();

  return (
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
      </nav>
    </header>
  );
}
