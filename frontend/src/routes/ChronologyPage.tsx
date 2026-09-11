import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiClient } from '../api/client';
import { CatalogFilterBar } from '../components/CatalogFilterBar';
import { ChronologyLanes, availableLanes, defaultLanes } from '../components/ChronologyLanes';
import { CoverImage } from '../components/CoverImage';
import { monthKey, monthLabel } from '../lib/catalogWindow';
import { useFilterParams } from '../lib/filterParams';
import { useShellViewToggle } from '../components/Shell';
import type { CatalogCollection, CatalogFacets, ChronologyEntry } from '../api/types';

const TIMELINE_PAGE_SIZE = 200;
const LANE_PAGE_SIZE = 500;
const DEFAULT_LANE_COUNT = 6;

type ChronologyPageProps = {
  searchQuery: string;
};

async function fetchAllCollections(query: Parameters<typeof apiClient.getCatalogCollections>[0]) {
  const first = await apiClient.getCatalogCollections(query, LANE_PAGE_SIZE, 0);
  const items = [...first.items];
  while (items.length < first.total) {
    const next = await apiClient.getCatalogCollections(query, LANE_PAGE_SIZE, items.length);
    if (next.items.length === 0) break;
    items.push(...next.items);
  }
  return { items, total: first.total };
}

function groupByMonth(entries: ChronologyEntry[]) {
  const months = new Map<string, { key: string; label: string; entries: ChronologyEntry[] }>();
  entries.forEach((entry) => {
    const key = monthKey(entry.publishedOn);
    const month = months.get(key) ?? { key, label: monthLabel(entry.publishedOn), entries: [] };
    month.entries.push(entry);
    months.set(key, month);
  });
  return [...months.values()];
}

export function ChronologyPage({ searchQuery }: ChronologyPageProps) {
  const { filters, extra, setFilters, setExtra } = useFilterParams({ view: 'lanes', lanes: '' });
  const view = extra.view === 'timeline' ? 'timeline' : 'lanes';
  const [facets, setFacets] = useState<CatalogFacets | undefined>();
  const [entries, setEntries] = useState<ChronologyEntry[]>([]);
  const [collections, setCollections] = useState<CatalogCollection[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    apiClient.getCatalogFacets().then(setFacets).catch(() => setFacets(undefined));
  }, []);

  const allLanes = useMemo(() => availableLanes(facets?.characters ?? []), [facets]);

  // Character lanes only exist once facets arrive, so the default is taken from
  // those rather than from the line lanes present on the first render.
  const laneIds = useMemo(() => {
    if (extra.lanes) return extra.lanes.split(',');
    return defaultLanes(allLanes, collections, DEFAULT_LANE_COUNT).map((lane) => lane.id);
  }, [allLanes, collections, extra.lanes]);

  const activeLanes = useMemo(() => allLanes.filter((lane) => laneIds.includes(lane.id)), [allLanes, laneIds]);

  useShellViewToggle(
    useMemo(
      () => ({
        route: '/chronology',
        ariaLabel: 'Chronology view',
        value: view,
        onChange: (next: string) => setExtra({ view: next }),
        options: [
          { value: 'lanes', label: 'Lanes', icon: 'lanes' as const },
          { value: 'timeline', label: 'Timeline', icon: 'timeline' as const },
        ],
      }),
      [view, setExtra],
    ),
  );

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    const query = { ...filters, search: searchQuery || undefined };
    const request =
      view === 'timeline'
        ? apiClient.getChronology(query, TIMELINE_PAGE_SIZE, 0).then((page) => {
            if (cancelled) return;
            setEntries(page.items);
            setTotal(page.total);
          })
        : // The board needs every collection, not the first page of them. A fixed
          // cap silently dropped whatever did not fit — at 470 collections that
          // was 70 of them, which is why One Piece showed four of its forty.
          fetchAllCollections(query).then(({ items, total: count }) => {
            if (cancelled) return;
            setCollections(items);
            setTotal(count);
          });
    request
      .then(() => {
        if (!cancelled) setError('');
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
  }, [filters, searchQuery, view]);

  const months = useMemo(() => groupByMonth(entries), [entries]);

  const loadMore = () => {
    apiClient
      .getChronology({ ...filters, search: searchQuery || undefined }, TIMELINE_PAGE_SIZE, entries.length)
      .then((page) => setEntries((current) => [...current, ...page.items]))
      .catch((cause: Error) => setError(cause.message));
  };

  const toggleLane = (laneId: string) => {
    const next = laneIds.includes(laneId) ? laneIds.filter((id) => id !== laneId) : [...laneIds, laneId];
    // An empty pick has to be recorded as something, or the URL would fall back
    // to the default lanes and the board would refuse to clear.
    setExtra({ lanes: next.length > 0 ? next.join(',') : 'none' });
  };

  return (
    <section className="view view--chronology">
      <CatalogFilterBar
        facets={facets}
        value={filters}
        onChange={setFilters}
        resultLabel={
          view === 'timeline'
            ? `${total} ${total === 1 ? 'issue' : 'issues'}`
            : `${total} ${total === 1 ? 'collection' : 'collections'}`
        }
      />

      {view === 'lanes' ? (
        <div className="lane-picker">
          <span className="filter-group__label">Lanes</span>
          <div className="filter-group__options">
            {allLanes.map((lane) => (
              <button
                key={lane.id}
                type="button"
                className="filter-chip"
                aria-pressed={laneIds.includes(lane.id)}
                onClick={() => toggleLane(lane.id)}
              >
                {lane.iconSrc ? <img className="filter-chip__icon" src={lane.iconSrc} alt="" aria-hidden="true" /> : null}
                <span className="filter-chip__text">
                  <span className="filter-chip__label">{lane.label}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {error ? <p className="view__error">{error}</p> : null}
      {isLoading ? <p className="view__empty">Loading chronology…</p> : null}

      {!isLoading && view === 'lanes' ? <ChronologyLanes collections={collections} lanes={activeLanes} /> : null}

      {!isLoading && view === 'timeline' ? (
        <>
          {entries.length === 0 && !error ? (
            <p className="view__empty">
              No dated issues match these filters. Manga chapters carry no publication date, so a manga
              publisher only appears in Lanes.
            </p>
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
                        <CoverImage src={entry.coverUrl} alt="" placeholderLabel="" className="chronology-row__cover-image" />
                      </div>
                      <div className="chronology-row__body">
                        <span className="chronology-row__title">
                          {entry.readingPathId ? (
                            <Link to={`/collections/${entry.readingPathId}`}>{entry.title}</Link>
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
        </>
      ) : null}
    </section>
  );
}
