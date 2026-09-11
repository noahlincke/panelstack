
/** The pair behind the "DC + Marvel" shortcut. Not the default — that is everything. */
export const DEFAULT_PUBLISHERS = ['dc', 'marvel'];

const MONTH_FORMATTER = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });

export function monthKey(isoDate: string): string {
  return isoDate.slice(0, 7);
}

export function monthLabel(isoDate: string): string {
  return MONTH_FORMATTER.format(new Date(`${monthKey(isoDate)}-01T00:00:00Z`));
}
