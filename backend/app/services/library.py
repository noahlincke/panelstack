from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Archive, Issue, Series
from .ingest import ComicMetadata, ScanResult


SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
ISSUE_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class PersistResult:
    series_created: int = 0
    series_updated: int = 0
    issues_created: int = 0
    issues_updated: int = 0
    archives_created: int = 0
    archives_updated: int = 0

    def merge(self, other: "PersistResult") -> "PersistResult":
        return PersistResult(
            series_created=self.series_created + other.series_created,
            series_updated=self.series_updated + other.series_updated,
            issues_created=self.issues_created + other.issues_created,
            issues_updated=self.issues_updated + other.issues_updated,
            archives_created=self.archives_created + other.archives_created,
            archives_updated=self.archives_updated + other.archives_updated,
        )


def slugify(value: str) -> str:
    normalized = SLUG_PATTERN.sub("-", value.lower()).strip("-")
    return normalized or "item"


def issue_sort_order(value: str | None, fallback: int = 0) -> int:
    if not value:
        return fallback
    match = ISSUE_NUMBER_PATTERN.search(value)
    if not match:
        return fallback
    number = float(match.group(0))
    return int(number * 1000)


def archive_checksum(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_issue_number(metadata: ComicMetadata, source_path: str) -> str:
    if metadata.issue_kind == "annual":
        if metadata.issue:
            return f"Annual {metadata.issue}"
        if metadata.year:
            return f"Annual {metadata.year}"
    if metadata.issue_kind in {"one-shot", "special"} and metadata.title:
        return metadata.title
    if metadata.issue_kind == "collection":
        if metadata.issue:
            return metadata.issue
        if metadata.volume:
            return f"Vol.{metadata.volume}"
        return metadata.title or Path(source_path).stem
    if metadata.issue:
        return metadata.issue
    if metadata.volume:
        return f"Vol.{metadata.volume}"
    return Path(source_path).stem


def issue_title(series_title: str, issue_number: str, metadata: ComicMetadata) -> str:
    if metadata.issue_kind == "annual":
        return f"{series_title} Annual {metadata.issue or issue_number.removeprefix('Annual ').strip()}"
    if metadata.issue_kind in {"one-shot", "special"}:
        return metadata.title or Path(metadata.raw_name).stem or series_title
    if metadata.issue_kind == "collection":
        return Path(metadata.raw_name).stem or metadata.title or series_title
    if metadata.issue:
        return f"{series_title} #{issue_number}"
    return metadata.title or series_title


def get_or_create_series(db: Session, metadata: ComicMetadata) -> tuple[Series, PersistResult]:
    title = metadata.series or metadata.title or metadata.raw_name
    slug = slugify(title)

    series = db.scalar(select(Series).where(Series.slug == slug))
    if series is None:
        series = db.scalar(select(Series).where(Series.title == title))

    if series is None:
        series = Series(
            slug=slug,
            title=title,
            publisher=metadata.publisher,
            description=f"Imported from local library: {metadata.raw_name}",
            status="ongoing",
            start_year=metadata.year,
            end_year=metadata.year,
        )
        db.add(series)
        db.flush()
        return series, PersistResult(series_created=1)

    updated = False
    if metadata.publisher and not series.publisher:
        series.publisher = metadata.publisher
        updated = True
    if metadata.year and series.start_year is None:
        series.start_year = metadata.year
        updated = True
    if metadata.year and series.end_year is None:
        series.end_year = metadata.year
        updated = True

    return series, PersistResult(series_updated=1 if updated else 0)


def get_or_create_issue(
    db: Session,
    series: Series,
    scan: ScanResult,
) -> tuple[Issue, PersistResult]:
    number = canonical_issue_number(scan.metadata, scan.source_path)
    issue = db.scalar(
        select(Issue).where(
            Issue.series_id == series.id,
            Issue.issue_number == number,
            Issue.variant.is_(None),
        )
    )

    if issue is None:
        issue = Issue(
            series_id=series.id,
            issue_number=number,
            issue_kind=scan.metadata.issue_kind,
            title=issue_title(series.title, number, scan.metadata),
            variant=None,
            volume=int(scan.metadata.volume) if scan.metadata.volume and scan.metadata.volume.isdigit() else None,
            sort_order=issue_sort_order(scan.metadata.issue or scan.metadata.volume, fallback=0),
            published_on=None,
            summary=f"Imported from {Path(scan.source_path).name}",
            cover_url=None,
            page_count=scan.page_count,
        )
        db.add(issue)
        db.flush()
        return issue, PersistResult(issues_created=1)

    updated = False
    if scan.page_count and issue.page_count != scan.page_count:
        issue.page_count = scan.page_count
        updated = True
    if issue.issue_kind != scan.metadata.issue_kind:
        issue.issue_kind = scan.metadata.issue_kind
        updated = True
    if not issue.title:
        issue.title = issue_title(series.title, number, scan.metadata)
        updated = True
    if not issue.summary:
        issue.summary = f"Imported from {Path(scan.source_path).name}"
        updated = True
    return issue, PersistResult(issues_updated=1 if updated else 0)


def get_or_create_archive(
    db: Session,
    series: Series,
    issue: Issue,
    scan: ScanResult,
) -> tuple[Archive, PersistResult]:
    source_path = str(Path(scan.source_path).resolve())
    archive = db.scalar(select(Archive).where(Archive.storage_path == source_path))

    archive_format = scan.archive_format or scan.source_kind
    extracted_path = source_path if scan.source_kind == "directory" else None
    checksum = archive_checksum(Path(source_path))

    if archive is None:
        archive = Archive(
            series_id=series.id,
            issue_id=issue.id,
            storage_path=source_path,
            original_filename=Path(source_path).name,
            source_url=None,
            archive_format=archive_format,
            page_count=scan.page_count,
            size_bytes=scan.total_bytes,
            checksum_sha256=checksum,
            extracted_path=extracted_path,
            status="available",
        )
        db.add(archive)
        db.flush()
        return archive, PersistResult(archives_created=1)

    updated = False
    if archive.series_id != series.id:
        archive.series_id = series.id
        updated = True
    if archive.issue_id != issue.id:
        archive.issue_id = issue.id
        updated = True
    if archive.page_count != scan.page_count:
        archive.page_count = scan.page_count
        updated = True
    if archive.size_bytes != scan.total_bytes:
        archive.size_bytes = scan.total_bytes
        updated = True
    if archive.archive_format != archive_format:
        archive.archive_format = archive_format
        updated = True
    if archive.extracted_path != extracted_path:
        archive.extracted_path = extracted_path
        updated = True
    if checksum and archive.checksum_sha256 != checksum:
        archive.checksum_sha256 = checksum
        updated = True

    return archive, PersistResult(archives_updated=1 if updated else 0)


def persist_scan(db: Session, scan: ScanResult) -> tuple[Archive, PersistResult]:
    series, series_result = get_or_create_series(db, scan.metadata)
    issue, issue_result = get_or_create_issue(db, series, scan)
    archive, archive_result = get_or_create_archive(db, series, issue, scan)
    if scan.page_count > 0 and not issue.cover_url:
        issue.cover_url = f"/archives/{archive.id}/pages/1"
    from .curation import match_local_issue, match_local_series

    match_local_series(db, series)
    match_local_issue(db, issue)
    match_local_series(db, series)
    db.flush()
    return archive, series_result.merge(issue_result).merge(archive_result)


def persist_scans(db: Session, scans: list[ScanResult] | tuple[ScanResult, ...]) -> PersistResult:
    result = PersistResult()
    try:
        for scan in scans:
            _, item_result = persist_scan(db, scan)
            result = result.merge(item_result)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
