'use client';

import { useState } from 'react';
import { Search as SearchIcon, Loader2 } from 'lucide-react';

interface SearchBarProps {
  onSearch: (query: string) => void;
  placeholder?: string;
  loading?: boolean;
}

export default function SearchBar({ onSearch, placeholder = 'Search jobs...', loading = false }: SearchBarProps) {
  const [query, setQuery] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) {
      onSearch(query.trim());
    }
  };

  return (
    <form onSubmit={handleSubmit} style={{ width: '100%', position: 'relative' }}>
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
        <div style={{ position: 'absolute', left: '1rem', pointerEvents: 'none', color: 'var(--color-fg-muted)' }}>
          {loading ? (
            <Loader2 className="animate-spin text-accent" size={20} />
          ) : (
            <SearchIcon size={20} />
          )}
        </div>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={placeholder}
          disabled={loading}
          style={{
            width: '100%',
            padding: '1rem 8rem 1rem 3rem',
            background: 'rgba(0,0,0,0.5)',
            border: '1px solid var(--color-border)',
            color: 'var(--color-fg)',
            fontFamily: 'var(--font-body)',
            fontSize: '1rem'
          }}
        />
        <button
          type="submit"
          disabled={loading || !query.trim()}
          className="btn btn-primary"
          style={{ position: 'absolute', right: '0.5rem', padding: '0.5rem 1rem' }}
        >
          SEARCH
        </button>
      </div>
    </form>
  );
}
