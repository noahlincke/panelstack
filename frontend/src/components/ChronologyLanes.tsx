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
      label: character.label.replace(/ Family$/, ''),
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

/**
 * A year-by-lane board. Reading down a column is one character's run history;
 * reading across a row is everything that launched that year.
 */
export function ChronologyLanes({ collections, lanes }: ChronologyLanesProps) {
  const years = useMemo(() => {
    const seen = new Set<number>();
    collections.forEach((collection) => {
      const year = Number(collection.firstPublishedOn?.slice(0, 4));
      if (year) seen.add(year);
    });
    return [...seen].sort((a, b) => b - a);
  }, [collections]);

  const cells = useMemo(() => {
    const byCell = new Map<string, CatalogCollection[]>();
    collections.forEach((collection) => {
      const year = Number(collection.firstPublishedOn?.slice(0, 4));
      if (!year) return;
      lanes.forEach((lane) => {
        if (!inLane(collection, lane.id)) return;
        const key = `${year}:${lane.id}`;
        byCell.set(key, [...(byCell.get(key) ?? []), collection]);
      });
    });
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
                {entries.map((collection) => (
                  <Link
                    className={`lane-card ${collection.ownedCount > 0 ? 'lane-card--owned' : ''}`}
                    to={collection.readingPathId ? `/collections/${collection.readingPathId}` : '/catalogue'}
                    key={collection.id}
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
                        {collection.issueCount} issues
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
