'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { api } from '@/lib/api';
import { Job } from '@/lib/types';
import SearchBar from '@/components/SearchBar';
import JobCard from '@/components/JobCard';
import { AlertCircle, CheckCircle2, Clock, Globe2, Loader2, RefreshCw, Radio } from 'lucide-react';

const PAGE_SIZE = 20;

interface SourceStat {
  latency_ms: number;
  count: number;
  status: string;
}

export default function SearchPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [totalFound, setTotalFound] = useState(0);
  const [offset, setOffset] = useState(0);
  const [currentQuery, setCurrentQuery] = useState('');
  const [hasMore, setHasMore] = useState(false);
  const [isFiltered, setIsFiltered] = useState(false);
  const [isCached, setIsCached] = useState(false);
  const [sourcesStats, setSourcesStats] = useState<Record<string, SourceStat>>({});
  const [activeSources, setActiveSources] = useState<string[]>([]);

  // Ref to track the active EventSource or AbortController
  const eventSourceRef = useRef<EventSource | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Clean up any streaming connections on unmount
  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
      abortRef.current?.abort();
    };
  }, []);

  // ── Load default structured jobs on first render ──────────
  const loadAllJobs = useCallback(async (isLoadMore = false) => {
    eventSourceRef.current?.close();
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    if (!isLoadMore) {
      setLoading(true);
      setError(null);
      setCurrentQuery('');
      setIsFiltered(false);
      setIsCached(false);
      setSourcesStats({});
      setJobs([]);
      setOffset(0);
    } else {
      setLoadingMore(true);
    }

    const currentOffset = isLoadMore ? offset : 0;

    try {
      const response = await api.search.structured(
        { limit: PAGE_SIZE, offset: currentOffset },
        controller.signal
      );

      if (isLoadMore) {
        setJobs((prev) => [...prev, ...response.jobs]);
        setOffset(currentOffset + PAGE_SIZE);
      } else {
        setJobs(response.jobs);
        setOffset(PAGE_SIZE);
      }

      setTotalFound(response.total);
      setHasMore(response.has_more);
    } catch (err) {
      if (controller.signal.aborted) return;
      setError(err instanceof Error ? err.message : 'Failed to load jobs.');
      if (!isLoadMore) setJobs([]);
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [offset]);

  useEffect(() => {
    loadAllJobs(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Real-time Multi-Source Streaming Search ──────────
  const handleRealtimeStreamSearch = useCallback((query: string, forceRefresh = false) => {
    eventSourceRef.current?.close();
    abortRef.current?.abort();

    setLoading(true);
    setError(null);
    setJobs([]);
    setSourcesStats({});
    setActiveSources([]);
    setCurrentQuery(query);
    setIsFiltered(true);
    setIsCached(false);

    const streamUrl = api.search.getStreamUrl({
      query,
      location: 'India',
      force_refresh: forceRefresh,
    });

    const es = new EventSource(streamUrl);
    eventSourceRef.current = es;

    es.addEventListener('init', (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.sources) setActiveSources(payload.sources);
      } catch (err) {
        console.error('Failed to parse init event', err);
      }
    });

    es.addEventListener('cached', (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        setIsCached(true);
        if (payload.jobs) {
          setJobs(payload.jobs);
          setTotalFound(payload.total || payload.jobs.length);
        }
        if (payload.sources_stats) {
          setSourcesStats(payload.sources_stats);
        }
      } catch (err) {
        console.error('Failed to parse cached event', err);
      }
    });

    es.addEventListener('chunk', (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        const sourceName = payload.source;

        setSourcesStats((prev) => ({
          ...prev,
          [sourceName]: {
            latency_ms: payload.latency_ms,
            count: payload.count,
            status: payload.status,
          },
        }));

        if (payload.jobs && payload.jobs.length > 0) {
          setJobs((prev) => {
            const existingIds = new Set(prev.map((j) => j.id));
            const newUniqueJobs = payload.jobs.filter((j: Job) => !existingIds.has(j.id));
            return [...prev, ...newUniqueJobs];
          });
        }
      } catch (err) {
        console.error('Failed to parse chunk event', err);
      }
    });

    es.addEventListener('complete', (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.sources_stats) {
          setSourcesStats(payload.sources_stats);
        }
      } catch (err) {
        console.error('Failed to parse complete event', err);
      }
      setLoading(false);
      es.close();
    });

    es.addEventListener('error', () => {
      setLoading(false);
      es.close();
    });
  }, []);

  const handleSearch = useCallback((query: string) => {
    if (!query.trim()) {
      loadAllJobs(false);
      return;
    }
    handleRealtimeStreamSearch(query, false);
  }, [loadAllJobs, handleRealtimeStreamSearch]);

  const handleForceRefresh = useCallback(() => {
    if (currentQuery) {
      handleRealtimeStreamSearch(currentQuery, true);
    }
  }, [currentQuery, handleRealtimeStreamSearch]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      {/* Header Info */}
      <div>
        <div className="badge badge-accent" style={{ marginBottom: '0.75rem' }}>
          <span className="status-dot status-dot-active" />
          <span>REAL-TIME AGGREGATION</span>
        </div>
        <h1 style={{ fontSize: '2.25rem', marginBottom: '0.4rem' }}>
          Real-Time Job Telemetry
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1rem', maxWidth: '65ch' }}>
          On-demand job aggregation across Greenhouse, Lever, Ashby, LinkedIn, and specialized tech boards.
        </p>
      </div>

      {/* Search Bar */}
      <div>
        <SearchBar
          onSearch={handleSearch}
          loading={loading}
          placeholder="e.g. Senior Frontend Engineer, Python AI Specialist, DevOps Lead..."
        />
      </div>

      {/* Real-time Sources Monitor Bar */}
      {isFiltered && (
        <div
          className="glass-panel"
          style={{
            padding: '1.15rem 1.25rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.85rem',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem', fontSize: '0.8125rem', fontWeight: 600, color: 'var(--color-fg-muted)' }}>
              <Globe2 size={15} className="text-accent" />
              <span>DATA SOURCES MONITORED:</span>
              {isCached && (
                <span className="badge badge-emerald" style={{ fontSize: '0.6875rem' }}>
                  <Clock size={11} /> 8H CACHE HIT
                </span>
              )}
            </div>
            {isFiltered && !loading && (
              <button
                onClick={handleForceRefresh}
                className="btn btn-secondary btn-sm"
                style={{ fontSize: '0.75rem', padding: '0.3rem 0.65rem' }}
              >
                <RefreshCw size={12} /> Live Re-scrape
              </button>
            )}
          </div>

          {/* Source Badges */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
            {['ats_platforms', 'linkedin', 'foundit', 'freshersworld', 'naukri', 'indeed_india'].map((src) => {
              const stat = sourcesStats[src];
              const isDone = Boolean(stat);
              const label = src.replace('_', ' ').toUpperCase();

              return (
                <div
                  key={src}
                  className="badge"
                  style={{
                    padding: '0.3rem 0.65rem',
                    fontSize: '0.75rem',
                    background: isDone
                      ? (stat.status === 'success' ? 'var(--color-info-subtle)' : 'var(--color-error-subtle)')
                      : 'var(--color-surface)',
                    borderColor: isDone
                      ? (stat.status === 'success' ? 'rgba(6, 182, 212, 0.3)' : 'rgba(239, 68, 68, 0.3)')
                      : 'var(--color-border)',
                    color: isDone
                      ? (stat.status === 'success' ? '#22d3ee' : '#f87171')
                      : 'var(--color-fg-muted)',
                  }}
                >
                  {!isDone && loading ? (
                    <Loader2 size={11} className="status-dot-pulse" />
                  ) : isDone && stat.status === 'success' ? (
                    <CheckCircle2 size={12} style={{ color: '#10b981' }} />
                  ) : (
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor' }} />
                  )}
                  <span>{label}</span>
                  {stat && (
                    <span style={{ opacity: 0.8, fontSize: '0.6875rem', fontFamily: 'var(--font-mono)' }}>
                      ({stat.count} jobs · {stat.latency_ms}ms)
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="auth-error" style={{ marginBottom: 0 }}>
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}

      {/* Results Count */}
      {!loading && (
        <div style={{ fontSize: '0.875rem', color: 'var(--color-fg-muted)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          {isFiltered ? (
            <span>
              Found <strong style={{ color: 'var(--color-fg)' }}>{jobs.length}</strong> live opening{jobs.length !== 1 ? 's' : ''} for <span className="text-accent">&ldquo;{currentQuery}&rdquo;</span>
            </span>
          ) : (
            <span>
              Showing <strong style={{ color: 'var(--color-fg)' }}>{jobs.length}</strong> of {totalFound} total indexed openings
            </span>
          )}
        </div>
      )}

      {/* Loading Indicator */}
      {loading && jobs.length === 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '5rem 0', color: 'var(--color-fg-muted)' }}>
          <div
            style={{
              width: '48px',
              height: '48px',
              borderRadius: '50%',
              border: '3px solid var(--color-border)',
              borderTopColor: 'var(--color-accent)',
              animation: 'spin 1s linear infinite',
              marginBottom: '1rem',
            }}
          />
          <div style={{ fontSize: '0.9375rem', fontWeight: 600, color: 'var(--color-fg)' }}>
            Streaming live job postings...
          </div>
        </div>
      )}

      {/* Job List */}
      {jobs.length > 0 && (
        <div style={{ display: 'grid', gap: '1.25rem' }}>
          {jobs.map((job) => (
            <JobCard key={job.id} job={job} showScore={false} />
          ))}
        </div>
      )}

      {/* Load More for structured search */}
      {!isFiltered && hasMore && !loading && (
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: '1rem' }}>
          <button
            onClick={() => loadAllJobs(true)}
            disabled={loadingMore}
            className="btn btn-secondary btn-lg"
          >
            {loadingMore ? (
              <>
                <Loader2 size={16} className="status-dot-pulse" />
                <span>Loading More Openings...</span>
              </>
            ) : (
              <span>Load More Jobs</span>
            )}
          </button>
        </div>
      )}

      {/* Empty State */}
      {!loading && jobs.length === 0 && !error && isFiltered && (
        <div className="panel" style={{ textAlign: 'center', padding: '4rem 1rem', color: 'var(--color-fg-subtle)' }}>
          <Radio size={48} style={{ margin: '0 auto 1rem auto', opacity: 0.3 }} />
          <h3 style={{ color: 'var(--color-fg)', marginBottom: '0.5rem' }}>No openings found</h3>
          <p style={{ fontSize: '0.875rem' }}>Try searching with a broader title, different keyword, or re-scrape.</p>
        </div>
      )}

      <style jsx>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
