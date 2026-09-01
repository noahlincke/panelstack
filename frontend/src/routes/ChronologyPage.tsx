import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiClient } from '../api/client';
import { CatalogFilterBar } from '../components/CatalogFilterBar';
import { CoverImage } from '../components/CoverImage';
import { CATALOG_WINDOW, DEFAULT_PUBLISHERS, monthKey, monthLabel } from '../lib/catalogWindow';
import type { CatalogFacets, CatalogFilterState, ChronologyEntry } from '../api/types';

const PAGE_SIZE = 200;

type ChronologyPageProps = {
  searchQuery: string;
};

type ChronologyMonth = {
  key: string;
  label: string;
  entries: ChronologyEntry[];
};

function groupByMonth(entries: ChronologyEntry[]): ChronologyMonth[] {
  const months = new Map<string, ChronologyMonth>();
  entries.forEach((entry) => {
    const key = monthKey(entry.publishedOn);
    const month = months.get(key) ?? { key, label: monthLabel(entry.publishedOn), entries: [] };
    month.entries.push(entry);
    months.set(key, month);
  });
  return [...months.values()];
}

export function ChronologyPage({ searchQuery }: ChronologyPageProps) {
  const [facets, setFacets] = useState<CatalogFacets | undefined>();
  const [filters, setFilters] = useState<CatalogFilterState>({ ...CATALOG_WINDOW, publisher: [...DEFAULT_PUBLISHERS] });
  const [entries, setEntries] = useState<ChronologyEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    apiClient.getCatalogFacets().then(setFacets).catch(() => setFacets(undefined));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    apiClient
      .getChronology({ ...filters, search: searchQuery || undefined }, PAGE_SIZE, 0)
      .then((page) => {
        if (cancelled) return;
        setEntries(page.items);
        setTotal(page.total);
        setError('');
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(cause.message);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filters, searchQuery]);

  const months = useMemo(() => groupByMonth(entries), [entries]);

  const loadMore = () => {
    apiClient
      .getChronology({ ...filters, search: searchQuery || undefined }, PAGE_SIZE, entries.length)
      .then((page) => setEntries((current) => [...current, ...page.items]))
      .catch((cause: Error) => setError(cause.message));
  };

  return (
    <section className="view view--chronology">
      <header className="view__header">
        <h1>Chronology</h1>
        <p className="view__lede">
          Every curated issue in publication order, newest first, {CATALOG_WINDOW.start.slice(0, 4)} through{' '}
          {CATALOG_WINDOW.end}.
        </p>
      </header>

      <CatalogFilterBar
        facets={facets}
        value={filters}
        onChange={setFilters}
        resultLabel={`${total} ${total === 1 ? 'issue' : 'issues'}`}
      />

      {error ? <p className="view__error">{error}</p> : null}
      {isLoading && entries.length === 0 ? <p className="view__empty">Loading chronology…</p> : null}
      {!isLoading && entries.length === 0 && !error ? (
        <p className="view__empty">No issues match these filters.</p>
      ) : null}

      <ol className="chronology">
        {months.map((month) => (
          <li className="chronology__month" key={month.key}>
            <h2 className="chronology__month-label">
              {month.label}
              <span className="chronology__month-count">{month.entries.length}</span>
            </h2>
            <ol className="chronology__entries">
              {month.entries.map((entry) => (
                <li className="chronology-row" key={entry.canonicalIssueId}>
                  <div className="chronology-row__cover">
                    <CoverImage
                      src={entry.coverUrl}
                      alt=""
                      placeholderLabel=""
                      className="chronology-row__cover-image"
                    />
                  </div>
                  <div className="chronology-row__body">
                    <span className="chronology-row__title">
                      {entry.readingPathId ? (
                        <Link to={`/all/${entry.readingPathId}`}>{entry.title}</Link>
                      ) : (
                        entry.title
                      )}
                    </span>
                    <span className="chronology-row__meta">
                      {entry.collectionTitle}
                      {entry.publisher ? ` · ${entry.publisher}` : ''}
                    </span>
                  </div>
                  <div className="chronology-row__tail">
                    {entry.line !== 'series' ? <span className="chip">{entry.line}</span> : null}
                    <time dateTime={entry.publishedOn}>{entry.publishedOn}</time>
                  </div>
                </li>
              ))}
            </ol>
          </li>
        ))}
      </ol>

      {entries.length < total ? (
        <div className="view__more">
          <button type="button" className="text-button" onClick={loadMore}>
            Show more ({total - entries.length} remaining)
          </button>
        </div>
      ) : null}
    </section>
  );
}
