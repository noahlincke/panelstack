import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { DEFAULT_PUBLISHERS } from './catalogWindow';
import type { CatalogFilterState } from '../api/types';

/**
 * Filters live in the URL so that clicking into a collection and coming back
 * lands on the same view. Without this, every trip through a detail page reset
 * the board and the filters had to be re-picked by hand.
 *
 * Only what differs from the default is written, which keeps a plain /chronology
 * link short and makes "did the user choose this?" answerable from the URL.
 */
const DEFAULT_PUBLISHER_PARAM = DEFAULT_PUBLISHERS.join(',');

export function filtersToParams(filters: CatalogFilterState): Record<string, string> {
  const params: Record<string, string> = {};
  const publisher = filters.publisher?.join(',');
  if (publisher !== DEFAULT_PUBLISHER_PARAM) {
    // An explicit empty value distinguishes "all publishers" from "not chosen".
    params.publisher = publisher ?? '';
  }
  if (filters.line) params.line = filters.line;
  if (filters.character) params.character = filters.character;
  if (filters.start) params.start = filters.start;
  if (filters.end) params.end = filters.end;
  if (filters.owned !== undefined) params.owned = filters.owned ? '1' : '0';
  return params;
}

export function filtersFromParams(params: URLSearchParams): CatalogFilterState {
  const publisher = params.get('publisher');
  const owned = params.get('owned');
  return {
    publisher:
      publisher === null ? [...DEFAULT_PUBLISHERS] : publisher === '' ? undefined : publisher.split(','),
    line: params.get('line') ?? undefined,
    character: params.get('character') ?? undefined,
    start: params.get('start') ?? undefined,
    end: params.get('end') ?? undefined,
    owned: owned === null ? undefined : owned === '1',
  };
}

type FilterParams<Extra extends Record<string, string>> = {
  filters: CatalogFilterState;
  extra: Extra;
  setFilters: (next: CatalogFilterState) => void;
  setExtra: (next: Partial<Extra>) => void;
};

/**
 * `extras` names the non-filter state a page also wants in the URL — the
 * chronology's view mode and lane picks — with the value used when absent.
 */
export function useFilterParams<Extra extends Record<string, string>>(extras: Extra): FilterParams<Extra> {
  const [searchParams, setSearchParams] = useSearchParams();

  const filters = useMemo(() => filtersFromParams(searchParams), [searchParams]);
  const extra = useMemo(() => {
    const resolved = { ...extras };
    (Object.keys(extras) as (keyof Extra)[]).forEach((key) => {
      const value = searchParams.get(String(key));
      if (value !== null) resolved[key] = value as Extra[keyof Extra];
    });
    return resolved;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- extras is a literal
  }, [searchParams]);

  const write = useCallback(
    (nextFilters: CatalogFilterState, nextExtra: Extra) => {
      const params = filtersToParams(nextFilters);
      (Object.keys(extras) as (keyof Extra)[]).forEach((key) => {
        const value = nextExtra[key];
        if (value !== extras[key]) params[String(key)] = String(value);
      });
      // Replacing keeps the back button pointing at the previous page rather
      // than walking back through every filter click.
      setSearchParams(params, { replace: true });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- extras is a literal
    [setSearchParams],
  );

  return {
    filters,
    extra,
    setFilters: (next: CatalogFilterState) => write(next, extra),
    setExtra: (next: Partial<Extra>) => write(filters, { ...extra, ...next }),
  };
}
