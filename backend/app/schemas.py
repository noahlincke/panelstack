from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class APIBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HealthResponse(APIBaseModel):
    status: str = "ok"
    database: str = "ok"


class ArchiveRead(APIBaseModel):
    id: int
    series_id: int | None
    issue_id: int | None
    storage_path: str
    original_filename: str | None
    source_url: str | None
    archive_format: str
    page_count: int | None
    size_bytes: int | None
    checksum_sha256: str | None
    extracted_path: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class ArchivePageRead(APIBaseModel):
    index: int
    relative_path: str
    media_type: str
    image_url: str


class ArchivePageListResponse(APIBaseModel):
    archive_id: int
    pages: list[ArchivePageRead] = Field(default_factory=list)


class PublisherSummary(APIBaseModel):
    id: int
    slug: str
    name: str


class PublisherRead(PublisherSummary):
    description: str | None
    created_at: datetime
    updated_at: datetime


class EventSummary(APIBaseModel):
    id: int
    publisher_id: int | None
    slug: str
    title: str
    description: str | None
    status: str
    start_year: int | None
    end_year: int | None
    source_name: str | None
    source_url: str | None


class StoryArcSummary(APIBaseModel):
    id: int
    event_id: int | None
    slug: str
    title: str
    phase: str | None
    status: str


class CanonicalSeriesSummary(APIBaseModel):
    id: int
    publisher_id: int | None
    slug: str
    title: str
    volume: int | None
    start_year: int | None
    end_year: int | None
    cover_url: str | None = None


class CanonicalIssueSummary(APIBaseModel):
    id: int
    series_id: int
    event_id: int | None
    legacy_key: str
    issue_number: str
    issue_kind: str
    title: str | None
    sort_order: int
    published_on: date | None
    cover_url: str | None = None
    page_count: int | None = None


class IssueSummary(APIBaseModel):
    id: int
    series_id: int
    issue_number: str
    issue_kind: str
    title: str | None
    variant: str | None
    volume: int | None
    sort_order: int
    published_on: date | None
    cover_url: str | None = None
    page_count: int | None


class IssueMatchRead(APIBaseModel):
    id: int
    local_issue_id: int
    canonical_issue_id: int
    match_strategy: str
    confidence_score: int
    is_primary: bool
    note: str | None
    created_at: datetime
    updated_at: datetime
    local_issue: IssueSummary | None = None
    canonical_issue: CanonicalIssueSummary | None = None


