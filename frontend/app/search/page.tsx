'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { api } from '@/lib/api';
import { Job } from '@/lib/types';
import SearchBar from '@/components/SearchBar';
import JobCard from '@/components/JobCard';
import { AlertCircle, CheckCircle2, Clock, Globe2, Loader2, RefreshCw, Zap } from 'lucide-react';

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
  const [searchMode, setSearchMode] = useState<'realtime' | 'semantic'>('realtime');

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
            const newUnique = payload.jobs.filter((j: Job) => !existingIds.has(j.id));
            const updated = [...prev, ...newUnique];
            setTotalFound(updated.length);
            return updated;
          });
        }
      } catch (err) {
        console.error('Failed to parse chunk event', err);
      }
    });

    es.addEventListener('done', (e: MessageEvent) => {
      setLoading(false);
      es.close();
    });

    es.addEventListener('error', (e: Event) => {
      console.warn('SSE stream closed or error occurred');
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
    <div>
      {/* Header Info */}
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: '2.5rem', display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '0.5rem', fontFamily: 'var(--font-display)', fontWeight: 700 }}>
          <Zap className="text-accent" size={36} />
          REAL-TIME TALENT RADAR
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1.125rem' }}>
          Live on-demand job aggregation across top ATS portals, Indian tech boards, and worldwide remote roles.
        </p>
      </div>

      {/* Search Bar */}
      <div style={{ marginBottom: '1.5rem' }}>
        <SearchBar
          onSearch={handleSearch}
          loading={loading}
          placeholder="e.g. Python Developer, Full Stack React, DevOps Engineer"
        />
      </div>

      {/* Real-time Sources Monitor Bar */}
      {isFiltered && (
        <div style={{
          marginBottom: '2rem',
          padding: '1rem 1.25rem',
          background: 'var(--color-bg-subtle, rgba(255, 255, 255, 0.03))',
          border: '1px solid var(--color-border, rgba(255, 255, 255, 0.1))',
          borderRadius: '8px',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.75rem',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.875rem', fontWeight: 600, color: 'var(--color-fg-muted)' }}>
              <Globe2 size={16} className="text-accent" />
              <span>DATA SOURCES MONITORED:</span>
              {isCached && (
                <span style={{
                  background: 'rgba(34, 197, 94, 0.15)',
                  color: '#4ade80',
                  padding: '2px 8px',
                  borderRadius: '4px',
                  fontSize: '0.75rem',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px'
                }}>
                  <Clock size={12} /> 8H CACHE HIT
                </span>
              )}
            </div>
            {isFiltered && !loading && (
              <button
                onClick={handleForceRefresh}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.35rem',
                  fontSize: '0.75rem',
                  background: 'transparent',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-fg)',
                  padding: '4px 10px',
                  borderRadius: '4px',
                  cursor: 'pointer',
                }}
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
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.4rem',
                    padding: '4px 10px',
                    borderRadius: '4px',
                    fontSize: '0.75rem',
                    background: isDone
                      ? (stat.status === 'success' ? 'rgba(59, 130, 246, 0.1)' : 'rgba(239, 68, 68, 0.1)')
                      : 'rgba(255, 255, 255, 0.05)',
                    border: `1px solid ${isDone ? (stat.status === 'success' ? 'rgba(59, 130, 246, 0.3)' : 'rgba(239, 68, 68, 0.3)') : 'transparent'}`,
                    color: isDone ? (stat.status === 'success' ? '#93c5fd' : '#fca5a5') : 'var(--color-fg-muted)',
                  }}
                >
                  {!isDone && loading ? (
                    <Loader2 size={12} className="animate-spin text-accent" />
                  ) : isDone && stat.status === 'success' ? (
                    <CheckCircle2 size={12} style={{ color: '#4ade80' }} />
                  ) : (
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor' }} />
                  )}
                  <span>{label}</span>
                  {stat && (
                    <span style={{ opacity: 0.75, fontSize: '0.7rem' }}>
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
        <div style={{ padding: '1rem', border: '1px solid #ef4444', background: 'rgba(239, 68, 68, 0.1)', color: '#fca5a5', display: 'flex', gap: '0.5rem', marginBottom: '2rem' }}>
          <AlertCircle /> {error}
        </div>
      )}

      {/* Results Count */}
      {!loading && totalFound > 0 && (
        <div style={{ marginBottom: '1.5rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', fontWeight: 600 }}>
          {isFiltered
            ? <>{jobs.length} REAL-TIME MATCH{jobs.length !== 1 ? 'ES' : ''} FOR &ldquo;<span style={{ color: 'var(--color-accent)' }}>{currentQuery}</span>&rdquo;</>
            : <>SHOWING <span style={{ color: 'var(--color-fg)' }}>{jobs.length}</span> OF <span style={{ color: 'var(--color-fg)' }}>{totalFound}</span> JOBS</>}
        </div>
      )}

      {/* Loading Indicator */}
      {loading && jobs.length === 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '4rem 0', color: 'var(--color-fg-muted)' }}>
          <Loader2 size={48} className="animate-spin text-accent" style={{ marginBottom: '1rem' }} />
          <div style={{ fontFamily: 'var(--font-display)', letterSpacing: '0.1em' }}>AGGREGATING LIVE JOB POSTINGS...</div>
        </div>
      )}

      {/* Job List */}
      {jobs.length > 0 && (
        <div style={{ display: 'grid', gap: '1.5rem' }}>
          {jobs.map((job) => (
            <JobCard key={job.id} job={job} showScore={false} />
          ))}
        </div>
      )}

      {/* Empty State */}
      {!loading && jobs.length === 0 && !error && isFiltered && (
        <div style={{ textAlign: 'center', padding: '4rem 0', color: 'var(--color-border)' }}>
          <Zap size={64} style={{ margin: '0 auto 1rem auto', opacity: 0.5 }} />
          <p style={{ fontFamily: 'var(--font-display)', letterSpacing: '0.1em' }}>NO ACTIVE OPENINGS FOUND ACROSS TARGET BOARDS</p>
        </div>
      )}
    </div>
  );
}

