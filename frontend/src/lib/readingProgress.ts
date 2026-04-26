const STORAGE_KEY = 'comic-library-read-issues-v1';
const TIMESTAMPS_STORAGE_KEY = 'comic-library-read-issue-timestamps-v1';
export const READING_PROGRESS_EVENT = 'reading-progress-changed';

type ReadIssueMap = Record<string, string[]>;
type ReadTimestampMap = Record<string, Record<string, string>>;
type ProgressCollectionRef = {
  readingPathId?: string | null;
  seriesId?: string | null;
};
type ProgressIssueRef = {
  canonicalIssueId?: string | null;
  issueId?: string | null;
  entryId?: string | null;
};

function hasWindow(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function readMap(): ReadIssueMap {
  if (!hasWindow()) {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as ReadIssueMap;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeMap(value: ReadIssueMap): void {
  writeProgress(value, readTimestampMap());
}

function readTimestampMap(): ReadTimestampMap {
  if (!hasWindow()) {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(TIMESTAMPS_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as ReadTimestampMap;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeProgress(reads: ReadIssueMap, timestamps: ReadTimestampMap): void {
  if (!hasWindow()) {
    return;
  }
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(reads));
  window.localStorage.setItem(TIMESTAMPS_STORAGE_KEY, JSON.stringify(timestamps));
  window.dispatchEvent(new CustomEvent(READING_PROGRESS_EVENT));
}

export function getCollectionProgressKey({ readingPathId, seriesId }: ProgressCollectionRef): string {
  if (readingPathId) {
    return `path:${readingPathId}`;
  }
  return `series:${seriesId ?? 'unknown'}`;
}

export function getIssueProgressKey({ canonicalIssueId, issueId, entryId }: ProgressIssueRef): string {
  if (canonicalIssueId) {
    return `canonical:${canonicalIssueId}`;
  }
  if (issueId) {
    return `issue:${issueId}`;
  }
  return `entry:${entryId ?? 'unknown'}`;
}

function getReadCountForCollectionKey(collectionKey: string): number {
  return (readMap()[collectionKey] ?? []).length;
}

export function getReadCountForCollection(collectionKey: string): number {
  return getReadCountForCollectionKey(collectionKey);
}

export function getUnreadCountForCollection(collectionKey: string, issueCount: number): number {
  return Math.max(0, issueCount - getReadCountForCollectionKey(collectionKey));
}

export function getLastReadAtForCollection(collectionKey: string): number | null {
  const entries = Object.values(readTimestampMap()[collectionKey] ?? {});
  if (entries.length === 0) {
    return null;
  }
  const timestamps = entries
    .map((value) => Date.parse(value))
    .filter((value) => Number.isFinite(value));
  if (timestamps.length === 0) {
    return null;
  }
  return Math.max(...timestamps);
}

export function isCollectionIssueRead(collectionKey: string, issueKey: string): boolean {
  return (readMap()[collectionKey] ?? []).includes(issueKey);
}

export function setCollectionIssueReadState(collectionKey: string, issueKey: string, read: boolean): void {
  const reads = readMap();
  const timestamps = readTimestampMap();
  const seen = new Set(reads[collectionKey] ?? []);
  const collectionTimestamps = { ...(timestamps[collectionKey] ?? {}) };
  if (read) {
    seen.add(issueKey);
    collectionTimestamps[issueKey] = new Date().toISOString();
  } else {
    seen.delete(issueKey);
    delete collectionTimestamps[issueKey];
  }
  reads[collectionKey] = Array.from(seen);
  timestamps[collectionKey] = collectionTimestamps;
  writeProgress(reads, timestamps);
}

export function markIssueAsRead(seriesId: string, issueId: string): void {
  const collectionKey = getCollectionProgressKey({ seriesId });
  const issueKey = getIssueProgressKey({ issueId });
  setCollectionIssueReadState(collectionKey, issueKey, true);
}

export function getReadCountForSeries(seriesId: string): number {
  return getReadCountForCollectionKey(getCollectionProgressKey({ seriesId }));
}

export function getUnreadCountForSeries(seriesId: string, issueCount: number): number {
  return getUnreadCountForCollection(getCollectionProgressKey({ seriesId }), issueCount);
}

export function isIssueRead(seriesId: string, issueId: string): boolean {
  return isCollectionIssueRead(getCollectionProgressKey({ seriesId }), getIssueProgressKey({ issueId }));
}

export function setIssueReadState(seriesId: string, issueId: string, read: boolean): void {
  setCollectionIssueReadState(getCollectionProgressKey({ seriesId }), getIssueProgressKey({ issueId }), read);
}
