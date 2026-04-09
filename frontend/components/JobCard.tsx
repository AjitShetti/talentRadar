'use client';

import { Job } from '@/lib/types';
import { formatSalary, formatDate, getSeniorityLabel, getEmploymentTypeLabel } from '@/lib/utils';
import { MapPin, Building2, DollarSign, Calendar, ExternalLink, Briefcase, Star } from 'lucide-react';
import Link from 'next/link';

interface JobCardProps {
  job: Job;
  showScore?: boolean;
}

export default function JobCard({ job, showScore = false }: JobCardProps) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-6 hover:shadow-lg hover:border-primary-300 transition-all group">
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex-1">
          <h3 className="text-lg font-semibold text-slate-900 group-hover:text-primary-700 transition-colors">
            <Link href={`/jobs/${job.id}`} className="hover:underline">
              {job.title}
            </Link>
          </h3>
          {job.company_name && (
            <div className="flex items-center gap-2 mt-1 text-slate-600">
              <Building2 className="w-4 h-4" />
              <span className="text-sm">{job.company_name}</span>
            </div>
          )}
        </div>
        {showScore && job.match_score && (
          <div className="flex items-center gap-1 px-3 py-1 bg-green-50 text-green-700 rounded-full text-sm font-medium">
            <Star className="w-4 h-4 fill-current" />
            {Math.round(job.match_score * 100)}%
          </div>
        )}
      </div>

      {/* Meta info */}
      <div className="grid grid-cols-2 gap-3 mb-4 text-sm">
        {/* Location */}
        <div className="flex items-center gap-2 text-slate-600">
          <MapPin className="w-4 h-4" />
          <span>{job.is_remote ? '🌐 Remote' : job.location_raw || job.city || 'Location not specified'}</span>
        </div>

        {/* Salary */}
        <div className="flex items-center gap-2 text-slate-600">
          <DollarSign className="w-4 h-4" />
          <span>{formatSalary(job.salary_min, job.salary_max, job.salary_currency)}</span>
        </div>

        {/* Seniority */}
        <div className="flex items-center gap-2 text-slate-600">
          <Briefcase className="w-4 h-4" />
          <span>{getSeniorityLabel(job.seniority)}</span>
        </div>

        {/* Posted */}
        <div className="flex items-center gap-2 text-slate-600">
          <Calendar className="w-4 h-4" />
          <span>{formatDate(job.posted_at)}</span>
        </div>
      </div>

      {/* Skills */}
      {job.skills.length > 0 && (
        <div className="mb-4">
          <div className="flex flex-wrap gap-2">
            {job.skills.slice(0, 6).map((skill, idx) => (
              <span
                key={idx}
                className="px-2 py-1 bg-slate-100 text-slate-700 rounded-md text-xs font-medium"
              >
                {skill}
              </span>
            ))}
            {job.skills.length > 6 && (
              <span className="px-2 py-1 bg-slate-100 text-slate-700 rounded-md text-xs">
                +{job.skills.length - 6} more
              </span>
            )}
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between pt-4 border-t border-slate-100">
        <div className="flex gap-2 text-xs text-slate-500">
          {job.seniority && (
            <span className="px-2 py-1 bg-blue-50 text-blue-700 rounded">
              {getSeniorityLabel(job.seniority)}
            </span>
          )}
          {job.employment_type && (
            <span className="px-2 py-1 bg-purple-50 text-purple-700 rounded">
              {getEmploymentTypeLabel(job.employment_type)}
            </span>
          )}
        </div>
        {job.source_url && (
          <a
            href={job.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-sm text-primary-600 hover:text-primary-700 font-medium"
          >
            Apply Now
            <ExternalLink className="w-4 h-4" />
          </a>
        )}
      </div>
    </div>
  );
}
