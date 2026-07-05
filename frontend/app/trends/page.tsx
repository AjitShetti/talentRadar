'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '@/lib/api';
import { TrendData } from '@/lib/types';
import { TrendingUp, DollarSign, MapPin, Briefcase, Loader2, AlertCircle, BarChart3, Zap } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

function sanitizeText(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

export default function TrendsPage() {
  const [trendData, setTrendData] = useState<TrendData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);

  const abortRef = useRef<AbortController | null>(null);

  const loadTrends = useCallback(async (selectedDays: number) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);

    try {
      const data = await api.trends.get('Market trends and insights', selectedDays, controller.signal);
      setTrendData(data);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message = err instanceof Error ? err.message : 'An unexpected error occurred';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTrends(days);
    return () => {
      abortRef.current?.abort();
    };
  }, [days, loadTrends]);

  return (
    <div>
      {/* Header Info */}
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: '3rem', display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem', fontFamily: 'var(--font-display)', fontWeight: 700 }}>
          <TrendingUp className="text-accent" size={40} />
          MARKET TRENDS
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1.25rem' }}>
          Real-time telemetry and analysis on skill demands, locations, and compensation metrics.
        </p>
      </div>

      {/* Time Range Selector */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '3rem' }}>
        <span style={{ fontSize: '0.875rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', fontWeight: 600 }}>TIME RANGE:</span>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={days === d ? 'btn btn-primary' : 'btn'}
              style={{ padding: '0.5rem 1rem', fontSize: '0.875rem' }}
            >
              {d === 7 ? '7 DAYS' : d === 30 ? '30 DAYS' : '90 DAYS'}
            </button>
          ))}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div style={{ padding: '1rem', border: '1px solid #ef4444', background: 'rgba(239, 68, 68, 0.1)', color: '#fca5a5', display: 'flex', gap: '0.5rem', marginBottom: '2rem' }}>
          <AlertCircle /> {sanitizeText(error)}
        </div>
      )}

      {/* Loading */}
      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '4rem 0', color: 'var(--color-fg-muted)' }}>
          <Loader2 size={48} className="animate-spin text-accent" style={{ marginBottom: '1rem' }} />
          <div style={{ fontFamily: 'var(--font-display)', letterSpacing: '0.1em' }}>AGGREGATING DATA...</div>
        </div>
      ) : trendData ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
          
          {/* AI Summary */}
          {trendData.summary && (
            <div className="panel" style={{ borderLeft: '4px solid var(--color-accent)' }}>
              <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--color-accent)', marginBottom: '1rem', fontFamily: 'var(--font-display)', fontWeight: 700 }}>
                <Zap size={18} />
                AI MARKET ANALYSIS
              </h3>
              <div className="markdown-content" style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', color: 'var(--color-fg)' }}>
                <ReactMarkdown>{trendData.summary}</ReactMarkdown>
              </div>
            </div>
          )}

          {/* Stats Grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1.5rem' }}>
            <div className="panel" style={{ padding: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--color-fg-muted)', marginBottom: '0.5rem', fontSize: '0.875rem' }}>
                <BarChart3 size={16} /> ACTIVE JOBS
              </div>
              <div style={{ fontSize: '2rem', fontFamily: 'var(--font-display)', fontWeight: 700, color: 'var(--color-fg)' }}>
                {trendData.total_jobs.toLocaleString()}
              </div>
            </div>

            <div className="panel" style={{ padding: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--color-fg-muted)', marginBottom: '0.5rem', fontSize: '0.875rem' }}>
                <DollarSign size={16} /> AVG SALARY
              </div>
              <div style={{ fontSize: '1.5rem', fontFamily: 'var(--font-display)', fontWeight: 700, color: 'var(--color-fg)' }}>
                {trendData.salary_data?.available
                  ? `$${Math.round(trendData.salary_data.avg_min || 0).toLocaleString()} - $${Math.round(trendData.salary_data.avg_max || 0).toLocaleString()}`
                  : 'N/A'}
              </div>
            </div>

            <div className="panel" style={{ padding: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--color-fg-muted)', marginBottom: '0.5rem', fontSize: '0.875rem' }}>
                <MapPin size={16} /> TOP LOCATION
              </div>
              <div style={{ fontSize: '1.5rem', fontFamily: 'var(--font-display)', fontWeight: 700, color: 'var(--color-fg)', wordBreak: 'break-word' }}>
                {trendData.location_data[0]?.location || 'N/A'}
              </div>
            </div>

            <div className="panel" style={{ padding: '1.5rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--color-fg-muted)', marginBottom: '0.5rem', fontSize: '0.875rem' }}>
                <Briefcase size={16} /> TOP SKILL
              </div>
              <div style={{ fontSize: '1.5rem', fontFamily: 'var(--font-display)', fontWeight: 700, color: 'var(--color-fg)', wordBreak: 'break-word' }}>
                {trendData.top_skills[0]?.skill || 'N/A'}
              </div>
            </div>
          </div>

          {/* Skills & Locations */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1.5rem' }}>
            
            {/* Top Skills */}
            <div className="panel">
              <h3 style={{ marginBottom: '1.5rem', fontSize: '1.25rem', fontFamily: 'var(--font-display)', fontWeight: 700 }}>
                MOST IN-DEMAND SKILLS
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                {trendData.top_skills.slice(0, 10).map((item) => {
                  const maxCount = trendData.top_skills[0]?.count || 1;
                  const percentage = (item.count / maxCount) * 100;
                  return (
                    <div key={item.skill}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem', fontSize: '0.875rem' }}>
                        <span style={{ color: 'var(--color-fg)' }}>{sanitizeText(item.skill)}</span>
                        <span style={{ color: 'var(--color-fg-muted)' }}>{item.count}</span>
                      </div>
                      <div style={{ width: '100%', height: '4px', background: 'var(--color-border)' }}>
                        <div style={{ height: '100%', background: 'var(--color-accent)', width: `${percentage}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Locations */}
            <div className="panel">
              <h3 style={{ marginBottom: '1.5rem', fontSize: '1.25rem', fontFamily: 'var(--font-display)', fontWeight: 700 }}>
                JOB DISTRIBUTION BY LOCATION
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                {trendData.location_data.slice(0, 10).map((item) => {
                  const maxCount = trendData.location_data[0]?.count || 1;
                  const percentage = (item.count / maxCount) * 100;
                  return (
                    <div key={item.location}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem', fontSize: '0.875rem' }}>
                        <span style={{ color: 'var(--color-fg)' }}>{sanitizeText(item.location)}</span>
                        <span style={{ color: 'var(--color-fg-muted)' }}>{item.count}</span>
                      </div>
                      <div style={{ width: '100%', height: '4px', background: 'var(--color-border)' }}>
                        <div style={{ height: '100%', background: 'var(--color-accent)', width: `${percentage}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

          </div>
        </div>
      ) : (
        <div style={{ textAlign: 'center', padding: '4rem 0', color: 'var(--color-border)' }}>
          <TrendingUp size={64} style={{ margin: '0 auto 1rem auto', opacity: 0.5 }} />
          <p style={{ fontFamily: 'var(--font-display)', letterSpacing: '0.1em' }}>NO TREND DATA AVAILABLE</p>
        </div>
      )}
    </div>
  );
}
