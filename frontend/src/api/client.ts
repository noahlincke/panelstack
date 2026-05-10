import { issues as mockIssues, readingPaths as mockReadingPaths, series as mockSeries } from '../data/mockLibrary';
import {
  getCollectionProgressKey,
  getIssueProgressKey,
  getLastReadAtForCollection,
  getUnreadCountForCollection,
  isCollectionIssueRead,
} from '../lib/readingProgress';
import type {
  AppSettings,
  AuthSession,
  EventDetail,
  EventSummary,
  ImportResult,
  Issue,
  LibrarySummary,
  OpenFolderResult,
  ReadingPathCover,
  ReadingPathDownloadResult,
  ReadingPath,
  ReadingPathDetail,
  ReadingPathEntry,
  Series,
  StoryArc,
} from './types';

const latency = 120;
const appBaseUrl = import.meta.env.BASE_URL.replace(/\/$/, '');
const defaultApiBaseUrl = import.meta.env.DEV ? '/api' : `${appBaseUrl}/api`;
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? defaultApiBaseUrl;
const USE_MOCK_DATA = import.meta.env.VITE_USE_MOCK_DATA === 'true';
export const AUTH_REQUIRED_EVENT = 'app-auth-required';

type BackendSeries = {
  id: number;
  slug: string;
  title: string;
  publisher: string | null;
  start_year: number | null;
  status: string;
  description?: string | null;
  issue_count?: number | null;
  reading_path_id?: number | null;
  cover_url?: string | null;
  latest_published_on?: string | null;
};

type BackendArchive = {
  id: number;
  page_count: number | null;
};

type BackendIssue = {
  id: number;
  series_id: number;
  reading_path_id?: number | null;
  primary_canonical_issue_id?: number | null;
  is_read?: boolean;
  issue_number: string;
  title: string | null;
  published_on: string | null;
  page_count: number | null;
  summary?: string | null;
  cover_url?: string | null;
  pages?: BackendArchivePage[];
  archives?: BackendArchive[];
};

type BackendEventSummary = {
  id: number;
  slug: string;
  title: string;
  publisher_id: number | null;
  description?: string | null;
  status: string;
  start_year: number | null;
  end_year: number | null;
  source_name?: string | null;
  source_url?: string | null;
};

type BackendStoryArcSummary = {
  id: number;
  slug: string;
  title: string;
  phase?: string | null;
  status: string;
};

type BackendCanonicalSeriesSummary = {
  id: number;
  slug: string;
  title: string;
};

type BackendCanonicalIssueSummary = {
  id: number;
  provider_name?: string | null;
  issue_number: string;
  title: string | null;
  published_on: string | null;
  cover_url?: string | null;
  page_count?: number | null;
};

type BackendCanonicalIssueRead = BackendCanonicalIssueSummary & {
  is_read?: boolean;
  summary?: string | null;
  pages?: BackendArchivePage[];
  series?: BackendCanonicalSeriesSummary | null;
};

type BackendReaderIssueRead = {
  id: string;
  issue_number: string;
  title: string;
  published_on?: string | null;
  summary?: string | null;
  page_count?: number | null;
  cover_url?: string | null;
  reading_path_id?: number | null;
  reading_path_entry_id?: number | null;
  canonical_issue_id?: number | null;
  is_read?: boolean;
  pages?: BackendArchivePage[];
};

type BackendReadingPathSummary = {
  id: number;
  slug: string;
  event_id: number | null;
  title: string;
  description?: string | null;
  status: string;
  publisher_name?: string | null;
  source_name?: string | null;
  source_url?: string | null;
  cover_url?: string | null;
  issue_count?: number;
  series_count?: number;
  latest_issue_label?: string | null;
  first_published_on?: string | null;
  latest_published_on?: string | null;
  is_downloaded?: boolean;
  access_mode?: string;
  unread_count?: number;
  is_complete?: boolean;
  last_read_at?: string | null;
  continuity_group_id?: number | null;
  previous_collection_id?: number | null;
  next_collection_id?: number | null;
  tags?: string[];
};

