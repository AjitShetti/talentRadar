'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { api } from '@/lib/api';
import { Job } from '@/lib/types';
import SearchBar from '@/components/SearchBar';
import JobCard from '@/components/JobCard';
import { AlertCircle, Loader2, Zap } from 'lucide-react';

const PAGE_SIZE = 20;

export default function SearchPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [summary, setSummary] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [totalFound, setTotalFound] = useState(0);
  const [offset, setOffset] = useState(0);
  const [currentQuery, setCurrentQuery] = useState('');
  const [hasMore, setHasMore] = useState(false);

  // Ref to track the latest AbortController for cancelling in-flight requests
  const abortRef = useRef<AbortController | null>(null);

  // Cancel any in-flight request on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const handleSearch = useCallback(async (query: string, isLoadMore = false) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    if (!isLoadMore) {
      setLoading(true);
      setError(null);
      setSummary(null);
      setJobs([]);
      setOffset(0);
      setCurrentQuery(query);
    } else {
      setLoadingMore(true);
    }

    const currentOffset = isLoadMore ? offset : 0;

    try {
      const response = await api.search.semantic(
        { query, limit: PAGE_SIZE, offset: currentOffset },
        controller.signal
      );

      if (isLoadMore) {
        setJobs((prev) => [...prev, ...response.results]);
        setOffset(currentOffset + PAGE_SIZE);
      } else {
        setJobs(response.results);
        setOffset(PAGE_SIZE);
      }

      if (!isLoadMore) {
        setSummary(response.summary || null);
      }
      setTotalFound(response.total_found);
      setHasMore(response.results.length === PAGE_SIZE);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message = err instanceof Error ? err.message : 'An unexpected error occurred';
      setError(message);
      if (!isLoadMore) {
        setJobs([]);
        setSummary(null);
      }
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [offset]);

  const handleLoadMore = useCallback(() => {
    handleSearch(currentQuery, true);
  }, [handleSearch, currentQuery]);

  const sanitizeText = (text: string): string => {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#x27;');
  };

  return (
    <div>
      {/* Header Info */}
      <div style={{ marginBottom: '2rem' }}>
        <h1 style={{ fontSize: '3rem', display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem', fontFamily: 'var(--font-display)', fontWeight: 700 }}>
          <Zap className="text-accent" size={40} />
          SEMANTIC SEARCH
        </h1>
        <p style={{ color: 'var(--color-fg-muted)', fontSize: '1.25rem' }}>
          Execute natural language queries to discover roles matching your specific criteria.
        </p>
      </div>

      {/* Search Bar */}
      <div style={{ marginBottom: '3rem' }}>
        <SearchBar onSearch={(query) => handleSearch(query, false)} loading={loading} placeholder="e.g. Remote senior full-stack engineer using React and Python" />
      </div>

      {/* Error */}
      {error && (
        <div style={{ padding: '1rem', border: '1px solid #ef4444', background: 'rgba(239, 68, 68, 0.1)', color: '#fca5a5', display: 'flex', gap: '0.5rem', marginBottom: '2rem' }}>
          <AlertCircle /> {sanitizeText(error)}
        </div>
      )}

      {/* AI Summary */}
      {summary && (
        <div className="panel" style={{ marginBottom: '2rem', borderLeft: '4px solid var(--color-accent)' }}>
          <h3 style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--color-accent)', marginBottom: '1rem', fontFamily: 'var(--font-display)', fontWeight: 700 }}>
            <Zap size={18} />
            AI SEARCH SUMMARY
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', color: 'var(--color-fg)' }}>
            {summary.split('\n').map((line, idx) => (
              <p key={`summary-${idx}`} style={{ margin: 0, paddingLeft: line.startsWith('-') ? '1rem' : '0' }}>
                {sanitizeText(line)}
              </p>
            ))}
          </div>
        </div>
      )}

      {/* Results Count */}
      {!loading && totalFound > 0 && (
        <div style={{ marginBottom: '1.5rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)', fontWeight: 600 }}>
          SHOWING <span style={{ color: 'var(--color-fg)' }}>{jobs.length}</span> OF <span style={{ color: 'var(--color-fg)' }}>{totalFound}</span> JOBS
        </div>
      )}

      {/* Loading State */}
      {loading && (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '4rem 0', color: 'var(--color-fg-muted)' }}>
          <Loader2 size={48} className="animate-spin text-accent" style={{ marginBottom: '1rem' }} />
          <div style={{ fontFamily: 'var(--font-display)', letterSpacing: '0.1em' }}>SCANNING DATA SOURCES...</div>
        </div>
      )}

      {/* Job List */}
      {!loading && jobs.length > 0 && (
        <div style={{ display: 'grid', gap: '1.5rem' }}>
          {jobs.map((job) => (
            <JobCard key={job.id} job={job} showScore={true} />
          ))}
        </div>
      )}

      {/* Load More Button */}
      {!loading && hasMore && jobs.length > 0 && (
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: '3rem' }}>
          <button
            onClick={handleLoadMore}
            disabled={loadingMore}
            className="btn"
          >
            {loadingMore ? (
              <><Loader2 className="animate-spin text-accent" size={18} /> FETCHING...</>
            ) : (
              'LOAD MORE JOBS'
            )}
          </button>
        </div>
      )}

      {/* Empty State */}
      {!loading && !loadingMore && jobs.length === 0 && !error && totalFound === 0 && (
        <div style={{ textAlign: 'center', padding: '4rem 0', color: 'var(--color-border)' }}>
          <Zap size={64} style={{ margin: '0 auto 1rem auto', opacity: 0.5 }} />
          <p style={{ fontFamily: 'var(--font-display)', letterSpacing: '0.1em' }}>AWAITING QUERY INPUT</p>
        </div>
      )}
    </div>
  );
}
