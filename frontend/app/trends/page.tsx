'use client';

import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { TrendData } from '@/lib/types';
import Header from '@/components/Header';
import { TrendingUp, DollarSign, MapPin, Briefcase, Loader2, AlertCircle, BarChart3 } from 'lucide-react';

export default function TrendsPage() {
  const [trendData, setTrendData] = useState<TrendData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState(30);

  useEffect(() => {
    loadTrends();
  }, [days]);

  const loadTrends = async () => {
    setLoading(true);
    setError(null);

    try {
      const data = await api.trends.get('Market trends and insights', days);
      setTrendData(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load trends');
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
          <h1 className="text-3xl font-bold text-slate-900 mb-2">Market Trends</h1>
          <p className="text-slate-600">
            Real-time insights into the job market, skill demands, and salary trends
          </p>
        </div>

        {/* Time Range Selector */}
        <div className="flex items-center gap-4 mb-8">
          <span className="text-sm font-medium text-slate-700">Time Range:</span>
          <div className="flex gap-2">
            {[7, 30, 90].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  days === d
                    ? 'bg-primary-600 text-white'
                    : 'bg-white text-slate-700 border border-slate-300 hover:border-primary-300'
                }`}
              >
                {d === 7 ? '1 Week' : d === 30 ? '1 Month' : '3 Months'}
              </button>
            ))}
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="mb-6 p-4 bg-red-50 border border-red-200 rounded-xl flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-red-900">Failed to load trends</p>
              <p className="text-red-700 text-sm">{error}</p>
            </div>
          </div>
        )}

        {/* Loading */}
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="w-8 h-8 text-primary-600 animate-spin" />
            <span className="ml-3 text-slate-600">Loading trends...</span>
          </div>
        ) : trendData ? (
          <div className="space-y-6">
            {/* AI Summary */}
            {trendData.summary && (
              <div className="p-6 bg-gradient-to-br from-green-50 to-emerald-50 border border-green-200 rounded-xl">
                <div className="flex items-start gap-3">
                  <div className="w-8 h-8 bg-green-600 rounded-lg flex items-center justify-center flex-shrink-0">
                    <TrendingUp className="w-5 h-5 text-white" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-green-900 mb-2">Market Analysis</h3>
                    <div className="text-slate-700 prose prose-sm max-w-none">
                      {trendData.summary.split('\n').map((line, idx) => (
                        <p key={idx} className={line.startsWith('-') ? 'ml-4' : ''}>
                          {line}
                        </p>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Stats Grid */}
            <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-4">
              {/* Total Jobs */}
              <div className="bg-white p-6 rounded-xl border border-slate-200">
                <div className="flex items-center gap-3 mb-3">
                  <BarChart3 className="w-5 h-5 text-primary-600" />
                  <span className="text-sm font-medium text-slate-600">Active Jobs</span>
                </div>
                <div className="text-2xl font-bold text-slate-900">
                  {trendData.total_jobs.toLocaleString()}
                </div>
              </div>

              {/* Salary */}
              <div className="bg-white p-6 rounded-xl border border-slate-200">
                <div className="flex items-center gap-3 mb-3">
                  <DollarSign className="w-5 h-5 text-green-600" />
                  <span className="text-sm font-medium text-slate-600">Avg Salary</span>
                </div>
                <div className="text-2xl font-bold text-slate-900">
                  {trendData.salary_data?.available
                    ? `$${Math.round(trendData.salary_data.avg_min || 0).toLocaleString()} - $${Math.round(trendData.salary_data.avg_max || 0).toLocaleString()}`
                    : 'N/A'}
                </div>
              </div>

              {/* Top Location */}
              <div className="bg-white p-6 rounded-xl border border-slate-200">
                <div className="flex items-center gap-3 mb-3">
                  <MapPin className="w-5 h-5 text-blue-600" />
                  <span className="text-sm font-medium text-slate-600">Top Location</span>
                </div>
                <div className="text-lg font-bold text-slate-900">
                  {trendData.location_data[0]?.location || 'N/A'}
                </div>
              </div>

              {/* Top Skill */}
              <div className="bg-white p-6 rounded-xl border border-slate-200">
                <div className="flex items-center gap-3 mb-3">
                  <Briefcase className="w-5 h-5 text-purple-600" />
                  <span className="text-sm font-medium text-slate-600">Top Skill</span>
                </div>
                <div className="text-lg font-bold text-slate-900">
                  {trendData.top_skills[0]?.skill || 'N/A'}
                </div>
              </div>
            </div>

            {/* Skills & Locations */}
            <div className="grid md:grid-cols-2 gap-6">
              {/* Top Skills */}
              <div className="bg-white p-6 rounded-xl border border-slate-200">
                <h3 className="font-semibold text-slate-900 mb-4">Most In-Demand Skills</h3>
                <div className="space-y-3">
                  {trendData.top_skills.slice(0, 10).map((item, idx) => {
                    const maxCount = trendData.top_skills[0]?.count || 1;
                    const percentage = (item.count / maxCount) * 100;
                    return (
                      <div key={idx}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-sm font-medium text-slate-700">{item.skill}</span>
                          <span className="text-sm text-slate-500">{item.count}</span>
                        </div>
                        <div className="w-full bg-slate-100 rounded-full h-2">
                          <div
                            className="bg-primary-600 h-2 rounded-full transition-all"
                            style={{ width: `${percentage}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Locations */}
              <div className="bg-white p-6 rounded-xl border border-slate-200">
                <h3 className="font-semibold text-slate-900 mb-4">Job Distribution by Location</h3>
                <div className="space-y-3">
                  {trendData.location_data.slice(0, 10).map((item, idx) => {
                    const maxCount = trendData.location_data[0]?.count || 1;
                    const percentage = (item.count / maxCount) * 100;
                    return (
                      <div key={idx}>
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-sm font-medium text-slate-700">{item.location}</span>
                          <span className="text-sm text-slate-500">{item.count}</span>
                        </div>
                        <div className="w-full bg-slate-100 rounded-full h-2">
                          <div
                            className="bg-blue-600 h-2 rounded-full transition-all"
                            style={{ width: `${percentage}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="text-center py-16">
            <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
              <TrendingUp className="w-8 h-8 text-slate-400" />
            </div>
            <h3 className="text-lg font-semibold text-slate-900 mb-2">No trend data available</h3>
            <p className="text-slate-600">Run the ingestion pipeline to collect job market data</p>
          </div>
        )}
      </div>
    </div>
  );
}