type BackendReadingPathCover = {
  reading_path_id: number;
  image_url?: string | null;
  post_url?: string | null;
  post_title?: string | null;
  query?: string | null;
};

type BackendReadingPathDownloadResponse = {
  reading_path_id: number;
  entry_id?: number | null;
  post_url?: string | null;
  imported_paths: string[];
  downloaded_issue_count?: number;
  skipped_issue_count?: number;
  series_created: number;
  series_updated: number;
  issues_created: number;
  issues_updated: number;
  archives_created: number;
  archives_updated: number;
};

type BackendReadingPathEntry = {
  id: number;
  sort_order: number;
  entry_type: string;
  importance: string;
  label?: string | null;
  note?: string | null;
  is_optional: boolean;
  issue_key?: string | null;
  is_read?: boolean;
  cover_url?: string | null;
  story_arc?: BackendStoryArcSummary | null;
  canonical_series?: BackendCanonicalSeriesSummary | null;
  canonical_issue?: BackendCanonicalIssueSummary | null;
  matched_issue?: {
    id: number;
    series_id: number;
    issue_number: string;
    title: string | null;
    cover_url?: string | null;
    published_on?: string | null;
  } | null;
};

type BackendReadingPathDetail = BackendReadingPathSummary & {
  event?: BackendEventSummary | null;
  entries: BackendReadingPathEntry[];
};

type BackendIssueState = {
  issue_key: string;
  issue_id?: number | null;
  canonical_issue_id?: number | null;
  is_read: boolean;
  read_at?: string | null;
  last_opened_at?: string | null;
};

type BackendEventDetail = BackendEventSummary & {
  description?: string | null;
  publisher?: { name: string } | null;
  story_arcs?: BackendStoryArcSummary[];
  reading_paths?: BackendReadingPathSummary[];
};

type BackendArchivePage = {
  index: number;
  relative_path: string;
  media_type: string;
  image_url: string;
};

function respond<T>(value: T): Promise<T> {
  return new Promise((resolve) => {
    window.setTimeout(() => resolve(value), latency);
  });
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { credentials: 'include', ...init });
  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const payload = await response.json() as { detail?: string };
      if (payload.detail) {
        detail = payload.detail;
      }
    } catch {
      // Ignore JSON parse errors here and keep the status-based message.
    }
    if (response.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT));
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

function resolveApiUrl(path: string): string {
  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path;
  }
  return `${API_BASE_URL}${path}`;
}

function formatYearRange(startYear: number | null, endYear: number | null): string {
  if (startYear && endYear && startYear !== endYear) {
    return `${startYear}–${endYear}`;
  }
  if (startYear) {
    return String(startYear);
  }
  if (endYear) {
    return String(endYear);
  }
  return 'Unknown years';
}

function estimateReadingTime(totalIssues: number): string {
  if (totalIssues <= 0) {
    return 'TBD';
  }
  return `${Math.max(1, Math.ceil(totalIssues / 4))} to ${Math.max(2, Math.ceil(totalIssues / 2))} hours`;
}

function mapSeries(item: BackendSeries): Series {
  const mock = mockSeries.find((entry) => entry.title === item.title);
  const description =
    item.description && !item.description.startsWith('Imported from local library:')
      ? item.description
      : mock?.synopsis ??
        `Locally indexed series record for ${item.title}. Editorial summaries and reading guidance can be layered on top.`;
  return {
    id: String(item.id),
    title: item.title,
    publisher: item.publisher ?? 'Unknown',
    yearStarted: item.start_year ?? new Date().getFullYear(),
    status: item.status === 'completed' || item.status === 'hiatus' ? item.status : 'ongoing',
    synopsis: description,
    accentClass: mock?.accentClass ?? 'accent-indexed',
    tags: mock?.tags ?? ['indexed', 'api-backed'],
    issueCount: item.issue_count ?? 0,
    readingPathId: item.reading_path_id ? String(item.reading_path_id) : undefined,
    coverUrl: item.cover_url ? resolveApiUrl(item.cover_url) : undefined,
    latestPublishedOn: item.latest_published_on ?? undefined,
  };
}

