'use client';

import { useState } from 'react';
import { api } from '@/lib/api';
import { Job } from '@/lib/types';
import Header from '@/components/Header';
import SearchBar from '@/components/SearchBar';
import JobCard from '@/components/JobCard';
import { AlertCircle, Loader2 } from 'lucide-react';

export default function SearchPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [summary, setSummary] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [totalFound, setTotalFound] = useState(0);

  const handleSearch = async (query: string) => {
    setLoading(true);
    setError(null);
    setSummary(null);

    try {
      const response = await api.search.semantic({ query, limit: 20, offset: 0 });
      setJobs(response.results);
      setSummary(response.summary || null);
      setTotalFound(response.total_found);
    } catch (err: any) {
      setError(err.message || 'Failed to search jobs');
      setJobs([]);
      setSummary(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <Header />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-slate-900 mb-2">Search Jobs</h1>
          <p className="text-slate-600">
            Use natural language to find jobs. Try "remote Python engineer" or "senior ML engineer San Francisco"
          </p>
        </div>

        {/* Search Bar */}
        <div className="mb-8">
          <SearchBar onSearch={handleSearch} loading={loading} placeholder="e.g., remote software engineer jobs" />
        </div>

        {/* Error */}
        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-xl flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-red-900">Search failed</p>
              <p className="text-red-700 text-sm">{error}</p>
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
                    <p key={idx} className={line.startsWith('-') ? 'ml-4' : ''}>
                      {line}
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
            Found <span className="font-semibold text-slate-900">{totalFound}</span> jobs
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

        {/* Empty State */}
        {!loading && jobs.length === 0 && !error && totalFound === 0 && (
          <div className="text-center py-16">
            <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <span className="text-3xl">🔍</span>
            </div>
            <h3 className="text-lg font-semibold text-slate-900 mb-2">Start your search</h3>
            <p className="text-slate-600">Enter a search query above to find relevant jobs</p>
          </div>
        )}
      </div>
    </div>
  );
}
