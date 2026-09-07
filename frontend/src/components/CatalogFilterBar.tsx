import { characterIcon } from '../data/catalogFilters';
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

/** An ISO date is stored, but only the year is ever typed. */
function yearOf(isoDate?: string): string {
  return isoDate ? isoDate.slice(0, 4) : '';
}

function startOfYear(year: string): string | undefined {
  return /^\d{4}$/.test(year) ? `${year}-01-01` : undefined;
}

function endOfYear(year: string): string | undefined {
  return /^\d{4}$/.test(year) ? `${year}-12-31` : undefined;
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
  /** Chronology is about what was published, not about what is on disk. */
  showLibraryFilter?: boolean;
};

export function defaultCatalogFilters(): CatalogFilterState {
  return { publisher: [...DEFAULT_PUBLISHERS] };
}

export function CatalogFilterBar({
  facets,
  value,
  onChange,
  resultLabel,
  showLibraryFilter = true,
}: CatalogFilterBarProps) {
  const scope = publisherScope(value.publisher);
  const defaults = defaultCatalogFilters();
  const hasFilters =
    scope !== CURATED_SCOPE ||
    Boolean(value.line || value.character) ||
    value.owned !== undefined ||
    value.start !== defaults.start ||
    value.end !== defaults.end;

  return (
    <div className="catalog-filters">
      <div className="catalog-filters__head">
        <span className="catalog-filters__count">{resultLabel}</span>
        {hasFilters ? (
          <button type="button" className="text-button" onClick={() => onChange(defaults)}>
            Reset filters
          </button>
        ) : null}
      </div>

      {showLibraryFilter ? (
        <FilterGroup label="Library">
          <FilterChip
            label="Everything"
            isActive={value.owned === undefined}
            onClick={() => onChange({ ...value, owned: undefined })}
          />
          <FilterChip label="In library" isActive={value.owned === true} onClick={() => onChange({ ...value, owned: true })} />
          <FilterChip
            label="Not yet owned"
            isActive={value.owned === false}
            onClick={() => onChange({ ...value, owned: false })}
          />
        </FilterGroup>
      ) : null}

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
              label={character.label}
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
        <div className="year-range">
          <input
            type="text"
            inputMode="numeric"
            className="year-range__input"
            aria-label="Earliest year"
            placeholder={facets?.minYear ? `Earliest (${facets.minYear})` : 'Earliest'}
            defaultValue={yearOf(value.start)}
            key={`start-${value.start ?? ''}`}
            onBlur={(event) => onChange({ ...value, start: startOfYear(event.target.value.trim()) })}
            onKeyDown={(event) => event.key === 'Enter' && event.currentTarget.blur()}
          />
          <span className="year-range__dash">–</span>
          <input
            type="text"
            inputMode="numeric"
            className="year-range__input"
            aria-label="Latest year"
            placeholder={facets?.maxYear ? `Latest (${facets.maxYear})` : 'Latest'}
            defaultValue={yearOf(value.end)}
            key={`end-${value.end ?? ''}`}
            onBlur={(event) => onChange({ ...value, end: endOfYear(event.target.value.trim()) })}
            onKeyDown={(event) => event.key === 'Enter' && event.currentTarget.blur()}
          />
        </div>
      </FilterGroup>
    </div>
  );
}