function baseIssue(item: BackendIssue): Issue {
  const mock = mockIssues.find((issue) => issue.id === String(item.id));
  return {
    id: String(item.id),
    seriesId: String(item.series_id),
    readingPathId: item.reading_path_id ? String(item.reading_path_id) : undefined,
    readingPathEntryId: undefined,
    canonicalIssueId: item.primary_canonical_issue_id ? String(item.primary_canonical_issue_id) : undefined,
    isRead: item.is_read ?? false,
    number: item.issue_number,
    title: item.title ?? `Issue ${item.issue_number}`,
    releaseDate: item.published_on ?? 'Unknown date',
    pageCount: item.page_count ?? mock?.pageCount ?? 0,
    summary: item.summary ?? mock?.summary ?? 'No issue summary has been indexed yet.',
    cover: mock?.cover ?? 'Cover art will come from archive metadata or an external source later.',
    coverUrl: item.cover_url ? resolveApiUrl(item.cover_url) : undefined,
    pages: (item.pages ?? []).map((page) => ({
      index: page.index,
      title: `Page ${page.index}`,
      caption: page.relative_path,
      tone: 'bone',
      imageUrl: resolveApiUrl(page.image_url),
    })),
  };
}

function readerIssue(payload: BackendReaderIssueRead): Issue {
  const pages = (payload.pages ?? []).map((page) => ({
    index: page.index,
    title: `Page ${page.index}`,
    caption: page.relative_path,
    tone: 'bone',
    imageUrl: resolveApiUrl(page.image_url),
  }));
  return {
    id: payload.id,
    seriesId: payload.reading_path_id ? `reading-path:${payload.reading_path_id}` : `reading-path-entry:${payload.id}`,
    readingPathId: payload.reading_path_id ? String(payload.reading_path_id) : undefined,
    readingPathEntryId: payload.reading_path_entry_id ? String(payload.reading_path_entry_id) : undefined,
    canonicalIssueId: payload.canonical_issue_id ? String(payload.canonical_issue_id) : undefined,
    isRead: payload.is_read ?? false,
    number: payload.issue_number,
    title: payload.title,
    releaseDate: payload.published_on ?? 'Unknown date',
    pageCount: payload.page_count ?? pages.length,
    summary: payload.summary ?? 'No issue summary has been indexed yet.',
    cover: payload.title,
    coverUrl: payload.cover_url ? resolveApiUrl(payload.cover_url) : undefined,
    pages,
  };
}

async function hydrateIssuePages(issue: Issue, archives: BackendArchive[] | undefined): Promise<Issue> {
  const primaryArchive = archives?.find((archive) => (archive.page_count ?? 0) > 0) ?? archives?.[0];
  if (!primaryArchive) {
    return issue;
  }

  const payload = await fetchJson<{ archive_id: number; pages: BackendArchivePage[] }>(
    `/archives/${primaryArchive.id}/pages`,
  );

  return {
    ...issue,
    pageCount: payload.pages.length || issue.pageCount,
    pages:
      payload.pages.length > 0
        ? payload.pages.map((page) => ({
            index: page.index,
            title: `Page ${page.index}`,
            caption: page.relative_path,
            tone: 'bone',
            imageUrl: resolveApiUrl(page.image_url),
          }))
        : issue.pages,
  };
}

function mapStoryArc(item: BackendStoryArcSummary): StoryArc {
  return {
    id: String(item.id),
    slug: item.slug,
    title: item.title,
    phase: item.phase ?? undefined,
    status: item.status,
  };
}

function mapEventSummary(item: BackendEventSummary, publisherName?: string, pathCount?: number, arcCount?: number): EventSummary {
  return {
    id: String(item.id),
    slug: item.slug,
    title: item.title,
    publisher: publisherName ?? 'Unknown publisher',
    years: formatYearRange(item.start_year, item.end_year),
    description: item.description ?? 'No editorial summary has been attached to this event yet.',
    sourceName: item.source_name ?? undefined,
    sourceUrl: item.source_url ?? undefined,
    pathCount,
    arcCount,
  };
}

