import { useEffect, useState } from 'react';
import { apiClient } from '../api/client';
import { CoverImage } from './CoverImage';
import type { CatalogCollection, DownloadTarget, ReadingPathDetail } from '../api/types';

const SEARCH_LIMIT = 12;
const DEBOUNCE_MS = 200;

type ListItemPickerProps = {
  onAdd: (targets: DownloadTarget[]) => void;
  /** Entry ids already in the open list, so they can be shown as added. */
  existingEntryIds: Set<string>;
};

/** Search the catalogue and add issues without leaving the list. */
export function ListItemPicker({ onAdd, existingEntryIds }: ListItemPickerProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<CatalogCollection[]>([]);
  const [openCollection, setOpenCollection] = useState<ReadingPathDetail | undefined>();
  const [isSearching, setIsSearching] = useState(false);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setResults([]);
      return undefined;
    }
    let cancelled = false;
    setIsSearching(true);
    const timer = window.setTimeout(() => {
      apiClient
        .getCatalogCollections({ search: trimmed }, SEARCH_LIMIT, 0)
        .then((page) => {
          if (!cancelled) setResults(page.items);
        })
        .catch(() => {
          if (!cancelled) setResults([]);
        })
        .finally(() => {
          if (!cancelled) setIsSearching(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  const openDetail = (collection: CatalogCollection) => {
    if (!collection.readingPathId) return;
    apiClient.getReadingPath(collection.readingPathId).then(setOpenCollection).catch(() => setOpenCollection(undefined));
  };

  const entryTargets = (detail: ReadingPathDetail, entryIds?: string[]) =>
    detail.entries
      .filter((entry) => (entryIds ? entryIds.includes(entry.id) : true))
      .map((entry) => ({
        readingPathId: detail.id,
        entryId: entry.id,
        title: entry.canonicalIssue?.title ?? entry.label ?? `Entry ${entry.id}`,
      }));

  return (
    <div className="picker">
      <div className="picker__search">
        <input
          type="search"
          value={query}
          placeholder="Search the catalogue to add issues"
          spellCheck={false}
          onChange={(event) => setQuery(event.target.value)}
        />
        {openCollection ? (
          <button type="button" className="text-button" onClick={() => setOpenCollection(undefined)}>
            Back to results
          </button>
        ) : null}
      </div>

      {openCollection ? (
        <div className="picker__detail">
          <div className="picker__detail-head">
            <h3>{openCollection.title}</h3>
            <button type="button" className="text-button" onClick={() => onAdd(entryTargets(openCollection))}>
              Add all {openCollection.entries.length}
            </button>
          </div>
          <ul className="picker__entries">
            {openCollection.entries.map((entry) => {
              const added = existingEntryIds.has(entry.id);
              return (
                <li key={entry.id}>
                  <button
                    type="button"
                    className="picker__entry"
                    disabled={added}
                    onClick={() => onAdd(entryTargets(openCollection, [entry.id]))}
                  >
                    <span className="picker__entry-title">
                      {entry.canonicalIssue?.title ?? entry.label ?? `Entry ${entry.id}`}
                    </span>
                    <span className="picker__entry-action">{added ? 'Added' : '+ Add'}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <>
          {isSearching && results.length === 0 ? <p className="picker__hint">Searching…</p> : null}
          {!isSearching && query.trim().length >= 2 && results.length === 0 ? (
            <p className="picker__hint">Nothing matches that.</p>
          ) : null}
          <ul className="picker__results">
            {results.map((collection) => (
              <li key={collection.id}>
                <button type="button" className="picker__result" onClick={() => openDetail(collection)}>
                  <span className="picker__result-cover">
                    <CoverImage
                      src={collection.coverUrl}
                      alt=""
                      placeholderLabel=""
                      className="picker__result-image"
                    />
                  </span>
                  <span className="picker__result-body">
                    <span className="picker__result-title">{collection.title}</span>
                    <span className="picker__result-meta">
                      {collection.publisher ?? 'Unknown'} · {collection.issueCount} issues
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
