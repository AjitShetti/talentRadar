import MatchAnalyzer from '@/components/MatchAnalyzer';
import Link from 'next/link';
import { Target, Zap } from 'lucide-react';

export default function MatchPage() {
  return (
    <div style={{ position: 'relative' }}>
      {/* Header */}
      <header style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '2rem 0',
        borderBottom: '1px solid var(--color-border)',
        marginBottom: '4rem'
      }}>
        <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Zap className="text-accent" />
          <span style={{ fontSize: '1.25rem', fontWeight: 700, fontFamily: 'var(--font-display)' }}>
            TALENT_RADAR
          </span>
        </Link>
        <nav style={{ display: 'flex', gap: '2rem', alignItems: 'center' }}>
          <Link href="/search">SEARCH</Link>
          <Link href="/trends">TRENDS</Link>
          <Link href="/match" className="text-accent">MATCH ENGINE</Link>
        </nav>
      </header>

      <div style={{ marginBottom: '4rem' }}>
        <h1 style={{ fontSize: '3rem', display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
          <Target className="text-accent" size={48} />
          MATCH ANALYZER
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1.25rem', maxWidth: '800px' }}>
          Input job telemetry and candidate data below. The AI matching engine will calculate the fit score, generate a learning path for missing competencies, and can export a tailored ATS-optimized resume.
        </p>
      </div>

      <MatchAnalyzer />
    </div>
  );
}