function derivePathLine(item: BackendReadingPathSummary): ReadingPath['line'] {
  const signature = `${item.slug} ${item.title}`.toLowerCase();
  if (signature.includes('ultimate')) {
    return 'ultimate';
  }
  if (signature.includes('absolute')) {
    return 'absolute';
  }
  if (item.event_id) {
    return 'event';
  }
  return 'series';
}

function mapReadingPathSummary(item: BackendReadingPathSummary, eventTitle?: string): ReadingPath {
  const totalIssues = item.issue_count ?? 0;
  const seriesCount = item.series_count ?? 0;
  const base: ReadingPath = {
    id: String(item.id),
    slug: item.slug,
    title: item.title,
    description: item.description ?? 'Curated path record.',
    totalIssues,
    estimate: estimateReadingTime(totalIssues),
    seriesIds: [],
    seriesCount,
    publisher: item.publisher_name ?? undefined,
    line: derivePathLine(item),
    eventId: item.event_id ? String(item.event_id) : undefined,
    eventTitle,
    sourceName: item.source_name ?? undefined,
    sourceUrl: item.source_url ?? undefined,
    coverUrl: item.cover_url ? resolveApiUrl(item.cover_url) : undefined,
    latestIssueLabel: item.latest_issue_label ?? undefined,
    firstPublishedOn: item.first_published_on ?? undefined,
    latestPublishedOn: item.latest_published_on ?? undefined,
    isDownloaded: item.is_downloaded ?? false,
    accessMode: item.access_mode === 'stream' ? 'stream' : 'download',
    unreadCount: item.unread_count ?? 0,
    isComplete: item.is_complete ?? false,
    lastReadAt: item.last_read_at ?? undefined,
    continuityGroupId: item.continuity_group_id ? String(item.continuity_group_id) : undefined,
    previousCollectionId: item.previous_collection_id ? String(item.previous_collection_id) : undefined,
    nextCollectionId: item.next_collection_id ? String(item.next_collection_id) : undefined,
    tags: item.tags ?? [],
  };
  return applyClientCollectionState(base);
}

function mapReadingPathEntry(item: BackendReadingPathEntry): ReadingPathEntry {
  const entry = {
    id: String(item.id),
    sortOrder: item.sort_order,
    entryType: item.entry_type,
    importance: item.importance,
    label: item.label ?? undefined,
    note: item.note ?? undefined,
    isOptional: item.is_optional,
    issueKey: item.issue_key ?? undefined,
    isRead: item.is_read ?? false,
    coverUrl: item.cover_url ? resolveApiUrl(item.cover_url) : undefined,
    storyArc: item.story_arc ? mapStoryArc(item.story_arc) : undefined,
    canonicalSeries: item.canonical_series
      ? { id: String(item.canonical_series.id), slug: item.canonical_series.slug, title: item.canonical_series.title }
      : undefined,
        canonicalIssue: item.canonical_issue
          ? {
              id: String(item.canonical_issue.id),
              providerName: item.canonical_issue.provider_name ?? undefined,
              issueNumber: item.canonical_issue.issue_number,
              title: item.canonical_issue.title ?? `Issue ${item.canonical_issue.issue_number}`,
              publishedOn: item.canonical_issue.published_on ?? undefined,
          coverUrl: item.canonical_issue.cover_url ? resolveApiUrl(item.canonical_issue.cover_url) : undefined,
        }
      : undefined,
    matchedIssue: item.matched_issue
      ? {
          id: String(item.matched_issue.id),
          seriesId: String(item.matched_issue.series_id),
          readingPathId: undefined,
          issueNumber: item.matched_issue.issue_number,
          title: item.matched_issue.title ?? `Issue ${item.matched_issue.issue_number}`,
          coverUrl: item.matched_issue.cover_url ? resolveApiUrl(item.matched_issue.cover_url) : undefined,
          publishedOn: item.matched_issue.published_on ?? undefined,
        }
      : undefined,
  };
  return entry;
}

