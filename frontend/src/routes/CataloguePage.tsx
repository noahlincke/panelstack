import { useCallback, useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { apiClient } from '../api/client';
import { CatalogFilterBar } from '../components/CatalogFilterBar';
import { CoverImage } from '../components/CoverImage';
import { CATALOG_WINDOW, DEFAULT_PUBLISHERS } from '../lib/catalogWindow';
import type { CatalogCollection, CatalogFacets, CatalogFilterState } from '../api/types';

const PAGE_SIZE = 60;

type CataloguePageProps = {
  searchQuery: string;
};

export function CataloguePage({ searchQuery }: CataloguePageProps) {
  const [facets, setFacets] = useState<CatalogFacets | undefined>();
  const [filters, setFilters] = useState<CatalogFilterState>({ ...CATALOG_WINDOW, publisher: [...DEFAULT_PUBLISHERS] });
  const [collections, setCollections] = useState<CatalogCollection[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    apiClient.getCatalogFacets().then(setFacets).catch(() => setFacets(undefined));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    const query = { ...filters, search: searchQuery || undefined };
    apiClient
      .getCatalogCollections(query, PAGE_SIZE, 0)
      .then((page) => {
        if (cancelled) return;
        setCollections(page.items);
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

  const loadMore = useCallback(() => {
    apiClient
      .getCatalogCollections({ ...filters, search: searchQuery || undefined }, PAGE_SIZE, collections.length)
      .then((page) => setCollections((current) => [...current, ...page.items]))
      .catch((cause: Error) => setError(cause.message));
  }, [collections.length, filters, searchQuery]);

  return (
    <section className="view view--catalogue">
      <header className="view__header">
        <h1>Catalogue</h1>
        <p className="view__lede">
          Curated DC and Marvel runs and collected editions, {CATALOG_WINDOW.start.slice(0, 4)} through{' '}
          {CATALOG_WINDOW.end}.
        </p>
      </header>

      <CatalogFilterBar
        facets={facets}
        value={filters}
        onChange={setFilters}
        resultLabel={`${total} ${total === 1 ? 'collection' : 'collections'}`}
      />

      {error ? <p className="view__error">{error}</p> : null}
      {isLoading && collections.length === 0 ? <p className="view__empty">Loading catalogue…</p> : null}
      {!isLoading && collections.length === 0 && !error ? (
        <p className="view__empty">No collections match these filters.</p>
      ) : null}

      <div className="poster-grid" style={{ '--poster-min-width': '132px' } as CSSProperties}>
        {collections.map((collection) => (
          <article className="poster-tile" key={collection.id}>
            <Link
              to={collection.readingPathId ? `/all/${collection.readingPathId}` : '/all'}
              className="poster-tile__media"
            >
              <CoverImage
                src={collection.coverUrl}
                alt={`${collection.title} cover`}
                placeholderLabel={collection.publisher ?? 'Cover pending'}
              />
            </Link>
            <h2 className="poster-tile__title">
              <Link to={collection.readingPathId ? `/all/${collection.readingPathId}` : '/all'}>
                {collection.title}
              </Link>
            </h2>
            <div className="poster-tile__meta">
              <span>
                {collection.issueCount} {collection.issueCount === 1 ? 'issue' : 'issues'}
              </span>
              <span>{collection.firstPublishedOn?.slice(0, 4) ?? '—'}</span>
            </div>
          </article>
        ))}
      </div>

      {collections.length < total ? (
        <div className="view__more">
          <button type="button" className="text-button" onClick={loadMore}>
            Show more ({total - collections.length} remaining)
          </button>
        </div>
      ) : null}
    </section>
  );
}
