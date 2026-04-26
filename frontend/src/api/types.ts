export type Series = {
  id: string;
  title: string;
  publisher: string;
  yearStarted: number;
  status: 'ongoing' | 'completed' | 'hiatus';
  synopsis: string;
  accentClass: string;
  tags: string[];
  issueCount: number;
  readingPathId?: string;
  coverUrl?: string;
  latestPublishedOn?: string;
};

export type Issue = {
  id: string;
  seriesId: string;
  readingPathId?: string;
  canonicalIssueId?: string;
  isRead?: boolean;
  number: string;
  title: string;
  releaseDate: string;
  pageCount: number;
  summary: string;
  cover: string;
  coverUrl?: string;
  pages: ComicPage[];
};

export type ComicPage = {
  index: number;
  title: string;
  caption: string;
  tone: string;
  imageUrl?: string;
};

export type CanonicalReference = {
  id: string;
  slug?: string;
  title: string;
};

export type EventSummary = {
  id: string;
  slug: string;
  title: string;
  publisher: string;
  years: string;
  description: string;
  sourceName?: string;
  sourceUrl?: string;
  pathCount?: number;
  arcCount?: number;
};

export type EventDetail = EventSummary & {
  arcs: StoryArc[];
  readingPaths: ReadingPath[];
};

export type StoryArc = {
  id: string;
  slug: string;
  title: string;
  phase?: string;
  status: string;
};

export type ReadingPath = {
  id: string;
  slug?: string;
  title: string;
  description: string;
  totalIssues: number;
  estimate: string;
  seriesIds: string[];
  seriesCount?: number;
  publisher?: string;
  line?: 'all' | 'event' | 'series' | 'ultimate' | 'absolute';
  eventId?: string;
  eventTitle?: string;
  sourceName?: string;
  sourceUrl?: string;
  latestIssueLabel?: string;
  firstPublishedOn?: string;
  latestPublishedOn?: string;
  isDownloaded?: boolean;
  accessMode?: 'download' | 'stream';
  unreadCount?: number;
  isComplete?: boolean;
  lastReadAt?: string;
  continuityGroupId?: string;
  previousCollectionId?: string;
  nextCollectionId?: string;
  tags?: string[];
};

export type ReadingPathCover = {
  readingPathId: string;
  imageUrl?: string;
  postUrl?: string;
  postTitle?: string;
  query?: string;
};

export type ReadingPathDownloadResult = {
  readingPathId: string;
  entryId?: string;
  postUrl?: string;
  importedPaths: string[];
  downloadedIssueCount: number;
  skippedIssueCount: number;
  seriesCreated: number;
  seriesUpdated: number;
  issuesCreated: number;
  issuesUpdated: number;
  archivesCreated: number;
  archivesUpdated: number;
};

export type ReadingPathEntry = {
  id: string;
  sortOrder: number;
  entryType: string;
  importance: string;
  label?: string;
  note?: string;
  isOptional: boolean;
  issueKey?: string;
  isRead?: boolean;
  coverUrl?: string;
  storyArc?: StoryArc;
  canonicalSeries?: CanonicalReference;
  canonicalIssue?: {
    id: string;
    issueNumber: string;
    title: string;
    publishedOn?: string;
    coverUrl?: string;
  };
  matchedIssue?: {
    id: string;
    seriesId: string;
    readingPathId?: string;
    title: string;
    issueNumber: string;
    coverUrl?: string;
    publishedOn?: string;
  };
};

export type ReadingPathDetail = ReadingPath & {
  entries: ReadingPathEntry[];
};

export type LibrarySummary = {
  seriesCount: number;
  issueCount: number;
  archiveCount: number;
  readingPathCount: number;
};

export type ImportResult = {
  importedPaths: string[];
  seriesCreated: number;
  seriesUpdated: number;
  issuesCreated: number;
  issuesUpdated: number;
  archivesCreated: number;
  archivesUpdated: number;
};

export type AuthSession = {
  enabled: boolean;
  authenticated: boolean;
};

export type OpenFolderResult = {
  path: string;
};
