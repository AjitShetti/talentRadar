'use client';

import { useState, useCallback, useRef, useEffect } from 'react';
import { api } from '@/lib/api';
import { Job } from '@/lib/types';
import Header from '@/components/Header';
import SearchBar from '@/components/SearchBar';
import JobCard from '@/components/JobCard';
import { AlertCircle, Loader2 } from 'lucide-react';

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
    // Abort any previous in-flight request
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
        // Append new results to existing list
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

  // Sanitize text for safe display (prevents XSS from user-provided content)
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
      <Header />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-slate-900 mb-2">Search Jobs</h1>
          <p className="text-slate-600">
            Use natural language to find jobs. Try &quot;remote Python engineer&quot; or &quot;senior ML engineer San Francisco&quot;
          </p>
        </div>

        {/* Search Bar */}
        <div className="mb-8">
          <SearchBar onSearch={(query) => handleSearch(query, false)} loading={loading} placeholder="e.g., remote software engineer jobs" />
        </div>

        {/* Error */}
        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-xl flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-red-900">Search failed</p>
              <p className="text-red-700 text-sm">{sanitizeText(error)}</p>
            </div>
          </div>
        )}

        {/* AI Summary */}
        {summary && (
          <div className="mb-6 p-6 bg-gradient-to-br from-primary-50 to-blue-50 border border-primary-200 rounded-xl">
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 bg-primary-600 rounded-lg flex items-center justify-center flex-shrink-0">
                <span className="text-white text-sm font-bold">AI</span>
              </div>
              <div>
                <h3 className="font-semibold text-primary-900 mb-2">Search Summary</h3>
                <div className="text-slate-700 prose prose-sm max-w-none">
                  {summary.split('\n').map((line, idx) => (
                    <p key={`summary-${idx}`} className={line.startsWith('-') ? 'ml-4' : ''}>
                      {sanitizeText(line)}
                    </p>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Results Count */}
        {!loading && totalFound > 0 && (
          <div className="mb-4 text-slate-600">
            Showing <span className="font-semibold text-slate-900">{jobs.length}</span> of{' '}
            <span className="font-semibold text-slate-900">{totalFound}</span> jobs
          </div>
        )}

        {/* Loading State */}
        {loading && (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="w-8 h-8 text-primary-600 animate-spin" />
            <span className="ml-3 text-slate-600">Searching jobs...</span>
          </div>
        )}

        {/* Job List */}
        {!loading && jobs.length > 0 && (
          <div className="grid gap-4">
            {jobs.map((job) => (
              <JobCard key={job.id} job={job} showScore />
            ))}
          </div>
        )}

        {/* Load More Button */}
        {!loading && hasMore && jobs.length > 0 && (
          <div className="flex justify-center mt-8">
            <button
              onClick={handleLoadMore}
              disabled={loadingMore}
              className="px-6 py-3 bg-white border border-slate-300 text-slate-700 rounded-xl font-medium hover:bg-slate-50 hover:border-primary-300 disabled:opacity-50 disabled:cursor-not-allowed transition-all flex items-center gap-2"
            >
              {loadingMore ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Loading more...
                </>
              ) : (
                'Load More Jobs'
              )}
            </button>
          </div>
        )}

        {/* Empty State */}
        {!loading && !loadingMore && jobs.length === 0 && !error && totalFound === 0 && (
          <div className="text-center py-16">
            <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <span className="text-3xl" role="img" aria-label="search">🔍</span>
            </div>
            <h3 className="text-lg font-semibold text-slate-900 mb-2">Start your search</h3>
            <p className="text-slate-600">Enter a search query above to find relevant jobs</p>
          </div>
        )}
      </div>
    </div>
  );
}
