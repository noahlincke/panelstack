import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiClient } from '../api/client';
import { CatalogFilterBar, defaultCatalogFilters } from '../components/CatalogFilterBar';
import { ChronologyLanes, availableLanes } from '../components/ChronologyLanes';
import { CoverImage } from '../components/CoverImage';
import { monthKey, monthLabel } from '../lib/catalogWindow';
import type { CatalogCollection, CatalogFacets, CatalogFilterState, ChronologyEntry } from '../api/types';

const TIMELINE_PAGE_SIZE = 200;
const LANE_PAGE_SIZE = 400;
const DEFAULT_LANE_COUNT = 6;

type ChronologyPageProps = {
  searchQuery: string;
};

type ChronologyView = 'timeline' | 'lanes';

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
  const [view, setView] = useState<ChronologyView>('lanes');
  const [facets, setFacets] = useState<CatalogFacets | undefined>();
  const [filters, setFilters] = useState<CatalogFilterState>(defaultCatalogFilters());
  const [entries, setEntries] = useState<ChronologyEntry[]>([]);
  const [collections, setCollections] = useState<CatalogCollection[]>([]);
  const [total, setTotal] = useState(0);
  const [laneIds, setLaneIds] = useState<string[] | undefined>();
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    apiClient.getCatalogFacets().then(setFacets).catch(() => setFacets(undefined));
  }, []);

  const allLanes = useMemo(() => availableLanes(facets?.characters ?? []), [facets]);

  // Character lanes only exist once facets arrive, so seed the default from those
  // rather than from the line lanes that are present on the first render.
  useEffect(() => {
    if (laneIds === undefined && facets) {
      setLaneIds(allLanes.slice(0, DEFAULT_LANE_COUNT).map((lane) => lane.id));
    }
  }, [allLanes, facets, laneIds]);

  const activeLanes = useMemo(
    () => allLanes.filter((lane) => (laneIds ?? []).includes(lane.id)),
    [allLanes, laneIds],
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
        : apiClient.getCatalogCollections(query, LANE_PAGE_SIZE, 0).then((page) => {
            if (cancelled) return;
            setCollections(page.items);
            setTotal(page.total);
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

  const toggleLane = (laneId: string) =>
    setLaneIds((current) => {
      const active = current ?? [];
      return active.includes(laneId) ? active.filter((id) => id !== laneId) : [...active, laneId];
    });

  return (
    <section className="view view--chronology">
      <header className="view__header">
        <div className="view__header-row">
          <h1>Chronology</h1>
          <div className="view-switch" role="group" aria-label="Chronology view">
            <button type="button" aria-pressed={view === 'lanes'} onClick={() => setView('lanes')}>
              Lanes
            </button>
            <button type="button" aria-pressed={view === 'timeline'} onClick={() => setView('timeline')}>
              Timeline
            </button>
          </div>
        </div>
        <p className="view__lede">
          {view === 'timeline'
            ? 'Every curated issue in publication order, newest first — for catching up on the last few months.'
            : 'One column per character, team or line, stacked by year — for finding a jumping-on point.'}
        </p>
      </header>

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
                aria-pressed={(laneIds ?? []).includes(lane.id)}
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
          {entries.length === 0 && !error ? <p className="view__empty">No issues match these filters.</p> : null}
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
