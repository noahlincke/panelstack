/** The curated catalog window: modern DC/Marvel continuity through the present. */
export const CATALOG_WINDOW = {
  start: '2019-01-01',
  end: '2026-08-31',
} as const;

/** The catalog is curated around modern DC and Marvel continuity. */
export const DEFAULT_PUBLISHERS = ['dc', 'marvel'];

const MONTH_FORMATTER = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });

export function monthKey(isoDate: string): string {
  return isoDate.slice(0, 7);
}

export function monthLabel(isoDate: string): string {
  return MONTH_FORMATTER.format(new Date(`${monthKey(isoDate)}-01T00:00:00Z`));
}
