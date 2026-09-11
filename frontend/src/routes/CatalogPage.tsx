import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiClient } from '../api/client';
import { CatalogFilterBar } from '../components/CatalogFilterBar';
import { CoverImage } from '../components/CoverImage';
import { useFilterParams } from '../lib/filterParams';
import type { CatalogCollection, CatalogFacets } from '../api/types';

const PAGE_SIZE = 60;

type CatalogPageProps = {
  searchQuery: string;
  refreshToken: number;
};

export function CatalogPage({ searchQuery, refreshToken }: CatalogPageProps) {
  const [facets, setFacets] = useState<CatalogFacets | undefined>();
  const { filters, setFilters } = useFilterParams({});
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
  }, [filters, refreshToken, searchQuery]);

  const loadMore = useCallback(() => {
    apiClient
      .getCatalogCollections({ ...filters, search: searchQuery || undefined }, PAGE_SIZE, collections.length)
      .then((page) => setCollections((current) => [...current, ...page.items]))
      .catch((cause: Error) => setError(cause.message));
  }, [collections.length, filters, searchQuery]);

  return (
    <section className="view view--catalog">
      <CatalogFilterBar
        facets={facets}
        value={filters}
        onChange={setFilters}
        resultLabel={`${total} ${total === 1 ? 'collection' : 'collections'}`}
      />

      {error ? <p className="view__error">{error}</p> : null}
      {isLoading && collections.length === 0 ? <p className="view__empty">Loading catalog…</p> : null}
      {!isLoading && collections.length === 0 && !error ? (
        <p className="view__empty">No collections match these filters.</p>
      ) : null}

      <div className="poster-grid">
        {collections.map((collection) => (
          <article className="poster-tile" key={collection.id}>
            <Link
              to={collection.readingPathId ? `/collections/${collection.readingPathId}` : '/catalog'}
              className="poster-tile__media"
            >
              <CoverImage
                src={collection.coverUrl}
                alt={`${collection.title} cover`}
                placeholderLabel={collection.publisher ?? 'Cover pending'}
              />
            </Link>
            <h2 className="poster-tile__title">
              <Link to={collection.readingPathId ? `/collections/${collection.readingPathId}` : '/catalog'}>
                {collection.title}
              </Link>
            </h2>
            <div className="poster-tile__meta">
              <span>
                {collection.issueCount} {collection.issueCount === 1 ? 'issue' : 'issues'}
              </span>
              <span>{collection.startYear ?? '—'}</span>
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
