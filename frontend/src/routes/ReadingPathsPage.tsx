import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { apiClient } from '../api/client';
import type { ReadingPath, ReadingPathCover } from '../api/types';
import { ReadingPathPoster } from '../components/ReadingPathPoster';
import { useShellSettingsAction } from '../components/Shell';
import { ViewSettingsDrawer } from '../components/ViewSettingsDrawer';
import { FILTER_GROUPS, matchesAnyCatalogFilter, matchesCatalogFilters } from '../data/catalogFilters';
import { usePersistentPosterSize } from '../lib/usePersistentPosterSize';

const SORT_OPTIONS = [
  { value: 'latest_published_desc', label: 'Newest issues first', hint: 'Current runs rise to the top.' },
  { value: 'latest_published_asc', label: 'Earliest issues first', hint: 'Start at the beginning of each run.' },
  { value: 'title', label: 'Title A-Z', hint: 'Alphabetical browse.' },
] as const;
const INITIAL_VISIBLE_COUNT = 8;
const LOAD_MORE_COUNT = 8;
const PRELOAD_AHEAD_COUNT = 6;

type ReadingPathsPageProps = {
  onLibraryMutated: () => void;
  searchQuery: string;
};

export function ReadingPathsPage({ onLibraryMutated, searchQuery }: ReadingPathsPageProps) {
  const [paths, setPaths] = useState<ReadingPath[]>([]);
  const [coversById, setCoversById] = useState<Record<string, ReadingPathCover>>({});
  const [error, setError] = useState<string>('');
  const [sort, setSort] = useState<'title' | 'latest_published_desc' | 'latest_published_asc'>('latest_published_desc');
  const [activeFilterIds, setActiveFilterIds] = useState<string[]>([]);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE_COUNT);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const [posterSize, setPosterSize] = usePersistentPosterSize('reading-path-poster-size', 130);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const deferredSearchQuery = useDeferredValue(searchQuery);
  const openSettings = useCallback(() => {
    setIsSettingsOpen(true);
  }, []);
  const shellSettingsAction = useMemo(
    () => ({ label: 'Open view settings', onClick: openSettings }),
    [openSettings],
  );

  useShellSettingsAction(shellSettingsAction);

  useEffect(() => {
    let mounted = true;
    setError('');
    apiClient.listReadingPaths(sort).then((items) => {
      if (mounted) {
        setPaths(items);
      }
    }).catch((reason: unknown) => {
      if (mounted) {
        setError(reason instanceof Error ? reason.message : 'Unable to load reading paths.');
      }
    });
    return () => {
      mounted = false;
    };
  }, [sort]);

  const normalizedQuery = deferredSearchQuery.trim().toLowerCase();
  const isGlobalSearch = normalizedQuery.length > 0;
  function matchesSearch(path: ReadingPath): boolean {
    if (!isGlobalSearch) {
      return true;
    }
    const searchable = [
      path.title,
      path.publisher,
      path.sourceName,
      path.latestIssueLabel,
      path.eventTitle,
      ...(path.tags ?? []),
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return searchable.includes(normalizedQuery);
  }
  const visiblePaths = paths.filter((path) => {
    if (!matchesSearch(path)) {
      return false;
    }
    if (isGlobalSearch) {
      return true;
    }
    const matchesFilter = matchesCatalogFilters(path, activeFilterIds);
    return matchesFilter && matchesAnyCatalogFilter(path) && path.line !== 'event';
  });
  const renderedPaths = visiblePaths.slice(0, visibleCount);

  useEffect(() => {
    setVisibleCount(Math.min(INITIAL_VISIBLE_COUNT, visiblePaths.length));
  }, [activeFilterIds, normalizedQuery, sort, visiblePaths.length]);

  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || visibleCount >= visiblePaths.length) {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const [entry] = entries;
        if (entry?.isIntersecting) {
          setVisibleCount((current) => Math.min(current + LOAD_MORE_COUNT, visiblePaths.length));
        }
      },
      { rootMargin: '1200px 0px' },
    );
    observer.observe(node);
    return () => {
      observer.disconnect();
    };
  }, [visibleCount, visiblePaths.length]);

  useEffect(() => {
    const preloadTarget = visiblePaths.slice(0, Math.min(visibleCount + PRELOAD_AHEAD_COUNT, visiblePaths.length));
    const missingIds = preloadTarget.map((path) => path.id).filter((id) => !coversById[id]);
    if (missingIds.length === 0) {
      return;
    }

    let cancelled = false;
    apiClient.getReadingPathCovers(missingIds).then((payload) => {
      if (cancelled) {
        return;
      }
      setCoversById((current) => ({ ...current, ...payload }));
      Object.values(payload).forEach((cover) => {
        if (!cover.imageUrl) {
          return;
        }
        const image = new Image();
        image.src = cover.imageUrl;
      });
    }).catch(() => {
      if (cancelled) {
        return;
      }
    });

    return () => {
      cancelled = true;
    };
  }, [coversById, visibleCount, visiblePaths]);

  return (
    <section className="view">
      <div className="catalog-toolbar">
        <div className="catalog-toolbar__left">
          <div className="filter-groups" aria-label="Publisher and character filters">
            {FILTER_GROUPS.map((group) => {
              const activeCount = group.items.filter((item) => activeFilterIds.includes(item.id)).length;
              const isGroupActive = activeCount > 0;
              const areAllActive = activeCount === group.items.length;
              return (
                <div key={group.id} className={`filter-group-card ${isGroupActive ? 'filter-group-card--active' : ''}`}>
                  <div className="filter-group-card__head">
                    <button
                      type="button"
                      className={`publisher-filter publisher-filter--brand ${isGroupActive ? 'publisher-filter--active' : ''}`}
                      aria-pressed={isGroupActive}
                      aria-label={`${group.label} filters`}
                      onClick={() => {
                        setActiveFilterIds((current) => {
                          const withoutGroup = current.filter((value) => !group.items.some((item) => item.id === value));
                          if (areAllActive) {
                            return withoutGroup;
                          }
                          return [...withoutGroup, ...group.items.map((item) => item.id)];
                        });
                      }}
                    >
                      {group.id === 'dc' ? <DcLogo /> : group.id === 'marvel' ? <MarvelLogo /> : <AnimeLogo />}
                    </button>
                    <button
                      type="button"
                      className="publisher-filter publisher-filter--expand"
                      aria-label={`${expandedGroups[group.id] ? 'Collapse' : 'Expand'} ${group.label} filters`}
                      onClick={() =>
                        setExpandedGroups((current) => ({ ...current, [group.id]: !current[group.id] }))
                      }
                    >
                      <ChevronIcon open={Boolean(expandedGroups[group.id])} />
                    </button>
                  </div>
                  {expandedGroups[group.id] ? (
                    <div className="filter-group-card__items">
                      {group.items.map((item) => {
                        const isActive = activeFilterIds.includes(item.id);
                        return (
                          <button
                            key={item.id}
                            type="button"
                            className={`character-filter ${isActive ? 'character-filter--active' : ''}`}
                            aria-pressed={isActive}
                            aria-label={item.label}
                            title={item.label}
                            onClick={() =>
                              setActiveFilterIds((current) =>
                                current.includes(item.id)
                                  ? current.filter((value) => value !== item.id)
                                  : [...current, item.id],
                              )
                            }
                          >
                            <img
                              src={item.imageSrc}
                              alt=""
                              aria-hidden="true"
                              className="character-filter__image"
                              loading="lazy"
                            />
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <ViewSettingsDrawer
        open={isSettingsOpen}
        title="All settings"
        sort={sort}
        sortOptions={SORT_OPTIONS}
        onClose={() => setIsSettingsOpen(false)}
        onSortChange={(value) => setSort(value as typeof sort)}
        posterSize={posterSize}
        onPosterSizeChange={setPosterSize}
      />

      {error ? (
        <div className="empty-state">{error}</div>
      ) : renderedPaths.length > 0 ? (
        <>
          <div className="poster-grid" style={{ '--poster-min-width': `${posterSize}px` } as CSSProperties}>
            {renderedPaths.map((path) => (
              <ReadingPathPoster
                key={path.id}
                path={path}
                cover={coversById[path.id]}
                onLibraryMutated={onLibraryMutated}
              />
            ))}
          </div>
          <div ref={sentinelRef} className="reading-path-poster-sentinel" aria-hidden="true" />
        </>
      ) : (
        <div className="empty-state">
          {normalizedQuery ? 'No collections match that search.' : 'No available series match the current filters.'}
        </div>
      )}
    </section>
  );
}

function MarvelLogo() {
  return (
    <svg viewBox="0 0 94 28" aria-hidden="true" className="publisher-logo publisher-logo--marvel">
      <rect width="94" height="28" rx="6" fill="#ed1d24" />
      <text x="47" y="19" textAnchor="middle" fill="#fff" fontSize="16" fontWeight="900" letterSpacing="-0.8">
        MARVEL
      </text>
    </svg>
  );
}

function AnimeLogo() {
  return (
    <svg viewBox="0 0 94 28" aria-hidden="true" className="publisher-logo publisher-logo--anime">
      <rect width="94" height="28" rx="6" fill="#111" />
      <text x="47" y="19" textAnchor="middle" fill="#fff" fontSize="15" fontWeight="900" letterSpacing="1.1">
        ANIME
      </text>
    </svg>
  );
}

function DcLogo() {
  return (
    <svg viewBox="0 0 86 28" aria-hidden="true" className="publisher-logo publisher-logo--dc">
      <circle cx="14" cy="14" r="12" fill="#1175f7" />
      <text x="14" y="18" textAnchor="middle" fill="#fff" fontSize="12" fontWeight="800" letterSpacing="-0.6">
        DC
      </text>
      <text x="32" y="18" fill="#17457b" fontSize="11" fontWeight="800" letterSpacing="0.6">
        COMICS
      </text>
    </svg>
  );
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={`chevron-icon ${open ? 'chevron-icon--open' : ''}`}>
      <path
        d="M7 10.5l5 5 5-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