class CanonicalIssueRead(CanonicalIssueSummary):
    summary: str | None
    is_read: bool = False
    pages: list[ArchivePageRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    series: CanonicalSeriesSummary | None = None
    event: EventSummary | None = None
    local_matches: list[IssueMatchRead] = Field(default_factory=list)


class IssueRead(IssueSummary):
    summary: str | None
    reading_path_id: int | None = None
    primary_canonical_issue_id: int | None = None
    is_read: bool = False
    cover_url: str | None
    archives: list[ArchiveRead] = Field(default_factory=list)
    canonical_matches: list[IssueMatchRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SeriesSummary(APIBaseModel):
    id: int
    canonical_series_id: int | None
    slug: str
    title: str
    publisher: str | None
    status: str
    start_year: int | None
    end_year: int | None
    issue_count: int = 0
    cover_url: str | None = None
    latest_published_on: date | None = None


class SeriesRead(SeriesSummary):
    description: str | None
    canonical_match_strategy: str | None = None
    canonical_match_confidence: int | None = None
    reading_path_id: int | None = None
    canonical_series: CanonicalSeriesSummary | None = None
    issues: list[IssueSummary] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ReadingPathEntryRead(APIBaseModel):
    id: int
    reading_path_id: int
    series_id: int | None
    issue_id: int | None
    canonical_series_id: int | None
    canonical_issue_id: int | None
    story_arc_id: int | None
    sort_order: int
    entry_type: str
    importance: str
    label: str | None
    note: str | None
    is_optional: bool
    issue_key: str | None = None
    is_read: bool = False
    cover_url: str | None = None
    created_at: datetime
    updated_at: datetime
    series: SeriesSummary | None = None
    issue: IssueSummary | None = None
    matched_issue: IssueSummary | None = None
    canonical_series: CanonicalSeriesSummary | None = None
    canonical_issue: CanonicalIssueSummary | None = None
    story_arc: StoryArcSummary | None = None


class ReadingPathSummary(APIBaseModel):
    id: int
    event_id: int | None
    slug: str
    title: str
    description: str | None
    status: str
    publisher_name: str | None = None
    source_name: str | None
    source_url: str | None
    issue_count: int = 0
    series_count: int = 0
    latest_issue_label: str | None = None
    first_published_on: date | None = None
    latest_published_on: date | None = None
    is_downloaded: bool = False
    access_mode: str = "download"
    unread_count: int = 0
    is_complete: bool = False
    last_read_at: datetime | None = None
    continuity_group_id: int | None = None
    previous_collection_id: int | None = None
    next_collection_id: int | None = None
    tags: list[str] = Field(default_factory=list)


class ReadingPathRead(ReadingPathSummary):
    description: str | None
    event: EventSummary | None = None
    entries: list[ReadingPathEntryRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class IssueStateWrite(APIBaseModel):
    read: bool
    mark_opened: bool = False


class IssueStateRead(APIBaseModel):
    issue_key: str
    issue_id: int | None = None
    canonical_issue_id: int | None = None
    is_read: bool
    read_at: datetime | None = None
    last_opened_at: datetime | None = None


class EventRead(EventSummary):
    description: str | None
    publisher: PublisherSummary | None = None
    story_arcs: list[StoryArcSummary] = Field(default_factory=list)
    reading_paths: list[ReadingPathSummary] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class StoryArcRead(StoryArcSummary):
    description: str | None
    event: EventSummary | None = None
    reading_path_entries: list[ReadingPathEntryRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CanonicalSeriesRead(CanonicalSeriesSummary):
    description: str | None
    publisher: PublisherSummary | None = None
    issues: list[CanonicalIssueSummary] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class LibrarySummaryResponse(APIBaseModel):
    series_count: int
    issue_count: int
    archive_count: int
    reading_path_count: int
    latest_series: list[SeriesSummary] = Field(default_factory=list)
    latest_issues: list[IssueSummary] = Field(default_factory=list)
    latest_reading_paths: list[ReadingPathSummary] = Field(default_factory=list)


class SeriesListResponse(APIBaseModel):
    items: list[SeriesSummary]
    total: int


class IssueListResponse(APIBaseModel):
    items: list[IssueSummary]
    total: int


class ReadingPathListResponse(APIBaseModel):
    items: list[ReadingPathSummary]
    total: int


class ReadingPathCoverRead(APIBaseModel):
    reading_path_id: int
    image_url: str | None = None
    post_url: str | None = None
    post_title: str | None = None
    query: str | None = None


class ReadingPathCoverBatchResponse(APIBaseModel):
    items: list[ReadingPathCoverRead] = Field(default_factory=list)


class ReadingPathDownloadResponse(APIBaseModel):
    reading_path_id: int
    entry_id: int | None = None
    post_url: str | None = None
    imported_paths: list[str] = Field(default_factory=list)
    downloaded_issue_count: int = 0
    skipped_issue_count: int = 0
    series_created: int = 0
    series_updated: int = 0
    issues_created: int = 0
    issues_updated: int = 0
    archives_created: int = 0
    archives_updated: int = 0


class PublisherListResponse(APIBaseModel):
    items: list[PublisherSummary]
    total: int


class EventListResponse(APIBaseModel):
    items: list[EventSummary]
    total: int


class StoryArcListResponse(APIBaseModel):
    items: list[StoryArcSummary]
    total: int


class CanonicalSeriesListResponse(APIBaseModel):
    items: list[CanonicalSeriesSummary]
    total: int


class CanonicalIssueListResponse(APIBaseModel):
    items: list[CanonicalIssueSummary]
    total: int


class IngestImportResponse(APIBaseModel):
    imported_paths: list[str]
    series_created: int
    series_updated: int
    issues_created: int
    issues_updated: int
    archives_created: int
    archives_updated: int