function applyClientCollectionState(path: ReadingPath): ReadingPath {
  const collectionKey = getCollectionProgressKey({ readingPathId: path.id });
  const unreadCount = getUnreadCountForCollection(collectionKey, path.totalIssues);
  const lastReadAt = getLastReadAtForCollection(collectionKey);
  return {
    ...path,
    isDownloaded: path.isDownloaded,
    unreadCount,
    isComplete: path.totalIssues > 0 && unreadCount === 0,
    lastReadAt: lastReadAt ? new Date(lastReadAt).toISOString() : path.lastReadAt,
  };
}

function applyClientEntryState(readingPathId: string, entry: ReadingPathEntry): ReadingPathEntry {
  const collectionKey = getCollectionProgressKey({ readingPathId });
  const issueKey = getIssueProgressKey({
    canonicalIssueId: entry.canonicalIssue?.id,
    issueId: entry.matchedIssue?.id,
    entryId: entry.id,
  });
  return {
    ...entry,
    isRead: entry.isRead || isCollectionIssueRead(collectionKey, issueKey),
  };
}

function mapReadingPathDetail(payload: BackendReadingPathDetail): ReadingPathDetail {
  const entries = payload.entries.map(mapReadingPathEntry).map((entry) => applyClientEntryState(String(payload.id), entry));
  const seriesIds = Array.from(
    new Set(entries.flatMap((entry) => (entry.canonicalSeries ? [entry.canonicalSeries.id] : []))),
  );
  return {
    ...mapReadingPathSummary(payload, payload.event?.title),
    description: payload.description ?? 'Curated path record.',
    totalIssues: entries.filter((entry) => entry.entryType === 'issue').length,
    estimate: estimateReadingTime(entries.filter((entry) => entry.entryType === 'issue').length),
    seriesIds,
    seriesCount: seriesIds.length,
    eventId: payload.event_id ? String(payload.event_id) : undefined,
    eventTitle: payload.event?.title ?? undefined,
    entries,
  };
}

function mockEvents(): EventSummary[] {
  const uniquePublishers = Array.from(new Set(mockSeries.map((series) => series.publisher)));
  return uniquePublishers.map((publisher, index) => ({
    id: `mock-event-${index + 1}`,
    slug: `mock-event-${index + 1}`,
    title: `${publisher} Primer`,
    publisher,
    years: 'Curated',
    description: `A placeholder event surface for ${publisher} while mock mode is enabled.`,
    pathCount: mockReadingPaths.length,
    arcCount: 1,
  }));
}

