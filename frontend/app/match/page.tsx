'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { api } from '@/lib/api';
import { MatchResult } from '@/lib/types';
import Header from '@/components/Header';
import { Target, Plus, X, Loader2, AlertCircle, Check } from 'lucide-react';
import Link from 'next/link';

const MAX_SKILL_LENGTH = 50;
const MAX_TITLE_LENGTH = 100;
const MAX_LOCATION_LENGTH = 100;
const MAX_RESUME_LENGTH = 5000;

// Sanitize text for safe display
function sanitizeText(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

export default function MatchPage() {
  const [skills, setSkills] = useState<string[]>([]);
  const [skillInput, setSkillInput] = useState('');
  const [desiredTitle, setDesiredTitle] = useState('');
  const [location, setLocation] = useState('');
  const [isRemote, setIsRemote] = useState(false);
  const [seniority, setSeniority] = useState('');
  const [resumeText, setResumeText] = useState('');
  const [matches, setMatches] = useState<MatchResult[]>([]);
  const [summary, setSummary] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Ref to track the latest AbortController
  const abortRef = useRef<AbortController | null>(null);

  // Cancel in-flight request on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const addSkill = useCallback(() => {
    const skill = skillInput.trim().slice(0, MAX_SKILL_LENGTH);
    if (skill && !skills.includes(skill.toLowerCase())) {
      setSkills([...skills, skill.toLowerCase()]);
      setSkillInput('');
    }
  }, [skillInput, skills]);

  const removeSkill = useCallback((skill: string) => {
    setSkills((prev) => prev.filter((s) => s !== skill));
  }, []);

  const handleMatch = useCallback(async () => {
    if (skills.length === 0 && !resumeText.trim()) {
      setError('Please add at least one skill or paste your resume text');
      return;
    }

    // Abort any previous in-flight request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setError(null);

    try {
      const response = await api.recommend.match(
        {
          candidate: {
            skills,
            desired_title: desiredTitle || undefined,
            location: location || undefined,
            is_remote: isRemote,
            seniority: seniority || undefined,
            resume_text: resumeText || undefined,
          },
          limit: 20,
        },
        controller.signal
      );
      setMatches(response.matches);
      setSummary(response.summary || null);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message = err instanceof Error ? err.message : 'An unexpected error occurred';
      setError(message);
      setMatches([]);
      setSummary(null);
    } finally {
      setLoading(false);
    }
  }, [skills, desiredTitle, location, isRemote, seniority, resumeText]);

  return (
    <div>
      <Header />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-slate-900 mb-2">Job Matcher</h1>
          <p className="text-slate-600">
            Add your skills and preferences to find the best job matches for your profile
          </p>
        </div>

        <div className="grid lg:grid-cols-2 gap-8">
          {/* Profile Form */}
          <div className="space-y-6">
            {/* Skills */}
            <div className="bg-white p-6 rounded-xl border border-slate-200">
              <h3 className="font-semibold text-slate-900 mb-4">Your Skills</h3>
              <div className="flex gap-2 mb-3">
                <input
                  type="text"
                  value={skillInput}
                  onChange={(e) => setSkillInput(e.target.value.slice(0, MAX_SKILL_LENGTH))}
                  onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addSkill())}
                  placeholder="e.g., Python, React, AWS"
                  maxLength={MAX_SKILL_LENGTH}
                  aria-label="Add a skill"
                  className="flex-1 px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                />
                <button
                  onClick={addSkill}
                  disabled={!skillInput.trim()}
                  aria-label="Add skill"
                  className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <Plus className="w-5 h-5" />
                </button>
              </div>
              {skills.length > 0 && (
                <div className="flex flex-wrap gap-2" role="list" aria-label="Added skills">
                  {skills.map((skill) => (
                    <span
                      key={skill}
                      role="listitem"
                      className="inline-flex items-center gap-1 px-3 py-1 bg-primary-50 text-primary-700 rounded-lg text-sm"
                    >
                      {sanitizeText(skill)}
                      <button
                        onClick={() => removeSkill(skill)}
                        className="hover:text-primary-900"
                        aria-label={`Remove ${skill}`}
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Preferences */}
            <div className="bg-white p-6 rounded-xl border border-slate-200 space-y-4">
              <h3 className="font-semibold text-slate-900 mb-4">Preferences</h3>

              <div>
                <label htmlFor="desired-title" className="block text-sm font-medium text-slate-700 mb-1">Desired Role</label>
                <input
                  id="desired-title"
                  type="text"
                  value={desiredTitle}
                  onChange={(e) => setDesiredTitle(e.target.value.slice(0, MAX_TITLE_LENGTH))}
                  placeholder="e.g., Senior Software Engineer"
                  maxLength={MAX_TITLE_LENGTH}
                  className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                />
              </div>

              <div>
                <label htmlFor="location" className="block text-sm font-medium text-slate-700 mb-1">Location</label>
                <input
                  id="location"
                  type="text"
                  value={location}
                  onChange={(e) => setLocation(e.target.value.slice(0, MAX_LOCATION_LENGTH))}
                  placeholder="e.g., San Francisco, Remote"
                  maxLength={MAX_LOCATION_LENGTH}
                  className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                />
              </div>

              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  id="remote-toggle"
                  checked={isRemote}
                  onChange={(e) => setIsRemote(e.target.checked)}
                  className="w-4 h-4 text-primary-600 rounded"
                />
                <label htmlFor="remote-toggle" className="text-sm text-slate-700">
                  Remote only
                </label>
              </div>

              <div>
                <label htmlFor="seniority" className="block text-sm font-medium text-slate-700 mb-1">Seniority</label>
                <select
                  id="seniority"
                  value={seniority}
                  onChange={(e) => setSeniority(e.target.value)}
                  className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500"
                >
                  <option value="">Any</option>
                  <option value="junior">Junior</option>
                  <option value="mid">Mid-Level</option>
                  <option value="senior">Senior</option>
                  <option value="lead">Lead</option>
                  <option value="principal">Principal</option>
                </select>
              </div>
            </div>

            {/* Resume Text (Optional) */}
            <div className="bg-white p-6 rounded-xl border border-slate-200">
              <h3 className="font-semibold text-slate-900 mb-2">Resume / Summary (Optional)</h3>
              <p className="text-sm text-slate-600 mb-3">
                Paste your resume or professional summary for better matching
              </p>
              <textarea
                value={resumeText}
                onChange={(e) => setResumeText(e.target.value.slice(0, MAX_RESUME_LENGTH))}
                placeholder="Paste your resume text here..."
                rows={4}
                maxLength={MAX_RESUME_LENGTH}
                aria-label="Resume or professional summary"
                className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-primary-500 resize-none"
              />
              <p className="text-xs text-slate-500 mt-1">
                {resumeText.length}/{MAX_RESUME_LENGTH} characters
              </p>
            </div>

            {/* Match Button */}
            <button
              onClick={handleMatch}
              disabled={loading || (skills.length === 0 && !resumeText.trim())}
              className="w-full py-3 bg-gradient-to-r from-primary-600 to-primary-700 text-white rounded-xl font-semibold hover:from-primary-700 hover:to-primary-800 disabled:opacity-50 disabled:cursor-not-allowed transition-all flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  Finding Matches...
                </>
              ) : (
                <>
                  <Target className="w-5 h-5" />
                  Find Matching Jobs
                </>
              )}
            </button>

            {/* Error */}
            {error && (
              <div className="p-4 bg-red-50 border border-red-200 rounded-xl flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
                <p className="text-red-700 text-sm">{sanitizeText(error)}</p>
              </div>
            )}
          </div>

          {/* Results */}
          <div>
            {loading && (
              <div className="flex items-center justify-center py-16">
                <Loader2 className="w-8 h-8 text-primary-600 animate-spin" />
                <span className="ml-3 text-slate-600">Analyzing your profile...</span>
              </div>
            )}

            {!loading && summary && (
              <div className="mb-6 p-6 bg-gradient-to-br from-purple-50 to-pink-50 border border-purple-200 rounded-xl">
                <div className="flex items-start gap-3">
                  <div className="w-8 h-8 bg-purple-600 rounded-lg flex items-center justify-center flex-shrink-0">
                    <Check className="w-5 h-5 text-white" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-purple-900 mb-2">Match Summary</h3>
                    <p className="text-slate-700">{sanitizeText(summary)}</p>
                  </div>
                </div>
              </div>
            )}

            {!loading && matches.length > 0 && (
              <div className="space-y-4">
                <h3 className="font-semibold text-slate-900">
                  Your Top Matches ({matches.length})
                </h3>
                {matches.map((match) => (
                  <div
                    key={match.job_id}
                    className="bg-white p-6 rounded-xl border border-slate-200 hover:shadow-md transition-shadow"
                  >
                    <div className="flex items-start justify-between mb-3">
                      <div>
                        <h4 className="font-semibold text-slate-900">
                          <Link href={`/jobs/${match.job_id}`} className="hover:text-primary-600 hover:underline">
                            {sanitizeText(match.title)}
                          </Link>
                        </h4>
                        <p className="text-sm text-slate-600">{sanitizeText(match.company)}</p>
                      </div>
                      <div className="px-3 py-1 bg-green-50 text-green-700 rounded-full text-sm font-bold">
                        {Math.round(match.score * 100)}%
                      </div>
                    </div>

                    {match.match_reason && (
                      <p className="text-sm text-slate-600 mb-3">{sanitizeText(match.match_reason)}</p>
                    )}

                    {match.skills.length > 0 && (
                      <div className="flex flex-wrap gap-2">
                        {match.skills.slice(0, 5).map((skill) => (
                          <span
                            key={skill}
                            className="px-2 py-1 bg-slate-100 text-slate-700 rounded text-xs"
                          >
                            {sanitizeText(skill)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {!loading && matches.length === 0 && !summary && (
              <div className="text-center py-16">
                <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mx-auto mb-4">
                  <Target className="w-8 h-8 text-slate-400" />
                </div>
                <h3 className="text-lg font-semibold text-slate-900 mb-2">Add your profile</h3>
                <p className="text-slate-600">Fill in your skills and preferences to find matching jobs</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
