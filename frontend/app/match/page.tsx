import MatchAnalyzer from '@/components/MatchAnalyzer';
import Link from 'next/link';
import { Target, Zap } from 'lucide-react';

export default function MatchPage() {
  return (
    <div style={{ position: 'relative' }}>
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
