from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class Series(Base, TimestampMixin):
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    canonical_series_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_series.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    start_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    canonical_match_strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_match_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)

    issues: Mapped[list["Issue"]] = relationship(
        back_populates="series",
        cascade="all, delete-orphan",
        order_by="Issue.sort_order",
    )
    archives: Mapped[list["Archive"]] = relationship(back_populates="series")
    reading_path_entries: Mapped[list["ReadingPathEntry"]] = relationship(back_populates="series")
    canonical_series: Mapped["CanonicalSeries | None"] = relationship(foreign_keys=[canonical_series_id])


class Publisher(Base, TimestampMixin):
    __tablename__ = "publishers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    canonical_series: Mapped[list["CanonicalSeries"]] = relationship(back_populates="publisher")
    events: Mapped[list["Event"]] = relationship(back_populates="publisher")


class Event(Base, TimestampMixin):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    publisher_id: Mapped[int | None] = mapped_column(
        ForeignKey("publishers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    start_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    publisher: Mapped["Publisher | None"] = relationship(back_populates="events")
    story_arcs: Mapped[list["StoryArc"]] = relationship(back_populates="event")
    canonical_issues: Mapped[list["CanonicalIssue"]] = relationship(back_populates="event")
    reading_paths: Mapped[list["ReadingPath"]] = relationship(back_populates="event")


class StoryArc(Base, TimestampMixin):
    __tablename__ = "story_arcs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)

    event: Mapped["Event | None"] = relationship(back_populates="story_arcs")
    reading_path_entries: Mapped[list["ReadingPathEntry"]] = relationship(back_populates="story_arc")


class CanonicalSeries(Base, TimestampMixin):
    __tablename__ = "canonical_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    publisher_id: Mapped[int | None] = mapped_column(
        ForeignKey("publishers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_series_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    publisher: Mapped["Publisher | None"] = relationship(back_populates="canonical_series")
    issues: Mapped[list["CanonicalIssue"]] = relationship(
        back_populates="series",
        cascade="all, delete-orphan",
        order_by="CanonicalIssue.sort_order",
    )
    reading_path_entries: Mapped[list["ReadingPathEntry"]] = relationship(back_populates="canonical_series")
    aliases: Mapped[list["CanonicalSeriesAlias"]] = relationship(
        back_populates="canonical_series",
        cascade="all, delete-orphan",
        order_by="CanonicalSeriesAlias.alias.asc()",
    )
    catalog_collections: Mapped[list["CatalogCollection"]] = relationship(back_populates="canonical_series")


class CanonicalIssue(Base, TimestampMixin):
    __tablename__ = "canonical_issues"
    __table_args__ = (
        UniqueConstraint("series_id", "issue_number", name="uq_canonical_issue_identity"),
        UniqueConstraint("legacy_key", name="uq_canonical_issue_legacy_key"),
        Index("ix_canonical_issues_series_sort", "series_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_series.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    legacy_key: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_number: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_kind: Mapped[str] = mapped_column(String(32), default="issue", nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_issue_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    series: Mapped["CanonicalSeries"] = relationship(back_populates="issues")
    event: Mapped["Event | None"] = relationship(back_populates="canonical_issues")
    issue_matches: Mapped[list["IssueMatch"]] = relationship(
        back_populates="canonical_issue",
        cascade="all, delete-orphan",
    )
    reading_path_entries: Mapped[list["ReadingPathEntry"]] = relationship(back_populates="canonical_issue")
    catalog_items: Mapped[list["CatalogCollectionItem"]] = relationship(back_populates="canonical_issue")
    user_states: Mapped[list["UserIssueState"]] = relationship(back_populates="canonical_issue")


class Issue(Base, TimestampMixin):
    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("series_id", "issue_number", "variant", name="uq_issue_identity"),
        Index("ix_issues_series_sort", "series_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), nullable=False, index=True)
    issue_number: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_kind: Mapped[str] = mapped_column(String(32), default="issue", nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    variant: Mapped[str | None] = mapped_column(String(64), nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    series: Mapped["Series"] = relationship(back_populates="issues")
    archives: Mapped[list["Archive"]] = relationship(
        back_populates="issue",
        cascade="all, delete-orphan",
        order_by="Archive.created_at",
    )
    reading_path_entries: Mapped[list["ReadingPathEntry"]] = relationship(back_populates="issue")
    canonical_matches: Mapped[list["IssueMatch"]] = relationship(
        back_populates="local_issue",
        cascade="all, delete-orphan",
        order_by=lambda: IssueMatch.confidence_score.desc(),
    )
    catalog_items: Mapped[list["CatalogCollectionItem"]] = relationship(back_populates="issue")
    user_states: Mapped[list["UserIssueState"]] = relationship(back_populates="issue")


class Archive(Base, TimestampMixin):
    __tablename__ = "archives"
    __table_args__ = (
        UniqueConstraint("storage_path", name="uq_archive_storage_path"),
        UniqueConstraint("source_url", name="uq_archive_source_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id", ondelete="SET NULL"), nullable=True, index=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"), nullable=True, index=True)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    archive_format: Mapped[str] = mapped_column(String(16), default="cbz", nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extracted_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="available", nullable=False)

    series: Mapped["Series | None"] = relationship(back_populates="archives")
    issue: Mapped["Issue | None"] = relationship(back_populates="archives")


class ReadingPath(Base, TimestampMixin):
    __tablename__ = "reading_paths"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    event: Mapped["Event | None"] = relationship(back_populates="reading_paths")

    entries: Mapped[list["ReadingPathEntry"]] = relationship(
        back_populates="reading_path",
        cascade="all, delete-orphan",
        order_by="ReadingPathEntry.sort_order",
    )
    cover_asset: Mapped["ReadingPathCoverAsset | None"] = relationship(
        back_populates="reading_path",
        cascade="all, delete-orphan",
        uselist=False,
    )
    catalog_collection: Mapped["CatalogCollection | None"] = relationship(
        back_populates="reading_path",
        cascade="all, delete-orphan",
        uselist=False,
    )


class ReadingPathEntry(Base, TimestampMixin):
    __tablename__ = "reading_path_entries"
    __table_args__ = (
        UniqueConstraint("reading_path_id", "sort_order", name="uq_reading_path_sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    reading_path_id: Mapped[int] = mapped_column(
        ForeignKey("reading_paths.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id", ondelete="SET NULL"), nullable=True, index=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"), nullable=True, index=True)
    canonical_series_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_series.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    canonical_issue_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_issues.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    story_arc_id: Mapped[int | None] = mapped_column(
        ForeignKey("story_arcs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), default="issue", nullable=False)
    importance: Mapped[str] = mapped_column(String(32), default="main", nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_optional: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    reading_path: Mapped["ReadingPath"] = relationship(back_populates="entries")
    series: Mapped["Series | None"] = relationship(back_populates="reading_path_entries")
    issue: Mapped["Issue | None"] = relationship(back_populates="reading_path_entries")
    canonical_series: Mapped["CanonicalSeries | None"] = relationship(back_populates="reading_path_entries")
    canonical_issue: Mapped["CanonicalIssue | None"] = relationship(back_populates="reading_path_entries")
    story_arc: Mapped["StoryArc | None"] = relationship(back_populates="reading_path_entries")
    catalog_item: Mapped["CatalogCollectionItem | None"] = relationship(
        back_populates="reading_path_entry",
        cascade="all, delete-orphan",
        uselist=False,
    )


class IssueMatch(Base, TimestampMixin):
    __tablename__ = "issue_matches"
    __table_args__ = (
        UniqueConstraint("local_issue_id", "canonical_issue_id", name="uq_issue_match_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    local_issue_id: Mapped[int] = mapped_column(
        ForeignKey("issues.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    canonical_issue_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_issues.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_strategy: Mapped[str] = mapped_column(String(64), nullable=False, default="series+issue")
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    local_issue: Mapped["Issue"] = relationship(back_populates="canonical_matches")
    canonical_issue: Mapped["CanonicalIssue"] = relationship(back_populates="issue_matches")


class ReadingPathCoverAsset(Base, TimestampMixin):
    __tablename__ = "reading_path_cover_assets"
    __table_args__ = (
        UniqueConstraint("reading_path_id", name="uq_reading_path_cover_asset_path"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    reading_path_id: Mapped[int] = mapped_column(
        ForeignKey("reading_paths.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    query: Mapped[str | None] = mapped_column(String(255), nullable=True)
    post_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    reading_path: Mapped["ReadingPath"] = relationship(back_populates="cover_asset")


class CurationSource(Base, TimestampMixin):
    __tablename__ = "curation_sources"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_curation_source_slug"),
        UniqueConstraint("source_url", name="uq_curation_source_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), default="editorial", nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    catalog_collections: Mapped[list["CatalogCollection"]] = relationship(back_populates="source")
    catalog_items: Mapped[list["CatalogCollectionItem"]] = relationship(back_populates="source")


class ContinuityGroup(Base, TimestampMixin):
    __tablename__ = "continuity_groups"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_continuity_group_slug"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    publisher_id: Mapped[int | None] = mapped_column(
        ForeignKey("publishers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    publisher: Mapped["Publisher | None"] = relationship()
    collections: Mapped[list["CatalogCollection"]] = relationship(
        back_populates="continuity_group",
        order_by="CatalogCollection.sequence_number",
    )


class CatalogCollection(Base, TimestampMixin):
    __tablename__ = "catalog_collections"
    __table_args__ = (
        UniqueConstraint("reading_path_id", name="uq_catalog_collection_reading_path"),
        UniqueConstraint("slug", name="uq_catalog_collection_slug"),
        Index("ix_catalog_collections_continuity_sequence", "continuity_group_id", "sequence_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    reading_path_id: Mapped[int | None] = mapped_column(
        ForeignKey("reading_paths.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    canonical_series_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_series.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    continuity_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("continuity_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    publisher_id: Mapped[int | None] = mapped_column(
        ForeignKey("publishers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("curation_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_title: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    line: Mapped[str] = mapped_column(String(32), default="series", nullable=False)
    collection_type: Mapped[str] = mapped_column(String(32), default="run", nullable=False)
    volume_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sequence_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    start_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_published_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    latest_published_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    reading_path: Mapped["ReadingPath | None"] = relationship(back_populates="catalog_collection")
    canonical_series: Mapped["CanonicalSeries | None"] = relationship(back_populates="catalog_collections")
    continuity_group: Mapped["ContinuityGroup | None"] = relationship(back_populates="collections")
    publisher: Mapped["Publisher | None"] = relationship()
    source: Mapped["CurationSource | None"] = relationship(back_populates="catalog_collections")
    items: Mapped[list["CatalogCollectionItem"]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
        order_by="CatalogCollectionItem.sort_order",
    )
    aliases: Mapped[list["CatalogCollectionAlias"]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
        order_by="CatalogCollectionAlias.alias.asc()",
    )
    tags: Mapped[list["CatalogCollectionTag"]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
        order_by="CatalogCollectionTag.tag.asc()",
    )


class CatalogCollectionItem(Base, TimestampMixin):
    __tablename__ = "catalog_collection_items"
    __table_args__ = (
        UniqueConstraint("reading_path_entry_id", name="uq_catalog_collection_item_entry"),
        UniqueConstraint("collection_id", "sort_order", name="uq_catalog_collection_item_sort"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reading_path_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("reading_path_entries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"), nullable=True, index=True)
    canonical_issue_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_issues.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("curation_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    item_type: Mapped[str] = mapped_column(String(32), default="issue", nullable=False)
    importance: Mapped[str] = mapped_column(String(32), default="main", nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_optional: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    collection: Mapped["CatalogCollection"] = relationship(back_populates="items")
    reading_path_entry: Mapped["ReadingPathEntry | None"] = relationship(back_populates="catalog_item")
    issue: Mapped["Issue | None"] = relationship(back_populates="catalog_items")
    canonical_issue: Mapped["CanonicalIssue | None"] = relationship(back_populates="catalog_items")
    source: Mapped["CurationSource | None"] = relationship(back_populates="catalog_items")


class CatalogCollectionAlias(Base, TimestampMixin):
    __tablename__ = "catalog_collection_aliases"
    __table_args__ = (
        UniqueConstraint("collection_id", "alias", name="uq_catalog_collection_alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    collection: Mapped["CatalogCollection"] = relationship(back_populates="aliases")


class CatalogCollectionTag(Base, TimestampMixin):
    __tablename__ = "catalog_collection_tags"
    __table_args__ = (
        UniqueConstraint("collection_id", "tag", name="uq_catalog_collection_tag"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    collection: Mapped["CatalogCollection"] = relationship(back_populates="tags")


class CanonicalSeriesAlias(Base, TimestampMixin):
    __tablename__ = "canonical_series_aliases"
    __table_args__ = (
        UniqueConstraint("canonical_series_id", "alias", name="uq_canonical_series_alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    canonical_series_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_series.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    canonical_series: Mapped["CanonicalSeries"] = relationship(back_populates="aliases")


class UserIssueState(Base, TimestampMixin):
    __tablename__ = "user_issue_states"
    __table_args__ = (
        UniqueConstraint("issue_key", name="uq_user_issue_state_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    issue_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    issue_id: Mapped[int | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"), nullable=True, index=True)
    canonical_issue_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_issues.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    issue: Mapped["Issue | None"] = relationship(back_populates="user_states")
    canonical_issue: Mapped["CanonicalIssue | None"] = relationship(back_populates="user_states")
