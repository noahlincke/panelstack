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

/**
 * The lanes worth opening the board on: the busiest ones that have actually
 * started something lately.
 *
 * The default used to be the highest-count lanes outright, which put Jujutsu
 * Kaisen on the board — it has plenty of collections but ended in 2024, so its
 * column is empty across every recent year a reader is looking at.
 */
export function defaultLanes(lanes: Lane[], collections: CatalogCollection[], count: number): Lane[] {
  const newest = collections.reduce((latest, collection) => {
    const year = collection.startYear ?? Number(collection.firstPublishedOn?.slice(0, 4));
    return year && year > latest ? year : latest;
  }, 0);
  if (!newest) return lanes.slice(0, count);

  const recent = new Set(
    collections
      .filter((collection) => {
        const year = collection.startYear ?? Number(collection.firstPublishedOn?.slice(0, 4));
        return year && year >= newest - 1;
      })
      .flatMap((collection) => collection.tags),
  );
  const active = lanes.filter((lane) => recent.has(lane.id));
  // Lanes keep their incoming order, which is by collection count.
  return [...active, ...lanes.filter((lane) => !active.includes(lane))].slice(0, count);
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
 * The year a run lands in: when it began.
 *
 * It used to occupy every year it was publishing, greyed out and labelled
 * "continues" in all but the first. For manga that meant each of Hunter x
 * Hunter's seventeen collections appearing in all twenty-nine years of its run,
 * which buried the thing the board is for — what started when.
 *
 * startYear falls back to the series' run years, which is all manga has;
 * without it every non-DC/Marvel publisher fell off the board entirely.
 */
function startYear(collection: CatalogCollection): number | undefined {
  return collection.startYear ?? (Number(collection.firstPublishedOn?.slice(0, 4)) || undefined);
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
    collections.forEach((collection) => {
      const year = startYear(collection);
      if (year) seen.add(year);
    });
    return [...seen].sort((a, b) => b - a);
  }, [collections]);

  const cells = useMemo(() => {
    const byCell = new Map<string, CatalogCollection[]>();
    collections.forEach((collection) => {
      const year = startYear(collection);
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
                    className={['lane-card', collection.ownedCount > 0 ? 'lane-card--owned' : '']
                      .filter(Boolean)
                      .join(' ')}
                    to={collection.readingPathId ? `/collections/${collection.readingPathId}` : '/catalog'}
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
                        {`${collection.issueCount} issues`}
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
