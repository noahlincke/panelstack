import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { characterIcon } from '../data/catalogFilters';
import { CoverImage } from './CoverImage';
import type { CatalogCollection, CatalogFacet } from '../api/types';

/** Lanes that come from a collection's line rather than its character tags. */
const LINE_LANES: { id: string; label: string }[] = [
  { id: 'line:event', label: 'Events' },
  { id: 'line:absolute', label: 'Absolute' },
  { id: 'line:ultimate', label: 'Ultimate' },
];

export type Lane = {
  id: string;
  label: string;
  iconSrc?: string;
};

export function availableLanes(characters: CatalogFacet[]): Lane[] {
  return [
    ...characters.map((character) => ({
      id: character.value,
      label: character.label,
      iconSrc: characterIcon(character.value),
    })),
    ...LINE_LANES,
  ];
}

function inLane(collection: CatalogCollection, laneId: string): boolean {
  if (laneId.startsWith('line:')) {
    return collection.line === laneId.slice('line:'.length);
  }
  return collection.tags.includes(laneId);
}

type ChronologyLanesProps = {
  collections: CatalogCollection[];
  lanes: Lane[];
};

type Placed = {
  collection: CatalogCollection;
  /** True in the year the run began, false in the years it merely continues into. */
  starts: boolean;
};

function yearSpan(collection: CatalogCollection): number[] {
  // startYear falls back to the series' run years, which is all manga has —
  // without it every non-DC/Marvel publisher fell off the board entirely.
  const first = collection.startYear ?? Number(collection.firstPublishedOn?.slice(0, 4));
  if (!first) return [];
  const last = collection.endYear ?? (Number(collection.latestPublishedOn?.slice(0, 4)) || first);
  const years = [];
  for (let year = first; year <= Math.max(first, last); year += 1) {
    years.push(year);
  }
  return years;
}

/**
 * A year-by-lane board. A run appears in every year it was publishing, marked in
 * the year it began, so a column reads as both "what started when" and "what was
 * running then" — a run that launched in 2024 and is still going shows up in
 * 2026 rather than vanishing from it.
 */
export function ChronologyLanes({ collections, lanes }: ChronologyLanesProps) {
  const years = useMemo(() => {
    const seen = new Set<number>();
    collections.forEach((collection) => yearSpan(collection).forEach((year) => seen.add(year)));
    return [...seen].sort((a, b) => b - a);
  }, [collections]);

  const cells = useMemo(() => {
    const byCell = new Map<string, Placed[]>();
    collections.forEach((collection) => {
      const span = yearSpan(collection);
      lanes.forEach((lane) => {
        if (!inLane(collection, lane.id)) return;
        span.forEach((year, index) => {
          const key = `${year}:${lane.id}`;
          byCell.set(key, [...(byCell.get(key) ?? []), { collection, starts: index === 0 }]);
        });
      });
    });
    // Runs that begin in a year lead that year's cell.
    byCell.forEach((placed) => placed.sort((a, b) => Number(b.starts) - Number(a.starts)));
    return byCell;
  }, [collections, lanes]);

  if (lanes.length === 0) {
    return <p className="view__empty">Pick at least one lane to compare.</p>;
  }

  return (
    <div className="lanes" style={{ '--lane-count': lanes.length } as React.CSSProperties}>
      <div className="lanes__head">
        <div className="lanes__gutter lanes__gutter--head" />
        {lanes.map((lane) => (
          <div className="lanes__column-head" key={lane.id}>
            {lane.iconSrc ? <img className="lanes__column-icon" src={lane.iconSrc} alt="" aria-hidden="true" /> : null}
            <span>{lane.label}</span>
          </div>
        ))}
      </div>

      {years.map((year) => (
        <div className="lanes__row" key={year}>
          <div className="lanes__gutter">{year}</div>
          {lanes.map((lane) => {
            const entries = cells.get(`${year}:${lane.id}`) ?? [];
            return (
              <div className="lanes__cell" key={lane.id}>
                {entries.map(({ collection, starts }) => (
                  <Link
                    className={[
                      'lane-card',
                      starts ? 'lane-card--starts' : 'lane-card--continues',
                      collection.ownedCount > 0 ? 'lane-card--owned' : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                    to={collection.readingPathId ? `/collections/${collection.readingPathId}` : '/catalog'}
                    key={`${collection.id}-${starts ? 'start' : 'cont'}`}
                  >
                    <span className="lane-card__cover">
                      <CoverImage
                        src={collection.coverUrl}
                        alt=""
                        placeholderLabel=""
                        className="lane-card__image"
                      />
                    </span>
                    <span className="lane-card__body">
                      <span className="lane-card__title">{collection.title}</span>
                      <span className="lane-card__meta">
                        {starts ? `${collection.issueCount} issues` : 'continues'}
                        {collection.ownedCount > 0 ? ` · ${collection.ownedCount} owned` : ''}
                      </span>
                    </span>
                  </Link>
                ))}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
