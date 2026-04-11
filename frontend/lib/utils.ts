import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

// ─────────────────────────────────────────────
// Class merging utility
// ─────────────────────────────────────────────

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// ─────────────────────────────────────────────
// Currency / Salary formatting
// ─────────────────────────────────────────────

const CURRENCY_MAP: Record<string, string> = {
  USD: '$',
  EUR: '€',
  GBP: '£',
  INR: '₹',
  AUD: 'A$',
  CAD: 'C$',
  JPY: '¥',
  SGD: 'S$',
};

function getCurrencySymbol(currency?: string): string {
  if (!currency) return '$';
  return CURRENCY_MAP[currency.toUpperCase()] || currency;
}

function formatNumber(num: number): string {
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(0)}K`;
  return num.toLocaleString();
}

export function formatSalary(
  min?: number,
  max?: number,
  currency?: string
): string {
  if ((!min && !max) || (min === 0 && max === 0)) return 'Not specified';
  const sym = getCurrencySymbol(currency);

  if (min && max && min > 0 && max > 0) {
    return `${sym}${formatNumber(min)} – ${sym}${formatNumber(max)}`;
  }
  if (min && min > 0) return `From ${sym}${formatNumber(min)}`;
  if (max && max > 0) return `Up to ${sym}${formatNumber(max)}`;
  return 'Not specified';
}

export function formatSalaryFull(
  min?: number,
  max?: number,
  currency?: string
): string {
  if ((!min && !max) || (min === 0 && max === 0)) return 'Not specified';
  const sym = getCurrencySymbol(currency);

  if (min && max && min > 0 && max > 0) {
    return `${sym}${min.toLocaleString()} – ${sym}${max.toLocaleString()}`;
  }
  if (min && min > 0) return `From ${sym}${min.toLocaleString()}`;
  if (max && max > 0) return `Up to ${sym}${max.toLocaleString()}`;
  return 'Not specified';
}

// ─────────────────────────────────────────────
// Date formatting
// ─────────────────────────────────────────────

export function formatDate(dateStr?: string): string {
  if (!dateStr) return 'Unknown';

  try {
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return 'Unknown';

    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays < 0) return 'Future date';
    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays} days ago`;
    if (diffDays < 30) return `${Math.floor(diffDays / 7)} weeks ago`;
    if (diffDays < 365) return `${Math.floor(diffDays / 30)} months ago`;

    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return 'Unknown';
  }
}

export function formatDateTime(dateStr?: string): string {
  if (!dateStr) return 'Unknown';

  try {
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return 'Unknown';

    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return 'Unknown';
  }
}

// ─────────────────────────────────────────────
// Duration formatting (for ingestion runs)
// ─────────────────────────────────────────────

export function formatDuration(seconds?: number): string {
  if (seconds === undefined || seconds === null) return 'N/A';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return `${mins}m ${secs}s`;
  }
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  return `${hours}h ${mins}m`;
}

// ─────────────────────────────────────────────
// Label helpers
// ─────────────────────────────────────────────

export function getSeniorityLabel(level?: string): string {
  const labels: Record<string, string> = {
    intern: 'Intern',
    junior: 'Junior',
    entry: 'Entry-Level',
    mid: 'Mid-Level',
    senior: 'Senior',
    lead: 'Lead',
    principal: 'Principal',
    staff: 'Staff',
    director: 'Director',
    vp: 'VP',
    c_level: 'C-Level',
  };
  return level ? labels[level] || level : 'Not specified';
}

export function getEmploymentTypeLabel(type?: string): string {
  const labels: Record<string, string> = {
    full_time: 'Full-Time',
    part_time: 'Part-Time',
    contract: 'Contract',
    internship: 'Internship',
    freelance: 'Freelance',
    temporary: 'Temporary',
  };
  return type ? labels[type] || type : 'Not specified';
}

// ─────────────────────────────────────────────
// Ingestion state helpers
// ─────────────────────────────────────────────

export function getIngestStateColor(
  state: string
): 'green' | 'red' | 'yellow' | 'blue' | 'gray' {
  switch (state) {
    case 'success':
      return 'green';
    case 'failed':
      return 'red';
    case 'running':
      return 'blue';
    case 'queued':
      return 'yellow';
    case 'up_for_retry':
      return 'yellow';
    default:
      return 'gray';
  }
}

export function getIngestStateLabel(state: string): string {
  return state
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

// ─────────────────────────────────────────────
// Truncate text
// ─────────────────────────────────────────────

export function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength).trimEnd() + '…';
}

// ─────────────────────────────────────────────
// Debounce utility
// ─────────────────────────────────────────────

export function debounce<T extends (...args: unknown[]) => void>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: ReturnType<typeof setTimeout> | null = null;
  return (...args: Parameters<T>) => {
    if (timeout) clearTimeout(timeout);
    timeout = setTimeout(() => func(...args), wait);
  };
}
