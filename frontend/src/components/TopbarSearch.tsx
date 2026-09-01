import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiClient } from '../api/client';
import { CoverImage } from './CoverImage';
import type { CatalogCollection } from '../api/types';

const SUGGESTION_LIMIT = 8;
const DEBOUNCE_MS = 180;

type TopbarSearchProps = {
  query: string;
  onQueryChange: (value: string) => void;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  icon: React.ReactNode;
};

export function TopbarSearch({ query, onQueryChange, isOpen, onOpenChange, icon }: TopbarSearchProps) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [suggestions, setSuggestions] = useState<CatalogCollection[]>([]);

  useEffect(() => {
    if (isOpen) {
      inputRef.current?.focus();
    }
  }, [isOpen]);

  useEffect(() => {
    function handlePointerDown(event: PointerEvent) {
      const target = event.target as Node | null;
      if (containerRef.current && target && !containerRef.current.contains(target)) {
        onOpenChange(false);
      }
    }
    if (!isOpen) {
      return undefined;
    }
    window.addEventListener('pointerdown', handlePointerDown);
    return () => window.removeEventListener('pointerdown', handlePointerDown);
  }, [isOpen, onOpenChange]);

  useEffect(() => {
    const trimmed = query.trim();
    if (!isOpen || trimmed.length < 2) {
      setSuggestions([]);
      return undefined;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      apiClient
        .getCatalogCollections({ search: trimmed }, SUGGESTION_LIMIT, 0)
        .then((page) => {
          if (!cancelled) setSuggestions(page.items);
        })
        .catch(() => {
          if (!cancelled) setSuggestions([]);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [isOpen, query]);

  const openCollection = (collection: CatalogCollection) => {
    onOpenChange(false);
    onQueryChange('');
    navigate(collection.readingPathId ? `/collections/${collection.readingPathId}` : '/catalog');
  };

  return (
    <div ref={containerRef} className={`topbar-search ${isOpen ? 'topbar-search--open' : ''}`}>
      <button
        type="button"
        className={`topbar-search__trigger nav-link nav-link--icon ${isOpen || query ? 'nav-link--active' : ''}`}
        aria-label="Search collections"
        aria-expanded={isOpen}
        onClick={() => onOpenChange(!isOpen)}
      >
        {icon}
        <span className="sr-only">Search</span>
      </button>

      <div className="topbar-search__panel" aria-hidden={!isOpen}>
        <span className="topbar-search__panel-icon" aria-hidden="true">
          {icon}
        </span>
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              onOpenChange(false);
            }
            if (event.key === 'Enter' && suggestions.length > 0) {
              openCollection(suggestions[0]);
            }
          }}
          placeholder="Search collections"
          aria-label="Search collections"
          spellCheck={false}
          tabIndex={isOpen ? 0 : -1}
        />
        {query ? (
          <button type="button" className="topbar-search__clear" aria-label="Clear search" onClick={() => onQueryChange('')}>
            ×
          </button>
        ) : null}
      </div>

      {isOpen && suggestions.length > 0 ? (
        <ul className="topbar-search__suggestions">
          {suggestions.map((collection) => (
            <li key={collection.id}>
              <button type="button" className="topbar-search__suggestion" onClick={() => openCollection(collection)}>
                <span className="topbar-search__suggestion-cover">
                  <CoverImage
                    src={collection.coverUrl}
                    alt=""
                    placeholderLabel=""
                    className="topbar-search__suggestion-image"
                  />
                </span>
                <span className="topbar-search__suggestion-body">
                  <span className="topbar-search__suggestion-title">{collection.title}</span>
                  <span className="topbar-search__suggestion-meta">
                    {collection.publisher ?? 'Unknown publisher'} · {collection.issueCount} issues
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
