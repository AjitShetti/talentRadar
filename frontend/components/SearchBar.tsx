'use client';

import { useState, useRef } from 'react';
import { Search as SearchIcon, Loader2, X, ArrowRight } from 'lucide-react';

interface SearchBarProps {
  onSearch: (query: string) => void;
  placeholder?: string;
  loading?: boolean;
}

export default function SearchBar({
  onSearch,
  placeholder = 'Search by title, technology, or role...',
  loading = false,
}: SearchBarProps) {
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSearch(query.trim());
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setQuery(value);
    if (!value) {
      onSearch('');
    }
  };

  const handleClear = () => {
    setQuery('');
    onSearch('');
    inputRef.current?.focus();
  };

  return (
    <form onSubmit={handleSubmit} style={{ width: '100%', position: 'relative' }}>
      <div
        style={{
          position: 'relative',
          display: 'flex',
          alignItems: 'center',
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--border-radius)',
          boxShadow: 'var(--shadow-sm)',
          transition: 'all var(--transition-fast)',
        }}
      >
        <div
          style={{
            position: 'absolute',
            left: '1rem',
            pointerEvents: 'none',
            color: 'var(--color-fg-subtle)',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          {loading ? (
            <Loader2 size={18} className="text-accent status-dot-pulse" />
          ) : (
            <SearchIcon size={18} />
          )}
        </div>

        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={handleChange}
          placeholder={placeholder}
          disabled={loading}
          style={{
            width: '100%',
            padding: '0.85rem 7.5rem 0.85rem 2.85rem',
            background: 'transparent',
            border: 'none',
            color: 'var(--color-fg)',
            fontFamily: 'var(--font-body)',
            fontSize: '0.9375rem',
            outline: 'none',
          }}
        />

        <div style={{ position: 'absolute', right: '0.4rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          {query && !loading && (
            <button
              type="button"
              onClick={handleClear}
              aria-label="Clear search"
              style={{
                background: 'transparent',
                border: 'none',
                color: 'var(--color-fg-muted)',
                cursor: 'pointer',
                padding: '0.25rem',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: '50%',
              }}
            >
              <X size={15} />
            </button>
          )}

          <button
            type="submit"
            disabled={loading}
            className="btn btn-primary btn-sm"
          >
            <span>Search</span>
            <ArrowRight size={13} />
          </button>
        </div>
      </div>
    </form>
  );
}
