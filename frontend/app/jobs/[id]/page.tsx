'use client';

import { useState, useEffect, use } from 'react';
import { api } from '@/lib/api';
import { Job } from '@/lib/types';
import {
  formatSalaryFull,
  formatDate,
  getSeniorityLabel,
  getEmploymentTypeLabel,
} from '@/lib/utils';
import Header from '@/components/Header';
import {
  Building2,
  MapPin,
  DollarSign,
  Calendar,
  ExternalLink,
  Briefcase,
  Loader2,
  AlertCircle,
  ArrowLeft,
  Globe,
  Clock,
  Tag,
} from 'lucide-react';
import Link from 'next/link';

// Sanitize text to prevent XSS when rendering user-provided content
function sanitizeText(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

export default function JobDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function loadJob() {
      setLoading(true);
      setError(null);

      try {
        const response = await api.search.detail(id, controller.signal);
        setJob(response.job);
      } catch (err) {
        if (controller.signal.aborted) return;
        const message = err instanceof Error ? err.message : 'An unexpected error occurred';
        setError(message);
      } finally {
        setLoading(false);
      }
    }

    loadJob();

    return () => {
      controller.abort();
    };
  }, [id]);

  if (loading) {
    return (
      <div>
        <Header />
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <div className="flex items-center justify-center py-16">
            <Loader2 className="w-8 h-8 text-primary-600 animate-spin" />
            <span className="ml-3 text-slate-600">Loading job details...</span>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <Header />
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <Link
            href="/search"
            className="inline-flex items-center gap-2 text-slate-600 hover:text-primary-600 mb-6 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Search
          </Link>
          <div className="p-6 bg-red-50 border border-red-200 rounded-xl flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-red-900">Failed to load job</p>
              <p className="text-red-700 text-sm">{error}</p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div>
        <Header />
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <Link
            href="/search"
            className="inline-flex items-center gap-2 text-slate-600 hover:text-primary-600 mb-6 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Search
          </Link>
          <div className="text-center py-16">
            <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <Briefcase className="w-8 h-8 text-slate-400" />
            </div>
            <h3 className="text-lg font-semibold text-slate-900 mb-2">Job not found</h3>
            <p className="text-slate-600">This job may have been removed or is no longer available</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <Header />

      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Back link */}
        <Link
          href="/search"
          className="inline-flex items-center gap-2 text-slate-600 hover:text-primary-600 mb-6 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Search
        </Link>

        {/* Job Header */}
        <div className="bg-white rounded-xl border border-slate-200 p-6 mb-6">
          <div className="flex items-start justify-between mb-4">
            <div className="flex-1">
              <h1 className="text-2xl font-bold text-slate-900 mb-2">{job.title}</h1>
              {job.company_name && (
                <div className="flex items-center gap-2 text-slate-600">
                  <Building2 className="w-5 h-5" />
                  <span className="text-lg">{job.company_name}</span>
                </div>
              )}
            </div>
            {job.source_url && (
              <a
                href={job.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors font-medium"
              >
                Apply Now
                <ExternalLink className="w-4 h-4" />
              </a>
            )}
          </div>

          {/* Meta Grid */}
          <div className="grid grid-cols-2 gap-4">
            <div className="flex items-center gap-2 text-slate-600">
              <MapPin className="w-4 h-4 flex-shrink-0" />
              <span>{job.is_remote ? '🌐 Remote' : job.location_raw || job.city || 'Location not specified'}</span>
            </div>
            <div className="flex items-center gap-2 text-slate-600">
              <DollarSign className="w-4 h-4 flex-shrink-0" />
              <span>{formatSalaryFull(job.salary_min, job.salary_max, job.salary_currency)}</span>
            </div>
            <div className="flex items-center gap-2 text-slate-600">
              <Briefcase className="w-4 h-4 flex-shrink-0" />
              <span>{getSeniorityLabel(job.seniority)}</span>
            </div>
            <div className="flex items-center gap-2 text-slate-600">
              <Calendar className="w-4 h-4 flex-shrink-0" />
              <span>Posted {formatDate(job.posted_at)}</span>
            </div>
          </div>

          {/* Tags */}
          <div className="flex flex-wrap gap-2 mt-4 pt-4 border-t border-slate-100">
            {job.seniority && (
              <span className="px-3 py-1 bg-blue-50 text-blue-700 rounded-full text-sm font-medium">
                {getSeniorityLabel(job.seniority)}
              </span>
            )}
            {job.employment_type && (
              <span className="px-3 py-1 bg-purple-50 text-purple-700 rounded-full text-sm font-medium">
                {getEmploymentTypeLabel(job.employment_type)}
              </span>
            )}
            <span className="px-3 py-1 bg-slate-100 text-slate-600 rounded-full text-sm">
              {job.source}
            </span>
          </div>
        </div>

        {/* Skills */}
        {job.skills.length > 0 && (
          <div className="bg-white rounded-xl border border-slate-200 p-6 mb-6">
            <h2 className="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
              <Tag className="w-5 h-5" />
              Required Skills
            </h2>
            <div className="flex flex-wrap gap-2">
              {job.skills.map((skill) => (
                <span
                  key={skill}
                  className="px-3 py-1.5 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium"
                >
                  {skill}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Job Description */}
        {job.description_clean && (
          <div className="bg-white rounded-xl border border-slate-200 p-6 mb-6">
            <h2 className="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
              <Globe className="w-5 h-5" />
              Job Description
            </h2>
            <div className="prose prose-slate max-w-none text-slate-700 whitespace-pre-wrap">
              {job.description_clean.split('\n').map((line, idx) => {
                if (line.trim() === '') return <br key={`desc-empty-${idx}`} />;
                return <p key={`desc-${line.slice(0, 40)}-${idx}`}>{sanitizeText(line)}</p>;
              })}
            </div>
          </div>
        )}

        {/* Additional Info */}
        <div className="bg-white rounded-xl border border-slate-200 p-6">
          <h2 className="text-lg font-semibold text-slate-900 mb-4 flex items-center gap-2">
            <Clock className="w-5 h-5" />
            Additional Information
          </h2>
          <div className="space-y-3 text-sm text-slate-600">
            <div className="flex justify-between">
              <span>Source</span>
              <span className="font-medium text-slate-900">{job.source}</span>
            </div>
            <div className="flex justify-between">
              <span>Job ID</span>
              <span className="font-mono text-slate-900">{job.id}</span>
            </div>
            <div className="flex justify-between">
              <span>Last Updated</span>
              <span className="font-medium text-slate-900">{formatDate(job.created_at)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
