import { DEFAULT_PUBLISHERS } from '../lib/catalogWindow';
import type { CatalogFacet, CatalogFacets, CatalogFilterState } from '../api/types';

const CURATED_SCOPE = 'curated';

function publisherScope(selected?: string[]): string | undefined {
  if (!selected) {
    return undefined;
  }
  if (selected.length === DEFAULT_PUBLISHERS.length && DEFAULT_PUBLISHERS.every((slug) => selected.includes(slug))) {
    return CURATED_SCOPE;
  }
  return selected[0];
}

type CatalogFilterBarProps = {
  facets?: CatalogFacets;
  value: CatalogFilterState;
  onChange: (next: CatalogFilterState) => void;
  resultLabel: string;
};

type FilterGroupProps = {
  label: string;
  options: CatalogFacet[];
  selected?: string;
  anyLabel?: string;
  leading?: CatalogFacet[];
  onSelect: (value?: string) => void;
};

function FilterGroup({ label, options, selected, anyLabel = 'Any', leading = [], onSelect }: FilterGroupProps) {
  if (options.length === 0) {
    return null;
  }
  return (
    <div className="filter-group">
      <span className="filter-group__label">{label}</span>
      <div className="filter-group__options">
        {leading.map((option) => (
          <button
            key={option.value}
            type="button"
            className="filter-chip"
            aria-pressed={selected === option.value}
            onClick={() => onSelect(option.value)}
          >
            {option.label}
          </button>
        ))}
        <button
          type="button"
          className="filter-chip"
          aria-pressed={!selected}
          onClick={() => onSelect(undefined)}
        >
          {anyLabel}
        </button>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className="filter-chip"
            aria-pressed={selected === option.value}
            onClick={() => onSelect(selected === option.value ? undefined : option.value)}
          >
            {option.label}
            <span className="filter-chip__count">{option.count}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function CatalogFilterBar({ facets, value, onChange, resultLabel }: CatalogFilterBarProps) {
  const hasFilters = publisherScope(value.publisher) !== CURATED_SCOPE || Boolean(value.line || value.character);

  return (
    <div className="catalog-filters">
      <div className="catalog-filters__head">
        <span className="catalog-filters__count">{resultLabel}</span>
        {hasFilters ? (
          <button
            type="button"
            className="text-button"
            onClick={() =>
              onChange({ start: value.start, end: value.end, search: value.search, publisher: [...DEFAULT_PUBLISHERS] })
            }
          >
            Clear filters
          </button>
        ) : null}
      </div>
      <FilterGroup
        label="Publisher"
        options={facets?.publishers ?? []}
        selected={publisherScope(value.publisher)}
        anyLabel="All publishers"
        leading={[{ value: CURATED_SCOPE, label: 'DC + Marvel', count: 0 }]}
        onSelect={(scope) =>
          onChange({
            ...value,
            publisher: scope === CURATED_SCOPE ? [...DEFAULT_PUBLISHERS] : scope ? [scope] : undefined,
          })
        }
      />
      <FilterGroup
        label="Character / team"
        options={facets?.characters ?? []}
        selected={value.character}
        onSelect={(character) => onChange({ ...value, character })}
      />
      <FilterGroup
        label="Line"
        options={facets?.lines ?? []}
        selected={value.line}
        onSelect={(line) => onChange({ ...value, line })}
      />
    </div>
  );
}
