'use client';

import Link from 'next/link';
import { Search, TrendingUp, Target, Zap, ArrowRight, Github } from 'lucide-react';
import Header from '@/components/Header';

const features = [
  {
    icon: Search,
    title: 'Semantic Search',
    description: 'Search jobs using natural language. Our AI understands what you\'re looking for.',
    href: '/search',
    color: 'from-blue-500 to-blue-600',
  },
  {
    icon: TrendingUp,
    title: 'Market Trends',
    description: 'Get real-time insights into skill demand, salary trends, and market opportunities.',
    href: '/trends',
    color: 'from-green-500 to-green-600',
  },
  {
    icon: Target,
    title: 'Smart Matching',
    description: 'Upload your profile and get personalized job recommendations with match scores.',
    href: '/match',
    color: 'from-purple-500 to-purple-600',
  },
];

export default function HomePage() {
  return (
    <div>
      <Header />

      {/* Hero Section */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-primary-600 via-primary-700 to-primary-900" />
        <div className="absolute inset-0 opacity-10">
          <div className="absolute inset-0" style={{ backgroundImage: 'radial-gradient(circle at 2px 2px, white 1px, transparent 0)', backgroundSize: '40px 40px' }} />
        </div>

        <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-24 md:py-32">
          <div className="text-center">
            <div className="inline-flex items-center gap-2 px-4 py-2 bg-white/10 backdrop-blur-sm rounded-full text-white/90 text-sm mb-6">
              <Zap className="w-4 h-4" />
              AI-Powered Job Intelligence Platform
            </div>
            <h1 className="text-4xl md:text-6xl font-bold text-white mb-6">
              Find Your Perfect Job with
              <span className="block bg-gradient-to-r from-yellow-300 to-yellow-400 bg-clip-text text-transparent">
                AI Precision
              </span>
            </h1>
            <p className="text-lg md:text-xl text-white/80 max-w-2xl mx-auto mb-8">
              Search smarter with semantic understanding, discover market trends, and get personalized job matches powered by machine learning.
            </p>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <Link
                href="/search"
                className="w-full sm:w-auto px-8 py-3 bg-white text-primary-700 rounded-xl font-semibold hover:bg-slate-50 transition-colors flex items-center justify-center gap-2"
              >
                Start Searching
                <ArrowRight className="w-5 h-5" />
              </Link>
              <Link
                href="/trends"
                className="w-full sm:w-auto px-8 py-3 bg-white/10 backdrop-blur-sm text-white border border-white/20 rounded-xl font-semibold hover:bg-white/20 transition-colors flex items-center justify-center gap-2"
              >
                View Market Trends
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-20">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center mb-12">
            <h2 className="text-3xl font-bold text-slate-900 mb-4">
              Everything You Need to Land Your Dream Job
            </h2>
            <p className="text-lg text-slate-600 max-w-2xl mx-auto">
              Powerful AI tools to search, analyze, and match opportunities
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-8">
            {features.map((feature) => {
              const Icon = feature.icon;
              return (
                <Link
                  key={feature.title}
                  href={feature.href}
                  className="group bg-white rounded-2xl border border-slate-200 p-8 hover:shadow-xl hover:border-primary-300 transition-all"
                >
                  <div className={`w-14 h-14 bg-gradient-to-br ${feature.color} rounded-xl flex items-center justify-center mb-6 group-hover:scale-110 transition-transform`}>
                    <Icon className="w-7 h-7 text-white" />
                  </div>
                  <h3 className="text-xl font-semibold text-slate-900 mb-3">
                    {feature.title}
                  </h3>
                  <p className="text-slate-600 mb-4">{feature.description}</p>
                  <div className="flex items-center gap-2 text-primary-600 font-medium group-hover:gap-3 transition-all">
                    Get Started
                    <ArrowRight className="w-4 h-4" />
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
      </section>

      {/* Stats Section */}
      <section className="py-16 bg-slate-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="grid md:grid-cols-4 gap-8 text-center">
            <div>
              <div className="text-3xl font-bold text-primary-600 mb-2">AI-Powered</div>
              <div className="text-slate-600">Semantic Understanding</div>
            </div>
            <div>
              <div className="text-3xl font-bold text-primary-600 mb-2">Real-Time</div>
              <div className="text-slate-600">Market Insights</div>
            </div>
            <div>
              <div className="text-3xl font-bold text-primary-600 mb-2">Smart</div>
              <div className="text-slate-600">Job Matching</div>
            </div>
            <div>
              <div className="text-3xl font-bold text-primary-600 mb-2">Automated</div>
              <div className="text-slate-600">Data Collection</div>
            </div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-12 bg-white border-t border-slate-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col md:flex-row items-center justify-between gap-6">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-gradient-to-br from-primary-500 to-primary-700 rounded-lg flex items-center justify-center">
                <Zap className="w-5 h-5 text-white" />
              </div>
              <span className="text-lg font-bold text-slate-900">TalentRadar</span>
            </div>
            <div className="flex items-center gap-6 text-slate-600">
              <a href="/api/docs" className="hover:text-primary-600">API Docs</a>
              <a href="http://localhost:8080" className="hover:text-primary-600">Airflow UI</a>
              <a
                href="https://github.com"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 hover:text-primary-600"
              >
                <Github className="w-4 h-4" />
                GitHub
              </a>
            </div>
            <div className="text-slate-500 text-sm">
              © 2026 TalentRadar. All rights reserved.
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
