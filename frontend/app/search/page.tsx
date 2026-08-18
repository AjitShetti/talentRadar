'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { api } from '@/lib/api';
import { Job } from '@/lib/types';
import SearchBar from '@/components/SearchBar';
import JobCard from '@/components/JobCard';
import { ArrowsClockwise, WarningCircle } from '@phosphor-icons/react';

const PAGE_SIZE = 20;

interface SourceStat {
  latency_ms: number;
  count: number;
  status: string;
}

export default function SearchPage() {
  const [query, setQuery] = useState('');
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

  const eventSourceRef = useRef<EventSource | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
      abortRef.current?.abort();
    };
  }, []);

  const loadAllJobs = useCallback(async (isLoadMore = false) => {
    eventSourceRef.current?.close();
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    if (!isLoadMore) {
      setLoading(true); setError(null); setCurrentQuery('');
      setIsFiltered(false); setIsCached(false); setSourcesStats({});
      setJobs([]); setOffset(0);
    } else {
      setLoadingMore(true);
    }

    const currentOffset = isLoadMore ? offset : 0;
    try {
      const response = await api.search.structured({ limit: PAGE_SIZE, offset: currentOffset }, controller.signal);
      if (isLoadMore) {
        setJobs((prev) => [...prev, ...response.jobs]);
        setOffset(currentOffset + PAGE_SIZE);
      } else {
        setJobs(response.jobs); setOffset(PAGE_SIZE);
      }
      setTotalFound(response.total);
      setHasMore(response.has_more);
    } catch (err) {
      if (controller.signal.aborted) return;
      setError(err instanceof Error ? err.message : 'Failed to load jobs.');
      if (!isLoadMore) setJobs([]);
    } finally {
      setLoading(false); setLoadingMore(false);
    }
  }, [offset]);

  useEffect(() => { loadAllJobs(false); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRealtimeStreamSearch = useCallback((q: string, forceRefresh = false) => {
    eventSourceRef.current?.close();
    abortRef.current?.abort();
    setLoading(true); setError(null); setJobs([]);
    setSourcesStats({}); setCurrentQuery(q); setIsFiltered(true); setIsCached(false);

    const es = new EventSource(api.search.getStreamUrl({ query: q, location: 'India', force_refresh: forceRefresh }));
    eventSourceRef.current = es;

    es.addEventListener('cached', (e: MessageEvent) => {
      try {
        const p = JSON.parse(e.data);
        setIsCached(true);
        if (p.jobs) { setJobs(p.jobs); setTotalFound(p.total || p.jobs.length); }
        if (p.sources_stats) setSourcesStats(p.sources_stats);
      } catch { /* ignore parse errors */ }
    });

    es.addEventListener('chunk', (e: MessageEvent) => {
      try {
        const p = JSON.parse(e.data);
        setSourcesStats((prev) => ({ ...prev, [p.source]: { latency_ms: p.latency_ms, count: p.count, status: p.status } }));
        if (p.jobs?.length > 0) {
          setJobs((prev) => {
            const seen = new Set(prev.map((j) => j.id));
            return [...prev, ...p.jobs.filter((j: Job) => !seen.has(j.id))];
          });
        }
      } catch { /* ignore parse errors */ }
    });

    es.addEventListener('complete', (e: MessageEvent) => {
      try { const p = JSON.parse(e.data); if (p.sources_stats) setSourcesStats(p.sources_stats); } catch { /* ignore */ }
      setLoading(false); es.close();
    });

    es.addEventListener('error', () => { setLoading(false); es.close(); });
  }, []);

  const handleSearch = useCallback((q: string) => {
    if (!q.trim()) { loadAllJobs(false); return; }
    handleRealtimeStreamSearch(q, false);
  }, [loadAllJobs, handleRealtimeStreamSearch]);

  const sourceEntries = Object.entries(sourcesStats);

  return (
    <div style={{ paddingTop: '2.5rem', display: 'flex', flexDirection: 'column', gap: '1.75rem' }}>
      {/* Page header */}
      <div>
        <h1 style={{ fontSize: '1.75rem', fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--text)', marginBottom: '0.35rem' }}>
          Job search
        </h1>
        <p style={{ fontSize: '0.9375rem', color: 'var(--text-muted)' }}>
          Live results from Greenhouse, Lever, Ashby, LinkedIn, and Naukri.
        </p>
      </div>

      {/* Search bar + streaming progress */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        <SearchBar value={query} onChange={setQuery} onSearch={() => handleSearch(query)} loading={loading} />

        {/* Slim progress bar while streaming */}
        {loading && isFiltered && (
          <div style={{ height: '2px', background: 'var(--border)', borderRadius: '99px', overflow: 'hidden' }}>
            <div
              style={{
                height: '100%',
                background: 'var(--accent)',
                borderRadius: '99px',
                animation: 'progress-indeterminate 1.5s ease-in-out infinite',
                width: '40%',
              }}
            />
          </div>
        )}

        {/* Source stats (only when streaming search active) */}
        {isFiltered && sourceEntries.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
            {sourceEntries.map(([name, stat]) => (
              <span key={name} style={{ fontSize: '0.75rem', color: stat.status === 'ok' ? 'var(--success)' : 'var(--text-subtle)' }}>
                {name}: {stat.count} results
              </span>
            ))}
            {isCached && (
              <span style={{ fontSize: '0.75rem', color: 'var(--text-subtle)' }}>Cached results</span>
            )}
            {!loading && currentQuery && (
              <button
                onClick={() => handleRealtimeStreamSearch(currentQuery, true)}
                style={{
                  display: 'flex', alignItems: 'center', gap: '0.25rem',
                  background: 'transparent', border: 'none', cursor: 'pointer',
                  fontSize: '0.75rem', color: 'var(--text-muted)',
                  padding: 0,
                }}
              >
                <ArrowsClockwise size={13} /> Refresh
              </button>
            )}
          </div>
        )}
      </div>

      {/* Results */}
      {error && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', padding: '0.875rem 1rem', background: 'var(--error-bg)', border: '1px solid rgba(220,38,38,0.2)', borderRadius: 'var(--radius-sm)', color: 'var(--error)', fontSize: '0.875rem' }}>
          <WarningCircle size={16} />
          {error}
        </div>
      )}

      {loading && !jobs.length ? (
        <div style={{ color: 'var(--text-subtle)', fontSize: '0.9375rem', padding: '3rem 0', textAlign: 'center' }}>
          Searching...
        </div>
      ) : (
        <>
          {jobs.length > 0 && (
            <p style={{ fontSize: '0.875rem', color: 'var(--text-subtle)' }}>
              {jobs.length}{totalFound > jobs.length ? ` of ${totalFound}` : ''} results
              {currentQuery && <> for <strong style={{ color: 'var(--text)' }}>{currentQuery}</strong></>}
            </p>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
            {jobs.map((job) => <JobCard key={job.id} job={job} />)}
          </div>

          {jobs.length === 0 && !loading && !error && (
            <div style={{ padding: '4rem 0', textAlign: 'center', color: 'var(--text-subtle)', fontSize: '0.9375rem' }}>
              No results found. Try a different search.
            </div>
          )}

          {hasMore && !isFiltered && (
            <div style={{ textAlign: 'center', paddingTop: '0.5rem' }}>
              <button
                onClick={() => loadAllJobs(true)}
                disabled={loadingMore}
                style={{
                  padding: '0.5rem 1.25rem', background: 'var(--surface)', border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-sm)', fontSize: '0.875rem', color: 'var(--text)', cursor: 'pointer',
                }}
              >
                {loadingMore ? 'Loading...' : 'Load more'}
              </button>
            </div>
          )}
        </>
      )}

      <style>{`
        @keyframes progress-indeterminate {
          0% { transform: translateX(-100%); }
          50% { transform: translateX(150%); }
          100% { transform: translateX(400%); }
        }
      `}</style>
    </div>
  );
}