export const apiClient = {
  async getSession(): Promise<AuthSession> {
    return fetchJson<AuthSession>('/auth/session');
  },

  async login(password: string): Promise<AuthSession> {
    return fetchJson<AuthSession>('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
  },

  async getLibrarySummary(): Promise<LibrarySummary> {
    try {
      const payload = await fetchJson<{
        series_count: number;
        issue_count: number;
        archive_count: number;
        reading_path_count: number;
      }>('/library/summary');
      return {
        seriesCount: payload.series_count,
        issueCount: payload.issue_count,
        archiveCount: payload.archive_count,
        readingPathCount: payload.reading_path_count,
      };
    } catch (error) {
      if (!USE_MOCK_DATA) {
        throw error;
      }
      return respond({
        seriesCount: mockSeries.length,
        issueCount: mockIssues.length,
        archiveCount: mockIssues.length,
        readingPathCount: mockReadingPaths.length,
      });
    }
  },

  async importLibrary(paths: string[], recursive = true): Promise<ImportResult> {
    const payload = await fetchJson<{
      imported_paths: string[];
      series_created: number;
      series_updated: number;
      issues_created: number;
      issues_updated: number;
      archives_created: number;
      archives_updated: number;
    }>('/ingest/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, recursive }),
    });

    return {
      importedPaths: payload.imported_paths,
      seriesCreated: payload.series_created,
      seriesUpdated: payload.series_updated,
      issuesCreated: payload.issues_created,
      issuesUpdated: payload.issues_updated,
      archivesCreated: payload.archives_created,
      archivesUpdated: payload.archives_updated,
    };
  },

  async openDownloadsFolder(): Promise<OpenFolderResult> {
    const payload = await fetchJson<{ path: string }>('/library/open-downloads', { method: 'POST' });
    return { path: payload.path };
  },

  async getSettings(): Promise<AppSettings> {
    const payload = await fetchJson<{
      download_root: string;
      default_download_root: string;
      hosted_deployment?: boolean;
    }>('/settings');
    return {
      downloadRoot: payload.download_root,
      defaultDownloadRoot: payload.default_download_root,
      hostedDeployment: Boolean(payload.hosted_deployment),
    };
  },

  async updateSettings(settings: Pick<AppSettings, 'downloadRoot'>): Promise<AppSettings> {
    const payload = await fetchJson<{
      download_root: string;
      default_download_root: string;
      hosted_deployment?: boolean;
    }>('/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ download_root: settings.downloadRoot }),
    });
    return {
      downloadRoot: payload.download_root,
      defaultDownloadRoot: payload.default_download_root,
      hostedDeployment: Boolean(payload.hosted_deployment),
    };
  },

  async listSeries(sort: 'title' | 'latest_published_desc' | 'latest_published_asc' = 'title'): Promise<Series[]> {
    try {
      const payload = await fetchJson<{ items: BackendSeries[] }>(`/series?sort=${sort}`);
      return payload.items.map(mapSeries);
    } catch (error) {
      if (!USE_MOCK_DATA) {
        throw error;
      }
    }
    return respond(mockSeries);
  },

  async getSeries(seriesId: string): Promise<Series | undefined> {
    try {
      const payload = await fetchJson<BackendSeries>(`/series/${seriesId}`);
      return mapSeries(payload);
    } catch (error) {
      if (!USE_MOCK_DATA) {
        throw error;
      }
    }
    return respond(mockSeries.find((item) => item.id === seriesId));
  },

  async getSeriesIssues(seriesId: string): Promise<Issue[]> {
    try {
      const payload = await fetchJson<{ items: BackendIssue[] }>(`/issues?series_id=${seriesId}`);
      return payload.items.map(baseIssue);
    } catch (error) {
      if (!USE_MOCK_DATA) {
        throw error;
      }
    }
    return respond(mockIssues.filter((issue) => issue.seriesId === seriesId));
  },

  async getIssue(issueId: string): Promise<Issue | undefined> {
    try {
      const payload = await fetchJson<BackendIssue>(`/issues/${issueId}`);
      try {
        return await hydrateIssuePages(baseIssue(payload), payload.archives);
      } catch {
        return baseIssue(payload);
      }
    } catch (error) {
      if (!USE_MOCK_DATA) {
        throw error;
      }
    }
    return respond(mockIssues.find((issue) => issue.id === issueId));
  },

  async getCanonicalIssue(issueId: string): Promise<Issue | undefined> {
    const payload = await fetchJson<BackendCanonicalIssueRead>(`/canonical-issues/${issueId}`);
    const pages = (payload.pages ?? []).map((page) => ({
      index: page.index,
      title: `Page ${page.index}`,
      caption: page.relative_path,
      tone: 'bone',
      imageUrl: resolveApiUrl(page.image_url),
    }));
    return {
      id: `canonical:${payload.id}`,
      canonicalIssueId: String(payload.id),
      seriesId: payload.series ? String(payload.series.id) : `canonical-series:${payload.id}`,
      isRead: payload.is_read ?? false,
      number: payload.issue_number,
      title: payload.title ?? `Issue ${payload.issue_number}`,
      releaseDate: payload.published_on ?? 'Unknown date',
      pageCount: payload.page_count ?? pages.length,
      summary: payload.summary ?? 'No issue summary has been indexed yet.',
      cover: payload.title ?? `Issue ${payload.issue_number}`,
      coverUrl: payload.cover_url ? resolveApiUrl(payload.cover_url) : undefined,
      pages,
    };
  },

  async getReadingPathEntryViewerIssue(readingPathId: string, entryId: string): Promise<Issue | undefined> {
    const payload = await fetchJson<BackendReaderIssueRead>(`/reading-paths/${readingPathId}/entries/${entryId}/viewer`);
    return readerIssue(payload);
  },

  async listEvents(): Promise<EventSummary[]> {
    try {
      const [eventsPayload, publishersPayload] = await Promise.all([
        fetchJson<{ items: BackendEventSummary[] }>('/events'),
        fetchJson<{ items: Array<{ id: number; name: string }> }>('/publishers'),
      ]);
      const publisherMap = new Map(publishersPayload.items.map((item) => [item.id, item.name]));
      return eventsPayload.items.map((item) =>
        mapEventSummary(item, item.publisher_id ? publisherMap.get(item.publisher_id) : undefined),
      );
    } catch (error) {
      if (!USE_MOCK_DATA) {
        throw error;
      }
    }
    return respond(mockEvents());
  },

  async getEvent(eventId: string): Promise<EventDetail | undefined> {
    try {
      const payload = await fetchJson<BackendEventDetail>(`/events/${eventId}`);
      const paths = payload.reading_paths?.map((item) => mapReadingPathSummary(item, payload.title)) ?? [];
      const arcs = payload.story_arcs?.map(mapStoryArc) ?? [];
      return {
        ...mapEventSummary(payload, payload.publisher?.name, paths.length, arcs.length),
        description: payload.description ?? 'No editorial description yet.',
        arcs,
        readingPaths: paths.map((path) => ({
          ...path,
          sourceName: path.sourceName ?? payload.source_name ?? undefined,
          sourceUrl: path.sourceUrl ?? payload.source_url ?? undefined,
        })),
      };
    } catch (error) {
      if (!USE_MOCK_DATA) {
        throw error;
      }
    }
    const event = mockEvents().find((item) => item.id === eventId);
    if (!event) {
      return undefined;
    }
    return respond({ ...event, arcs: [], readingPaths: mockReadingPaths });
  },

  async listReadingPaths(
    sort: 'title' | 'latest_published_desc' | 'latest_published_asc' = 'latest_published_desc',
  ): Promise<ReadingPath[]> {
    try {
      const pageSize = 1000;
      let offset = 0;
      const items: BackendReadingPathSummary[] = [];
      while (true) {
        const payload = await fetchJson<{ items: BackendReadingPathSummary[]; total: number }>(
          `/reading-paths?sort=${sort}&limit=${pageSize}&offset=${offset}`,
        );
        items.push(...payload.items);
        offset += payload.items.length;
        if (items.length >= payload.total || payload.items.length < pageSize) {
          break;
        }
      }
      return items.map((item) => mapReadingPathSummary(item));
    } catch (error) {
      if (!USE_MOCK_DATA) {
        throw error;
      }
    }
    return respond(mockReadingPaths);
  },

  async getReadingPathCovers(ids: string[]): Promise<Record<string, ReadingPathCover>> {
    const uniqueIds = Array.from(new Set(ids.filter(Boolean)));
    if (uniqueIds.length === 0) {
      return {};
    }

    try {
      const payload = await fetchJson<{ items: BackendReadingPathCover[] }>(
        `/reading-paths/covers?ids=${uniqueIds.join(',')}`,
      );
      return Object.fromEntries(
        payload.items.map((item) => [
          String(item.reading_path_id),
          {
            readingPathId: String(item.reading_path_id),
            imageUrl: item.image_url ? resolveApiUrl(item.image_url) : undefined,
            postUrl: item.post_url ?? undefined,
            postTitle: item.post_title ?? undefined,
            query: item.query ?? undefined,
          } satisfies ReadingPathCover,
        ]),
      );
    } catch (error) {
      if (!USE_MOCK_DATA) {
        throw error;
      }
    }

    return Object.fromEntries(uniqueIds.map((id) => [id, { readingPathId: id } satisfies ReadingPathCover]));
  },

  async getReadingPath(readingPathId: string): Promise<ReadingPathDetail | undefined> {
    try {
      const payload = await fetchJson<BackendReadingPathDetail>(`/reading-paths/${readingPathId}`);
      return mapReadingPathDetail(payload);
    } catch (error) {
      if (!USE_MOCK_DATA) {
        throw error;
      }
    }
    const path = mockReadingPaths.find((item) => item.id === readingPathId);
    if (!path) {
      return undefined;
    }
    return respond({ ...path, entries: [] });
  },

  async downloadReadingPath(readingPathId: string): Promise<ReadingPathDownloadResult> {
    const payload = await fetchJson<BackendReadingPathDownloadResponse>(`/reading-paths/${readingPathId}/download`, {
      method: 'POST',
    });

    return {
      readingPathId: String(payload.reading_path_id),
      entryId: payload.entry_id ? String(payload.entry_id) : undefined,
      postUrl: payload.post_url ?? undefined,
      importedPaths: payload.imported_paths,
      downloadedIssueCount: payload.downloaded_issue_count ?? 0,
      skippedIssueCount: payload.skipped_issue_count ?? 0,
      seriesCreated: payload.series_created,
      seriesUpdated: payload.series_updated,
      issuesCreated: payload.issues_created,
      issuesUpdated: payload.issues_updated,
      archivesCreated: payload.archives_created,
      archivesUpdated: payload.archives_updated,
    };
  },

  async downloadReadingPathEntry(readingPathId: string, entryId: string): Promise<ReadingPathDownloadResult> {
    const payload = await fetchJson<BackendReadingPathDownloadResponse>(
      `/reading-paths/${readingPathId}/entries/${entryId}/download`,
      { method: 'POST' },
    );

    return {
      readingPathId: String(payload.reading_path_id),
      entryId: payload.entry_id ? String(payload.entry_id) : undefined,
      postUrl: payload.post_url ?? undefined,
      importedPaths: payload.imported_paths,
      downloadedIssueCount: payload.downloaded_issue_count ?? 0,
      skippedIssueCount: payload.skipped_issue_count ?? 0,
      seriesCreated: payload.series_created,
      seriesUpdated: payload.series_updated,
      issuesCreated: payload.issues_created,
      issuesUpdated: payload.issues_updated,
      archivesCreated: payload.archives_created,
      archivesUpdated: payload.archives_updated,
    };
  },

  getReadingPathEntryDownloadUrl(readingPathId: string, entryId: string): string {
    return resolveApiUrl(`/reading-paths/${readingPathId}/entries/${entryId}/download`);
  },

  async deleteSeries(seriesId: string): Promise<void> {
    await fetchJson(`/series/${seriesId}`, { method: 'DELETE' });
  },

  async deleteIssue(issueId: string): Promise<{ seriesDeleted: boolean }> {
    const payload = await fetchJson<{ series_deleted?: boolean }>(`/issues/${issueId}`, { method: 'DELETE' });
    return { seriesDeleted: payload.series_deleted ?? false };
  },

  async setReadingPathEntryReadState(readingPathId: string, entryId: string, read: boolean): Promise<BackendIssueState> {
    return fetchJson<BackendIssueState>(`/reading-paths/${readingPathId}/entries/${entryId}/read-state`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ read }),
    });
  },

  async setIssueReadState(issueId: string, read: boolean, markOpened = false): Promise<BackendIssueState> {
    return fetchJson<BackendIssueState>(`/issues/${issueId}/read-state`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ read, mark_opened: markOpened }),
    });
  },

  async setCanonicalIssueReadState(issueId: string, read: boolean, markOpened = false): Promise<BackendIssueState> {
    return fetchJson<BackendIssueState>(`/canonical-issues/${issueId}/read-state`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ read, mark_opened: markOpened }),
    });
  },
};
