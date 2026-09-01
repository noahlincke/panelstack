import { characterIcon } from '../data/catalogFilters';
import { CATALOG_WINDOW, DEFAULT_PUBLISHERS } from '../lib/catalogWindow';
import type { CatalogFacet, CatalogFacets, CatalogFilterState } from '../api/types';

const CURATED_SCOPE = 'curated';

/** Every browsing surface shows the same groups in the same order. */
const WINDOW_OPTIONS: { value: string; label: string; start?: string }[] = [
  { value: 'modern', label: '2019 →', start: CATALOG_WINDOW.start },
  { value: 'recent', label: '2024 →', start: '2024-01-01' },
  { value: 'all', label: 'All years' },
];

function publisherScope(selected?: string[]): string | undefined {
  if (!selected) {
    return undefined;
  }
  if (selected.length === DEFAULT_PUBLISHERS.length && DEFAULT_PUBLISHERS.every((slug) => selected.includes(slug))) {
    return CURATED_SCOPE;
  }
  return selected[0];
}

function windowScope(start?: string): string {
  return WINDOW_OPTIONS.find((option) => option.start === start)?.value ?? 'all';
}

type ChipProps = {
  label: string;
  hint?: string;
  iconSrc?: string;
  count?: number;
  isActive: boolean;
  onClick: () => void;
};

function FilterChip({ label, hint, iconSrc, count, isActive, onClick }: ChipProps) {
  return (
    <button type="button" className="filter-chip" aria-pressed={isActive} onClick={onClick}>
      {iconSrc ? <img className="filter-chip__icon" src={iconSrc} alt="" aria-hidden="true" /> : null}
      <span className="filter-chip__text">
        <span className="filter-chip__label">{label}</span>
        {hint ? <span className="filter-chip__hint">{hint}</span> : null}
      </span>
      {count === undefined ? null : <span className="filter-chip__count">{count}</span>}
    </button>
  );
}

type FilterGroupProps = {
  label: string;
  children: React.ReactNode;
};

function FilterGroup({ label, children }: FilterGroupProps) {
  return (
    <div className="filter-group">
      <span className="filter-group__label">{label}</span>
      <div className="filter-group__options">{children}</div>
    </div>
  );
}

type CatalogFilterBarProps = {
  facets?: CatalogFacets;
  value: CatalogFilterState;
  onChange: (next: CatalogFilterState) => void;
  resultLabel: string;
};

export function defaultCatalogFilters(): CatalogFilterState {
  return { ...CATALOG_WINDOW, publisher: [...DEFAULT_PUBLISHERS] };
}

export function CatalogFilterBar({ facets, value, onChange, resultLabel }: CatalogFilterBarProps) {
  const scope = publisherScope(value.publisher);
  const hasFilters =
    scope !== CURATED_SCOPE ||
    Boolean(value.line || value.character) ||
    value.owned !== undefined ||
    value.start !== CATALOG_WINDOW.start;

  const setWindow = (option: (typeof WINDOW_OPTIONS)[number]) =>
    onChange({ ...value, start: option.start, end: option.start ? CATALOG_WINDOW.end : undefined });

  return (
    <div className="catalog-filters">
      <div className="catalog-filters__head">
        <span className="catalog-filters__count">{resultLabel}</span>
        {hasFilters ? (
          <button type="button" className="text-button" onClick={() => onChange(defaultCatalogFilters())}>
            Reset filters
          </button>
        ) : null}
      </div>

      <FilterGroup label="Library">
        <FilterChip label="Everything" isActive={value.owned === undefined} onClick={() => onChange({ ...value, owned: undefined })} />
        <FilterChip label="In library" isActive={value.owned === true} onClick={() => onChange({ ...value, owned: true })} />
        <FilterChip label="Not yet owned" isActive={value.owned === false} onClick={() => onChange({ ...value, owned: false })} />
      </FilterGroup>

      <FilterGroup label="Publisher">
        <FilterChip
          label="DC + Marvel"
          isActive={scope === CURATED_SCOPE}
          onClick={() => onChange({ ...value, publisher: [...DEFAULT_PUBLISHERS] })}
        />
        <FilterChip label="All publishers" isActive={!scope} onClick={() => onChange({ ...value, publisher: undefined })} />
        {(facets?.publishers ?? []).map((publisher: CatalogFacet) => (
          <FilterChip
            key={publisher.value}
            label={publisher.label}
            count={publisher.count}
            isActive={scope === publisher.value}
            onClick={() =>
              onChange({ ...value, publisher: scope === publisher.value ? undefined : [publisher.value] })
            }
          />
        ))}
      </FilterGroup>

      {(facets?.characters ?? []).length > 0 ? (
        <FilterGroup label="Character / team">
          <FilterChip label="Any" isActive={!value.character} onClick={() => onChange({ ...value, character: undefined })} />
          {(facets?.characters ?? []).map((character: CatalogFacet) => (
            <FilterChip
              key={character.value}
              label={character.label.replace(/ Family$/, '')}
              hint="family"
              iconSrc={characterIcon(character.value)}
              count={character.count}
              isActive={value.character === character.value}
              onClick={() =>
                onChange({ ...value, character: value.character === character.value ? undefined : character.value })
              }
            />
          ))}
        </FilterGroup>
      ) : null}

      <FilterGroup label="Line">
        <FilterChip label="Any" isActive={!value.line} onClick={() => onChange({ ...value, line: undefined })} />
        {(facets?.lines ?? []).map((line: CatalogFacet) => (
          <FilterChip
            key={line.value}
            label={line.label}
            count={line.count}
            isActive={value.line === line.value}
            onClick={() => onChange({ ...value, line: value.line === line.value ? undefined : line.value })}
          />
        ))}
      </FilterGroup>

      <FilterGroup label="Published">
        {WINDOW_OPTIONS.map((option) => (
          <FilterChip
            key={option.value}
            label={option.label}
            isActive={windowScope(value.start) === option.value}
            onClick={() => setWindow(option)}
          />
        ))}
      </FilterGroup>
    </div>
  );
}
