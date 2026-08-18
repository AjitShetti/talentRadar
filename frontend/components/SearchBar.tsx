'use client';

import { MagnifyingGlass, X } from '@phosphor-icons/react';

interface SearchBarProps {
  value: string;
  onChange: (val: string) => void;
  onSearch: () => void;
  loading?: boolean;
  placeholder?: string;
}

export default function SearchBar({ value, onChange, onSearch, loading, placeholder }: SearchBarProps) {
  return (
    <div
      style={{
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
      }}
    >
      <MagnifyingGlass
        size={18}
        style={{
          position: 'absolute',
          left: '0.875rem',
          color: 'var(--text-muted)',
          flexShrink: 0,
          pointerEvents: 'none',
        }}
      />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && onSearch()}
        placeholder={placeholder || 'Search roles, companies, skills...'}
        style={{ paddingLeft: '2.75rem', paddingRight: value ? '6rem' : '7rem' }}
        aria-label="Job search"
      />
      {value && (
        <button
          onClick={() => onChange('')}
          style={{
            position: 'absolute',
            right: '5.5rem',
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--text-muted)',
            padding: '0.25rem',
            lineHeight: 0,
          }}
          aria-label="Clear search"
        >
          <X size={15} />
        </button>
      )}
      <button
        onClick={onSearch}
        disabled={loading}
        style={{
          position: 'absolute',
          right: '0.5rem',
          padding: '0.375rem 0.875rem',
          background: 'var(--accent)',
          color: '#fff',
          border: 'none',
          borderRadius: 'var(--radius-sm)',
          fontSize: '0.875rem',
          fontWeight: 500,
          cursor: loading ? 'not-allowed' : 'pointer',
          opacity: loading ? 0.7 : 1,
          transition: 'background 150ms ease',
          whiteSpace: 'nowrap',
        }}
        onMouseEnter={(e) => !loading && (e.currentTarget.style.background = 'var(--accent-hover)')}
        onMouseLeave={(e) => !loading && (e.currentTarget.style.background = 'var(--accent)')}
      >
        {loading ? 'Searching...' : 'Search'}
      </button>
    </div>
  );
}
