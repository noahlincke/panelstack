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
  readingPathEntryId?: string;
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
  coverUrl?: string;
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
    providerName?: string;
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

export type AppSettings = {
  downloadRoot: string;
  defaultDownloadRoot: string;
  hostedDeployment: boolean;
  opdsToken?: string;
};

export type OpenFolderResult = {
  path: string;
};

export type CatalogFacet = {
  value: string;
  label: string;
  count: number;
};

export type CatalogFacets = {
  publishers: CatalogFacet[];
  lines: CatalogFacet[];
  characters: CatalogFacet[];
  minYear?: number;
  maxYear?: number;
};

export type CatalogFilterState = {
  publisher?: string[];
  owned?: boolean;
  line?: string;
  character?: string;
  start?: string;
  end?: string;
  search?: string;
};

export type CatalogCollection = {
  id: string;
  slug: string;
  title: string;
  publisher?: string;
  line: string;
  collectionType: string;
  volumeNumber?: number;
  issueCount: number;
  ownedCount: number;
  tags: string[];
  firstPublishedOn?: string;
  latestPublishedOn?: string;
  /** The years the run covers. Manga has no chapter dates, so these come from
   *  the series' run years and are the only way to place it on the board. */
  startYear?: number;
  endYear?: number;
  readingPathId?: string;
  coverUrl?: string;
};

export type ChronologyEntry = {
  canonicalIssueId: string;
  title: string;
  issueNumber: string;
  publishedOn: string;
  publisher?: string;
  line: string;
  collectionId: string;
  collectionTitle: string;
  readingPathId?: string;
  coverUrl?: string;
};

export type DownloadTarget = {
  readingPathId: string;
  entryId: string;
  title: string;
};

export type DownloadEstimateItem = DownloadTarget & {
  sizeBytes?: number;
  status: string;
  detail?: string;
};

export type DestinationSpace = {
  path: string;
  totalBytes: number;
  freeBytes: number;
  exists: boolean;
};

export type DownloadEstimate = {
  targets: DownloadEstimateItem[];
  totalBytes: number;
  resolvedCount: number;
  unavailableCount: number;
  destination: DestinationSpace;
  fits: boolean;
};

export type DownloadQueueItem = DownloadTarget & {
  sizeBytes?: number;
  status: string;
  detail?: string;
};

export type DownloadQueue = {
  id: string;
  destination: string;
  status: string;
  items: DownloadQueueItem[];
  startedAt: string;
  finishedAt?: string;
  completedCount: number;
  totalCount: number;
};

export type ReadingListSummary = {
  id: string;
  name: string;
  description?: string;
  itemCount: number;
  firstYear?: number;
  lastYear?: number;
};

export type ReadingListItem = {
  id: string;
  readingPathId: string;
  entryId: string;
  title: string;
  sortOrder: number;
  owned: boolean;
  isRead: boolean;
  coverUrl?: string;
  canonicalIssueId?: string;
  publishedOn?: string;
};

export type ReadingList = {
  id: string;
  name: string;
  description?: string;
  items: ReadingListItem[];
};
