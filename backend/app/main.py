from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from functools import lru_cache
import logging
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import requests
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .db import SessionLocal, engine, ensure_runtime_schema, get_db
from .auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_DAYS,
    auth_enabled,
    create_session_cookie,
    verify_password,
    verify_session_cookie,
)
from .models import (
    Archive,
    Base,
    CanonicalIssue,
    CanonicalSeries,
    CatalogCollection,
    CatalogCollectionItem,
    ContinuityGroup,
    Event,
    Issue,
    IssueMatch,
    Publisher,
    ReadingPath,
    ReadingPathEntry,
    Series,
    StoryArc,
    UserIssueState,
)
from .routers import ingest_router
from .schemas import (
    ArchivePageListResponse,
    ArchivePageRead,
    CanonicalIssueListResponse,
    CanonicalIssueRead,
    CanonicalIssueSummary,
    CanonicalSeriesListResponse,
    CanonicalSeriesRead,
    CanonicalSeriesSummary,
    EventListResponse,
    EventRead,
    EventSummary,
    HealthResponse,
    IssueStateRead,
    IssueStateWrite,
    IssueListResponse,
    IssueRead,
    IssueSummary,
    LibrarySummaryResponse,
    PublisherListResponse,
    PublisherRead,
    PublisherSummary,
    ReadingPathCoverBatchResponse,
    ReadingPathCoverRead,
    ReadingPathDownloadResponse,
    ReadingPathListResponse,
    ReadingPathRead,
    ReadingPathSummary,
    SeriesListResponse,
    SeriesRead,
    SeriesSummary,
    StoryArcListResponse,
    StoryArcRead,
    StoryArcSummary,
)
from .services import (
    archive_is_streamable,
    archive_page_bytes,
    ensure_remote_cover_image,
    ensure_query_cover_image,
    ensure_reading_path_cover_asset,
    fetch_mangapill_chapter_pages,
    fetch_getcomics_cover,
    get_mangapill_collection_cover_url,
    list_archive_pages,
    MANGAPILL_DATA_PATH,
    PersistResult,
    persist_scans,
    scan_source,
    sync_catalog_data,
    sync_curation_data,
    sync_mangapill_catalog,
)
from .services.ingest import ComicMetadata, PageRecord, ScanResult

SeriesSort = Literal["title", "latest_published_desc", "latest_published_asc"]
ReadingPathSort = Literal["title", "latest_published_desc", "latest_published_asc"]
REPO_ROOT = Path(__file__).resolve().parents[2]
DOWNLOADS_ROOT = REPO_ROOT / "downloads"
logger = logging.getLogger(__name__)


def _series_latest_published_on(series: Series) -> date | None:
    return max((issue.published_on for issue in series.issues if issue.published_on is not None), default=None)


def _issue_cover_url(issue: Issue) -> str | None:
    if issue.cover_url:
        return issue.cover_url
    for archive in issue.archives:
        if archive_is_streamable(archive):
            return f"/archives/{archive.id}/pages/1"
    return None


def _issue_summary(issue: Issue) -> IssueSummary:
    return IssueSummary(
        id=issue.id,
        series_id=issue.series_id,
        issue_number=issue.issue_number,
        issue_kind=issue.issue_kind,
        title=issue.title,
        variant=issue.variant,
        volume=issue.volume,
        sort_order=issue.sort_order,
        published_on=issue.published_on,
        cover_url=_issue_cover_url(issue),
        page_count=issue.page_count,
    )


def _canonical_issue_cover_url(issue: CanonicalIssue) -> str | None:
    return issue.cover_url or (issue.series.cover_url if issue.series is not None else None)


@lru_cache(maxsize=2048)
def _mangapill_first_page_image(chapter_url: str) -> str | None:
    pages = fetch_mangapill_chapter_pages(chapter_url)
    return pages[0] if pages else None


def _provider_issue_cover_url(issue: CanonicalIssue) -> str | None:
    if issue.provider_name == "MangaPill" and issue.provider_url:
        return _mangapill_first_page_image(issue.provider_url) or _canonical_issue_cover_url(issue)
    return _canonical_issue_cover_url(issue)


def _canonical_issue_pages(issue: CanonicalIssue) -> list[ArchivePageRead]:
    if issue.provider_name != "MangaPill" or not issue.provider_url:
        return []
    return [
        ArchivePageRead(
            index=index,
            relative_path=f"page-{index}",
            media_type="image/jpeg",
            image_url=f"/canonical-issues/{issue.id}/pages/{index}",
        )
        for index, image_url in enumerate(fetch_mangapill_chapter_pages(issue.provider_url), start=1)
    ]


def _primary_canonical_issue_id(issue: Issue) -> int | None:
    primary = next((match for match in issue.canonical_matches if match.is_primary), None)
    if primary is not None:
        return primary.canonical_issue_id
    if issue.canonical_matches:
        return issue.canonical_matches[0].canonical_issue_id
    return None


def _issue_state_key(*, issue_id: int | None = None, canonical_issue_id: int | None = None) -> str | None:
    if canonical_issue_id is not None:
        return f"canonical:{canonical_issue_id}"
    if issue_id is not None:
        return f"issue:{issue_id}"
    return None


def _read_state_map(
    db: Session,
    *,
    canonical_issue_ids: set[int] | None = None,
    issue_ids: set[int] | None = None,
) -> dict[str, UserIssueState]:
    canonical_issue_ids = canonical_issue_ids or set()
    issue_ids = issue_ids or set()
    if not canonical_issue_ids and not issue_ids:
        return {}

    clauses = []
    if canonical_issue_ids:
        clauses.append(UserIssueState.canonical_issue_id.in_(canonical_issue_ids))
    if issue_ids:
        clauses.append(UserIssueState.issue_id.in_(issue_ids))

    states = db.scalars(select(UserIssueState).where(or_(*clauses))).all()
    return {state.issue_key: state for state in states}


def _upsert_issue_state(
    db: Session,
    *,
    issue_id: int | None = None,
    canonical_issue_id: int | None = None,
    read: bool,
    mark_opened: bool = False,
) -> UserIssueState:
    issue_key = _issue_state_key(issue_id=issue_id, canonical_issue_id=canonical_issue_id)
    if issue_key is None:
        raise HTTPException(status_code=400, detail="This issue cannot be tracked for read state.")
    state = db.scalar(select(UserIssueState).where(UserIssueState.issue_key == issue_key))
    if state is None:
        state = UserIssueState(issue_key=issue_key, issue_id=issue_id, canonical_issue_id=canonical_issue_id)
        db.add(state)
    state.issue_id = issue_id
    state.canonical_issue_id = canonical_issue_id
    state.is_read = read
    state.read_at = datetime.now(timezone.utc) if read else None
    if mark_opened:
        state.last_opened_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(state)
    return state


def _series_cover_url(series: Series) -> str | None:
    if not series.issues:
        return None
    latest_issue = max(series.issues, key=lambda issue: ((issue.published_on or date.min), issue.sort_order, issue.id))
    cover_url = _issue_cover_url(latest_issue)
    if cover_url:
        return cover_url
    for issue in sorted(series.issues, key=lambda item: (item.sort_order, item.id), reverse=True):
        cover_url = _issue_cover_url(issue)
        if cover_url:
            return cover_url
    return None


def _series_summary(series: Series) -> SeriesSummary:
    return SeriesSummary(
        id=series.id,
        canonical_series_id=series.canonical_series_id,
        slug=series.slug,
        title=series.title,
        publisher=series.publisher,
        status=series.status,
        start_year=series.start_year,
        end_year=series.end_year,
        issue_count=len(series.issues),
        cover_url=_series_cover_url(series),
        latest_published_on=_series_latest_published_on(series),
    )


def _series_reading_path_id(db: Session, series: Series) -> int | None:
    if series.canonical_series_id is not None:
        collection_id = db.scalar(
            select(CatalogCollection.id)
            .where(CatalogCollection.canonical_series_id == series.canonical_series_id)
            .order_by(CatalogCollection.sequence_number.asc(), CatalogCollection.id.asc())
            .limit(1)
        )
        if collection_id is not None:
            return collection_id
    if series.canonical_series_id is not None:
        matched_path_id = db.scalar(
            select(ReadingPath.id)
            .join(ReadingPathEntry, ReadingPathEntry.reading_path_id == ReadingPath.id)
            .where(
                ReadingPath.event_id.is_(None),
                ReadingPathEntry.canonical_series_id == series.canonical_series_id,
            )
            .order_by(ReadingPath.id.asc())
            .limit(1)
        )
        if matched_path_id is not None:
            return matched_path_id
    matched_by_title = db.scalar(
        select(ReadingPath.id)
        .where(
            ReadingPath.event_id.is_(None),
            func.lower(ReadingPath.title).like(f"{series.title.lower()}%"),
        )
        .order_by(ReadingPath.id.asc())
        .limit(1)
    )
    return matched_by_title


def _reading_path_issue_dates(reading_path: ReadingPath) -> tuple[date | None, date | None]:
    published_dates = [
        entry.canonical_issue.published_on or (entry.issue.published_on if entry.issue is not None else None)
        for entry in reading_path.entries
        if entry.entry_type == "issue"
        and (entry.canonical_issue is not None or entry.issue is not None)
    ]
    valid_dates = [published_on for published_on in published_dates if published_on is not None]
    if not valid_dates:
        return None, None
    return min(valid_dates), max(valid_dates)


def _reading_path_latest_issue_entry(reading_path: ReadingPath) -> ReadingPathEntry | None:
    issue_entries = [entry for entry in reading_path.entries if entry.entry_type == "issue"]
    if not issue_entries:
        return None
    return max(issue_entries, key=lambda entry: (entry.sort_order, entry.id))


def _reading_path_issue_label(entry: ReadingPathEntry | None) -> str | None:
    if entry is None:
        return None
    if entry.canonical_issue is not None:
        if entry.canonical_issue.title:
            return entry.canonical_issue.title
        return f"{entry.canonical_issue.series.title} #{entry.canonical_issue.issue_number}"
    if entry.issue is not None:
        if entry.issue.title:
            return entry.issue.title
        return f"{entry.issue.series.title} #{entry.issue.issue_number}"
    return None


def _reading_path_cover_query(reading_path: ReadingPath) -> str:
    latest_entry = _reading_path_latest_issue_entry(reading_path)
    latest_issue_label = _reading_path_issue_label(latest_entry)
    return latest_issue_label or reading_path.title


def _reading_path_cover_context(reading_path: ReadingPath) -> tuple[str | None, str | None, int | None]:
    latest_entry = _reading_path_latest_issue_entry(reading_path)
    if latest_entry is None:
        return None, None, None
    if latest_entry.canonical_issue is not None:
        issue = latest_entry.canonical_issue
        year = issue.published_on.year if issue.published_on is not None else None
        return issue.series.title, issue.issue_number, year
    if latest_entry.issue is not None:
        issue = latest_entry.issue
        year = issue.published_on.year if issue.published_on is not None else None
        return issue.series.title, issue.issue_number, year
    return None, None, None


def _reading_path_provider_cover_url(reading_path: ReadingPath) -> str | None:
    if reading_path.source_name == "MangaPill":
        configured_cover_url = get_mangapill_collection_cover_url(reading_path.slug)
        if configured_cover_url:
            return configured_cover_url
        latest_entry = _reading_path_latest_issue_entry(reading_path)
        if latest_entry is not None and latest_entry.canonical_issue is not None:
            series_cover = _canonical_issue_cover_url(latest_entry.canonical_issue)
            if series_cover:
                return series_cover
    latest_entry = _reading_path_latest_issue_entry(reading_path)
    if latest_entry is None or latest_entry.canonical_issue is None:
        return None
    return _provider_issue_cover_url(latest_entry.canonical_issue)


def _provider_cover_referer(image_url: str, fallback_referer: str | None) -> str | None:
    lowered = image_url.lower()
    if "static.wikia.nocookie.net" in lowered or "fandom.com" in lowered:
        return None
    return fallback_referer


def _reading_path_download_context(reading_path: ReadingPath) -> tuple[str, str | None, str | None, int | None]:
    query = _reading_path_cover_query(reading_path)
    expected_series_title, expected_issue_number, expected_year = _reading_path_cover_context(reading_path)
    return query, expected_series_title, expected_issue_number, expected_year


def _reading_path_entry_download_context(entry: ReadingPathEntry) -> tuple[str, str | None, str | None, int | None]:
    query = _reading_path_issue_label(entry) or entry.label or f"entry-{entry.id}"
    if entry.canonical_issue is not None:
        issue = entry.canonical_issue
        year = issue.published_on.year if issue.published_on is not None else None
        return query, issue.series.title, issue.issue_number, year
    if entry.issue is not None:
        issue = entry.issue
        year = issue.published_on.year if issue.published_on is not None else None
        return query, issue.series.title, issue.issue_number, year
    return query, None, None, None


def _reading_path_entry_cover_context(entry: ReadingPathEntry) -> tuple[str, str | None, str | None, int | None]:
    return _reading_path_entry_download_context(entry)


def _entry_has_local_match(entry: ReadingPathEntry) -> bool:
    if entry.issue is not None:
        return True
    if entry.canonical_issue is None:
        return False
    return any(match.local_issue is not None for match in entry.canonical_issue.issue_matches)


def _issue_has_streamable_archive(issue: Issue) -> bool:
    return any(archive_is_streamable(archive) for archive in issue.archives)


def _entry_streamable_local_issue(entry: ReadingPathEntry) -> Issue | None:
    if entry.issue is not None and _issue_has_streamable_archive(entry.issue):
        return entry.issue
    if entry.canonical_issue is None:
        return None

    primary_match = next(
        (
            match
            for match in entry.canonical_issue.issue_matches
            if match.is_primary and match.local_issue is not None and _issue_has_streamable_archive(match.local_issue)
        ),
        None,
    )
    if primary_match is not None:
        return primary_match.local_issue

    return next(
        (
            match.local_issue
            for match in entry.canonical_issue.issue_matches
            if match.local_issue is not None and _issue_has_streamable_archive(match.local_issue)
        ),
        None,
    )


def _entry_has_streamable_local_match(entry: ReadingPathEntry) -> bool:
    return _entry_streamable_local_issue(entry) is not None


def _catalog_collection_neighbors(collection: CatalogCollection | None) -> tuple[int | None, int | None]:
    if collection is None or collection.continuity_group is None:
        return None, None
    ordered = sorted(
        collection.continuity_group.collections,
        key=lambda item: (item.sequence_number, item.first_published_on or date.min, item.id),
    )
    for index, item in enumerate(ordered):
        if item.id != collection.id:
            continue
        previous_id = ordered[index - 1].id if index > 0 else None
        next_id = ordered[index + 1].id if index < len(ordered) - 1 else None
        return previous_id, next_id
    return None, None


def _catalog_collection_progress(
    collection: CatalogCollection | None,
    state_map: dict[str, UserIssueState],
) -> tuple[int, bool, datetime | None]:
    if collection is None:
        return 0, False, None
    issue_items = [item for item in collection.items if item.item_type == "issue"]
    unread_count = 0
    read_timestamps: list[datetime] = []
    for item in issue_items:
        issue_key = _issue_state_key(issue_id=item.issue_id, canonical_issue_id=item.canonical_issue_id)
        state = state_map.get(issue_key) if issue_key is not None else None
        if state is not None and state.is_read:
            if state.read_at is not None:
                read_timestamps.append(state.read_at)
        else:
            unread_count += 1
    is_complete = bool(issue_items) and unread_count == 0
    last_read_at = max(read_timestamps) if read_timestamps else None
    return unread_count, is_complete, last_read_at


def _parse_download_output(stdout: str) -> list[str]:
    imported_paths: list[str] = []

    for line in stdout.splitlines():
        if line.startswith("Saved archive: "):
            imported_paths.append(line.removeprefix("Saved archive: ").strip())
        elif line.startswith("Extracted to: "):
            extracted_path = line.removeprefix("Extracted to: ").strip()
            if extracted_path:
                imported_paths.append(extracted_path)

    unique_paths: list[str] = []
    seen_paths: set[str] = set()
    for raw_path in imported_paths:
        normalized = str(Path(raw_path).expanduser().resolve())
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        unique_paths.append(normalized)
    return unique_paths


def _import_downloaded_paths(db: Session, imported_paths: list[str]) -> tuple[list[str], PersistResult]:
    existing_paths: list[str] = []
    for raw_path in imported_paths:
        candidate = Path(raw_path)
        if candidate.exists():
            existing_paths.append(str(candidate.resolve()))

    if not existing_paths:
        raise HTTPException(status_code=502, detail="Downloader finished but no importable files were produced.")

    preferred_paths = [path for path in existing_paths if Path(path).is_dir()] or existing_paths
    scans = [scan_source(path) for path in preferred_paths]
    result = persist_scans(db, scans)
    sync_catalog_data(db)
    return preferred_paths, result


def _download_post_to_library(db: Session, post_url: str) -> tuple[list[str], PersistResult]:
    DOWNLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(REPO_ROOT / "comics.py"),
        post_url,
        "--output-dir",
        str(DOWNLOADS_ROOT),
    ]
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        error_output = completed.stderr.strip() or completed.stdout.strip() or "Downloader failed."
        raise HTTPException(status_code=502, detail=error_output)

    imported_paths = _parse_download_output(completed.stdout)
    return _import_downloaded_paths(db, imported_paths)


def _sanitize_download_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\\\|?*]+', "-", value).strip().rstrip(".")
    return cleaned or "download"


def _provider_series_title(issue: CanonicalIssue) -> str:
    return issue.series.title if issue.series is not None else "Provider Series"


def _provider_issue_directory(issue: CanonicalIssue) -> Path:
    series_title = _provider_series_title(issue)
    issue_number = issue.issue_number
    if re.fullmatch(r"\d+(?:\.\d+)?", issue_number):
        try:
            numeric_issue = float(issue_number)
            if numeric_issue.is_integer():
                issue_segment = f"{int(numeric_issue):03d}"
            else:
                issue_segment = issue_number
        except ValueError:
            issue_segment = issue_number
    else:
        issue_segment = issue_number
    directory_name = _sanitize_download_name(f"{series_title} {issue_segment}")
    return DOWNLOADS_ROOT / directory_name


def _provider_download_binary(url: str, *, referer_url: str | None = None) -> tuple[bytes, str | None]:
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer_url:
        headers["Referer"] = referer_url
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.content, response.headers.get("Content-Type")


def _provider_extension(image_url: str, content_type: str | None) -> str:
    if content_type:
        normalized = content_type.split(";", 1)[0].strip().lower()
        if normalized == "image/jpeg":
            return ".jpg"
        if normalized == "image/png":
            return ".png"
        if normalized == "image/webp":
            return ".webp"
        if normalized == "image/gif":
            return ".gif"
        if normalized == "image/avif":
            return ".avif"
    suffix = Path(image_url.split("?", 1)[0]).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        return suffix
    return ".jpg"


def _provider_scan(issue: CanonicalIssue, directory: Path) -> ScanResult:
    issue_number = issue.issue_number
    metadata = ComicMetadata(
        raw_name=directory.name,
        title=_provider_series_title(issue),
        series=_provider_series_title(issue),
        issue=issue_number if re.fullmatch(r"\d+(?:\.\d+)?", issue_number) else None,
        volume=None,
        issue_kind="issue",
        year=issue.published_on.year if issue.published_on is not None else issue.series.start_year if issue.series is not None else None,
        publisher=issue.series.publisher.name if issue.series is not None and issue.series.publisher is not None else None,
        confidence=1.0,
    )
    page_paths = sorted(
        [candidate for candidate in directory.iterdir() if candidate.is_file()],
        key=lambda item: item.name,
    )
    pages = tuple(
        PageRecord(
            index=index,
            relative_path=path.name,
            size_bytes=path.stat().st_size,
            extension=path.suffix.lower(),
        )
        for index, path in enumerate(page_paths, start=1)
    )
    return ScanResult(
        source_path=str(directory),
        source_kind="directory",
        archive_format=None,
        page_count=len(pages),
        file_count=len(pages),
        total_bytes=sum(path.stat().st_size for path in page_paths),
        metadata=metadata,
        pages=pages,
        warnings=(),
    )


def _download_provider_issue_to_library(db: Session, issue: CanonicalIssue) -> tuple[list[str], PersistResult]:
    if issue.provider_name != "MangaPill" or not issue.provider_url:
        raise HTTPException(status_code=409, detail="This issue does not support provider download.")

    page_urls = fetch_mangapill_chapter_pages(issue.provider_url)
    if not page_urls:
        raise HTTPException(status_code=502, detail="Provider returned no downloadable pages for this issue.")

    DOWNLOADS_ROOT.mkdir(parents=True, exist_ok=True)
    target_dir = _provider_issue_directory(issue)
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    for index, page_url in enumerate(page_urls, start=1):
        content, content_type = _provider_download_binary(page_url, referer_url=issue.provider_url)
        extension = _provider_extension(page_url, content_type)
        page_path = target_dir / f"{index:04d}{extension}"
        page_path.write_bytes(content)

    scan = _provider_scan(issue, target_dir)
    result = persist_scans(db, [scan])

    local_issue = next(
        (
            match.local_issue
            for match in issue.issue_matches
            if match.is_primary and match.local_issue is not None
        ),
        None,
    )
    if local_issue is None:
        local_issue = db.scalar(
            select(Issue)
            .join(Series, Series.id == Issue.series_id)
            .where(Series.title == _provider_series_title(issue), Issue.issue_number == issue.issue_number)
            .limit(1)
        )
    if local_issue is not None:
        if issue.title:
            local_issue.title = issue.title
        local_issue.summary = issue.summary or local_issue.summary
        local_issue.published_on = issue.published_on
        db.commit()

    sync_catalog_data(db)
    return [str(target_dir.resolve())], result


def _delete_path_if_present(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    path.unlink(missing_ok=True)


def _delete_archive_files(archives: list[Archive]) -> None:
    paths_to_delete: list[Path] = []
    seen: set[str] = set()
    for archive in archives:
        for raw_path in (archive.extracted_path, archive.storage_path):
            if not raw_path:
                continue
            resolved = str(Path(raw_path).expanduser().resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            paths_to_delete.append(Path(resolved))

    for target in sorted(paths_to_delete, key=lambda item: len(item.parts), reverse=True):
        _delete_path_if_present(target)


def _open_path_in_file_manager(path: Path) -> None:
    target = path.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
        return
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", str(target)])
        return
    subprocess.Popen(["xdg-open", str(target)])


def _reading_path_summary(
    reading_path: ReadingPath,
    *,
    collection: CatalogCollection | None = None,
    state_map: dict[str, UserIssueState] | None = None,
) -> ReadingPathSummary:
    first_published_on, latest_published_on = _reading_path_issue_dates(reading_path)
    issue_entries = [entry for entry in reading_path.entries if entry.entry_type == "issue"]
    latest_issue_label = _reading_path_issue_label(_reading_path_latest_issue_entry(reading_path))
    canonical_series_ids = {
        entry.canonical_series_id or (entry.canonical_issue.series_id if entry.canonical_issue is not None else None)
        for entry in issue_entries
    }
    canonical_series_ids.discard(None)
    is_downloaded = any(
        entry.issue is not None
        or (
            entry.canonical_issue is not None
            and any(match.local_issue is not None for match in entry.canonical_issue.issue_matches)
        )
        for entry in issue_entries
    )
    publisher_name = None
    if reading_path.event is not None and reading_path.event.publisher is not None:
        publisher_name = reading_path.event.publisher.name
    else:
        for entry in issue_entries:
            if entry.canonical_issue is not None and entry.canonical_issue.series.publisher is not None:
                publisher_name = entry.canonical_issue.series.publisher.name
                break
    state_map = state_map or {}
    unread_count, is_complete, last_read_at = _catalog_collection_progress(collection, state_map)
    previous_collection_id, next_collection_id = _catalog_collection_neighbors(collection)
    tags = [tag.tag for tag in collection.tags] if collection is not None else []
    access_mode = "download"
    if any(entry.canonical_issue is not None and entry.canonical_issue.provider_name for entry in issue_entries):
        access_mode = "stream"
    return ReadingPathSummary(
        id=reading_path.id,
        event_id=reading_path.event_id,
        slug=reading_path.slug,
        title=reading_path.title,
        description=reading_path.description,
        status=reading_path.status,
        publisher_name=publisher_name,
        source_name=reading_path.source_name,
        source_url=reading_path.source_url,
        issue_count=len(issue_entries),
        series_count=len(canonical_series_ids),
        latest_issue_label=latest_issue_label,
        first_published_on=first_published_on,
        latest_published_on=latest_published_on,
        is_downloaded=is_downloaded,
        access_mode=access_mode,
        unread_count=unread_count,
        is_complete=is_complete,
        last_read_at=last_read_at,
        continuity_group_id=collection.continuity_group_id if collection is not None else None,
        previous_collection_id=previous_collection_id,
        next_collection_id=next_collection_id,
        tags=tags,
    )


def _sort_key_latest_desc(value: date | None, fallback: str) -> tuple[int, int, str]:
    return (
        1 if value is None else 0,
        -(value.toordinal()) if value is not None else 0,
        _natural_text_sort_key(fallback),
    )


def _sort_key_latest_asc(value: date | None, fallback: str) -> tuple[int, date, tuple[tuple[int, str | float], ...]]:
    return (1 if value is None else 0, value or date.max, _natural_text_sort_key(fallback))


def _natural_text_sort_key(value: str) -> tuple[tuple[int, str | float], ...]:
    tokens: list[tuple[int, str | float]] = []
    for token in re.findall(r"\d+(?:\.\d+)?|[^\d]+", value.lower()):
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            tokens.append((0, float(token)))
        else:
            normalized = re.sub(r"\s+", " ", token).strip()
            if normalized:
                tokens.append((1, normalized))
    return tuple(tokens)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()
    with SessionLocal() as db:
        sync_curation_data(db)
        sync_mangapill_catalog(db, MANGAPILL_DATA_PATH)
        sync_catalog_data(db)
    yield


app = FastAPI(
    title="Comic Library API",
    version="0.2.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def require_authentication(request: Request, call_next):
    if request.method == "OPTIONS" or not auth_enabled():
        return await call_next(request)
    if request.url.path in {"/auth/session", "/auth/login", "/health"} or request.url.path.startswith("/auth/"):
        return await call_next(request)

    session_state = verify_session_cookie(request.cookies.get(SESSION_COOKIE_NAME))
    if not session_state.authenticated:
        return Response(status_code=401, media_type="application/json", content='{"detail":"Authentication required."}')
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(ingest_router)


@app.get("/auth/session")
def get_auth_session(request: Request) -> dict[str, bool]:
    session_state = verify_session_cookie(request.cookies.get(SESSION_COOKIE_NAME))
    return {
        "enabled": auth_enabled(),
        "authenticated": bool(session_state.authenticated),
    }


@app.post("/auth/login")
def login(payload: dict[str, str], request: Request, response: Response) -> dict[str, bool]:
    if not auth_enabled():
        return {"enabled": False, "authenticated": True}

    if not verify_password(payload.get("password", "")):
        raise HTTPException(status_code=401, detail="Invalid password.")

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_cookie(),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=SESSION_TTL_DAYS * 24 * 60 * 60,
        path="/",
    )
    return {"enabled": True, "authenticated": True}


@app.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    db.execute(select(1))
    return HealthResponse()


@app.get("/library/summary", response_model=LibrarySummaryResponse)
def library_summary(db: Session = Depends(get_db)) -> LibrarySummaryResponse:
    series_count = db.scalar(select(func.count()).select_from(Series)) or 0
    issue_count = db.scalar(select(func.count()).select_from(Issue)) or 0
    archive_count = db.scalar(select(func.count()).select_from(Archive)) or 0
    reading_path_count = db.scalar(select(func.count()).select_from(ReadingPath)) or 0

    latest_series = db.scalars(
        select(Series)
        .options(selectinload(Series.issues).selectinload(Issue.archives))
        .order_by(Series.updated_at.desc(), Series.id.desc())
        .limit(5)
    ).all()
    latest_issues = db.scalars(
        select(Issue).options(selectinload(Issue.archives)).order_by(Issue.updated_at.desc(), Issue.id.desc()).limit(5)
    ).all()
    latest_reading_paths = db.scalars(
        select(ReadingPath)
        .options(
            selectinload(ReadingPath.event).selectinload(Event.publisher),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.canonical_issue),
            selectinload(ReadingPath.entries)
            .selectinload(ReadingPathEntry.canonical_issue)
            .selectinload(CanonicalIssue.series)
            .selectinload(CanonicalSeries.publisher),
            selectinload(ReadingPath.entries)
            .selectinload(ReadingPathEntry.canonical_issue)
            .selectinload(CanonicalIssue.issue_matches)
            .selectinload(IssueMatch.local_issue),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue),
        )
        .order_by(ReadingPath.updated_at.desc(), ReadingPath.id.desc())
        .limit(5)
    ).all()

    return LibrarySummaryResponse(
        series_count=series_count,
        issue_count=issue_count,
        archive_count=archive_count,
        reading_path_count=reading_path_count,
        latest_series=[_series_summary(item) for item in latest_series],
        latest_issues=[_issue_summary(item) for item in latest_issues],
        latest_reading_paths=[_reading_path_summary(item) for item in latest_reading_paths],
    )


@app.post("/library/open-downloads")
def open_downloads_folder() -> dict[str, str]:
    try:
        _open_path_in_file_manager(DOWNLOADS_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="No file manager command is available on this system.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to open downloads folder: {exc}") from exc
    return {"path": str(DOWNLOADS_ROOT.resolve())}


@app.get("/series", response_model=SeriesListResponse)
def list_series(
    db: Session = Depends(get_db),
    sort: SeriesSort = Query(default="title"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> SeriesListResponse:
    stmt = select(Series).options(selectinload(Series.issues).selectinload(Issue.archives))
    items = db.scalars(stmt).all()
    if sort == "latest_published_desc":
        items.sort(key=lambda item: _sort_key_latest_desc(_series_latest_published_on(item), item.title))
    elif sort == "latest_published_asc":
        items.sort(key=lambda item: _sort_key_latest_asc(_series_latest_published_on(item), item.title))
    else:
        items.sort(key=lambda item: (item.title.lower(), item.id))
    total = db.scalar(select(func.count()).select_from(Series)) or 0
    paged_items = items[offset : offset + limit]
    return SeriesListResponse(items=[_series_summary(item) for item in paged_items], total=total)


@app.get("/series/{series_id}", response_model=SeriesRead)
def get_series(series_id: int, db: Session = Depends(get_db)) -> SeriesRead:
    stmt = (
        select(Series)
        .options(
            selectinload(Series.issues).selectinload(Issue.archives),
            selectinload(Series.canonical_series),
        )
        .where(Series.id == series_id)
    )
    series = db.scalars(stmt).first()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series {series_id} not found")
    setattr(series, "latest_published_on", _series_latest_published_on(series))
    setattr(series, "reading_path_id", _series_reading_path_id(db, series))
    return SeriesRead.model_validate(series)


@app.delete("/series/{series_id}")
def delete_series(series_id: int, db: Session = Depends(get_db)) -> dict[str, int | bool]:
    series = db.scalars(
        select(Series)
        .options(selectinload(Series.issues).selectinload(Issue.archives))
        .where(Series.id == series_id)
    ).first()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series {series_id} not found")

    archives = [archive for issue in series.issues for archive in issue.archives]
    _delete_archive_files(archives)
    issue_count = len(series.issues)
    db.delete(series)
    db.commit()
    sync_catalog_data(db)
    return {"deleted": True, "series_id": series_id, "issue_count": issue_count}


@app.get("/issues", response_model=IssueListResponse)
def list_issues(
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    series_id: int | None = Query(default=None, ge=1),
) -> IssueListResponse:
    stmt = select(Issue).options(selectinload(Issue.archives))
    if series_id is not None:
        stmt = stmt.where(Issue.series_id == series_id)
    stmt = stmt.order_by(Issue.sort_order.asc(), Issue.id.asc()).offset(offset).limit(limit)
    items = db.scalars(stmt).all()

    count_stmt = select(func.count()).select_from(Issue)
    if series_id is not None:
        count_stmt = count_stmt.where(Issue.series_id == series_id)
    total = db.scalar(count_stmt) or 0

    return IssueListResponse(items=[_issue_summary(item) for item in items], total=total)


@app.get("/issues/{issue_id}", response_model=IssueRead)
def get_issue(issue_id: int, db: Session = Depends(get_db)) -> IssueRead:
    stmt = (
        select(Issue)
        .options(
            selectinload(Issue.archives),
            selectinload(Issue.series),
            selectinload(Issue.canonical_matches)
            .selectinload(IssueMatch.canonical_issue)
            .selectinload(CanonicalIssue.series),
        )
        .where(Issue.id == issue_id)
    )
    issue = db.scalars(stmt).first()
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Issue {issue_id} not found")
    if not issue.cover_url:
        issue.cover_url = _issue_cover_url(issue)
    setattr(
        issue,
        "reading_path_id",
        _series_reading_path_id(db, issue.series) if issue.series is not None else None,
    )
    primary_canonical_issue_id = _primary_canonical_issue_id(issue)
    setattr(issue, "primary_canonical_issue_id", primary_canonical_issue_id)
    issue_key = _issue_state_key(issue_id=issue.id, canonical_issue_id=primary_canonical_issue_id)
    state = db.scalar(select(UserIssueState).where(UserIssueState.issue_key == issue_key)) if issue_key is not None else None
    setattr(issue, "is_read", bool(state is not None and state.is_read))
    return IssueRead.model_validate(issue)


@app.put("/issues/{issue_id}/read-state", response_model=IssueStateRead)
def set_issue_read_state(issue_id: int, payload: IssueStateWrite, db: Session = Depends(get_db)) -> IssueStateRead:
    issue = db.scalars(
        select(Issue)
        .options(selectinload(Issue.canonical_matches))
        .where(Issue.id == issue_id)
    ).first()
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Issue {issue_id} not found")
    state = _upsert_issue_state(
        db,
        issue_id=issue.id,
        canonical_issue_id=_primary_canonical_issue_id(issue),
        read=payload.read,
        mark_opened=payload.mark_opened,
    )
    return IssueStateRead.model_validate(state)


@app.delete("/issues/{issue_id}")
def delete_issue(issue_id: int, db: Session = Depends(get_db)) -> dict[str, int | bool | None]:
    issue = db.scalars(
        select(Issue)
        .options(selectinload(Issue.archives), selectinload(Issue.series).selectinload(Series.issues))
        .where(Issue.id == issue_id)
    ).first()
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Issue {issue_id} not found")

    series = issue.series
    series_id = series.id if series is not None else None
    series_deleted = False
    if series is not None:
        remaining_issues = db.scalar(select(func.count()).select_from(Issue).where(Issue.series_id == series.id)) or 0
        if remaining_issues <= 1:
            archives = [archive for item in series.issues for archive in item.archives]
            _delete_archive_files(archives)
            db.delete(series)
            series_deleted = True
        else:
            _delete_archive_files(list(issue.archives))
            db.delete(issue)
    else:
        _delete_archive_files(list(issue.archives))
        db.delete(issue)

    db.commit()
    sync_catalog_data(db)
    return {
        "deleted": True,
        "issue_id": issue_id,
        "series_id": series_id,
        "series_deleted": series_deleted,
    }


@app.get("/archives/{archive_id}/pages", response_model=ArchivePageListResponse)
def get_archive_pages(archive_id: int, db: Session = Depends(get_db)) -> ArchivePageListResponse:
    archive = db.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(status_code=404, detail=f"Archive {archive_id} not found")

    try:
        pages = list_archive_pages(archive)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return ArchivePageListResponse(
        archive_id=archive.id,
        pages=[
            ArchivePageRead(
                index=page.index,
                relative_path=page.relative_path,
                media_type=page.media_type,
                image_url=f"/archives/{archive.id}/pages/{page.index}",
            )
            for page in pages
        ],
    )


@app.get("/archives/{archive_id}/pages/{page_number}")
def get_archive_page_image(
    archive_id: int,
    page_number: int,
    db: Session = Depends(get_db),
) -> Response:
    archive = db.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(status_code=404, detail=f"Archive {archive_id} not found")

    try:
        content, media_type, filename = archive_page_bytes(archive, page_number)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    headers = {"Content-Disposition": f'inline; filename="{filename}"'}
    return Response(content=content, media_type=media_type, headers=headers)


@app.get("/publishers", response_model=PublisherListResponse)
def list_publishers(
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> PublisherListResponse:
    stmt = select(Publisher).order_by(Publisher.name.asc(), Publisher.id.asc()).offset(offset).limit(limit)
    items = db.scalars(stmt).all()
    total = db.scalar(select(func.count()).select_from(Publisher)) or 0
    return PublisherListResponse(items=[PublisherSummary.model_validate(item) for item in items], total=total)


@app.get("/publishers/{publisher_id}", response_model=PublisherRead)
def get_publisher(publisher_id: int, db: Session = Depends(get_db)) -> PublisherRead:
    publisher = db.get(Publisher, publisher_id)
    if publisher is None:
        raise HTTPException(status_code=404, detail=f"Publisher {publisher_id} not found")
    return PublisherRead.model_validate(publisher)


@app.get("/events", response_model=EventListResponse)
def list_events(
    db: Session = Depends(get_db),
    publisher_id: int | None = Query(default=None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> EventListResponse:
    stmt = select(Event)
    if publisher_id is not None:
        stmt = stmt.where(Event.publisher_id == publisher_id)
    stmt = stmt.order_by(Event.start_year.is_(None), Event.start_year.asc(), Event.title.asc(), Event.id.asc()).offset(offset).limit(limit)
    items = db.scalars(stmt).all()

    count_stmt = select(func.count()).select_from(Event)
    if publisher_id is not None:
        count_stmt = count_stmt.where(Event.publisher_id == publisher_id)
    total = db.scalar(count_stmt) or 0
    return EventListResponse(items=[EventSummary.model_validate(item) for item in items], total=total)


@app.get("/events/{event_id}", response_model=EventRead)
def get_event(event_id: int, db: Session = Depends(get_db)) -> EventRead:
    stmt = (
        select(Event)
        .options(
            selectinload(Event.publisher),
            selectinload(Event.story_arcs),
            selectinload(Event.reading_paths).selectinload(ReadingPath.event).selectinload(Event.publisher),
            selectinload(Event.reading_paths)
            .selectinload(ReadingPath.entries)
            .selectinload(ReadingPathEntry.canonical_issue),
            selectinload(Event.reading_paths)
            .selectinload(ReadingPath.entries)
            .selectinload(ReadingPathEntry.canonical_issue)
            .selectinload(CanonicalIssue.series)
            .selectinload(CanonicalSeries.publisher),
            selectinload(Event.reading_paths).selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue),
        )
        .where(Event.id == event_id)
    )
    event = db.scalars(stmt).first()
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    response = EventRead.model_validate(event)
    response.reading_paths = sorted(
        [_reading_path_summary(item) for item in event.reading_paths],
        key=lambda item: _sort_key_latest_desc(item.latest_published_on, item.title),
    )
    return response


@app.get("/story-arcs", response_model=StoryArcListResponse)
def list_story_arcs(
    db: Session = Depends(get_db),
    event_id: int | None = Query(default=None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> StoryArcListResponse:
    stmt = select(StoryArc)
    if event_id is not None:
        stmt = stmt.where(StoryArc.event_id == event_id)
    stmt = stmt.order_by(StoryArc.title.asc(), StoryArc.id.asc()).offset(offset).limit(limit)
    items = db.scalars(stmt).all()

    count_stmt = select(func.count()).select_from(StoryArc)
    if event_id is not None:
        count_stmt = count_stmt.where(StoryArc.event_id == event_id)
    total = db.scalar(count_stmt) or 0
    return StoryArcListResponse(items=[StoryArcSummary.model_validate(item) for item in items], total=total)


@app.get("/story-arcs/{story_arc_id}", response_model=StoryArcRead)
def get_story_arc(story_arc_id: int, db: Session = Depends(get_db)) -> StoryArcRead:
    stmt = (
        select(StoryArc)
        .options(
            selectinload(StoryArc.event),
            selectinload(StoryArc.reading_path_entries).selectinload(ReadingPathEntry.canonical_issue),
            selectinload(StoryArc.reading_path_entries).selectinload(ReadingPathEntry.canonical_series),
            selectinload(StoryArc.reading_path_entries).selectinload(ReadingPathEntry.issue),
        )
        .where(StoryArc.id == story_arc_id)
    )
    story_arc = db.scalars(stmt).first()
    if story_arc is None:
        raise HTTPException(status_code=404, detail=f"Story arc {story_arc_id} not found")
    return StoryArcRead.model_validate(story_arc)


@app.get("/canonical-series", response_model=CanonicalSeriesListResponse)
def list_canonical_series(
    db: Session = Depends(get_db),
    publisher_id: int | None = Query(default=None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CanonicalSeriesListResponse:
    stmt = select(CanonicalSeries)
    if publisher_id is not None:
        stmt = stmt.where(CanonicalSeries.publisher_id == publisher_id)
    stmt = stmt.order_by(CanonicalSeries.title.asc(), CanonicalSeries.id.asc()).offset(offset).limit(limit)
    items = db.scalars(stmt).all()

    count_stmt = select(func.count()).select_from(CanonicalSeries)
    if publisher_id is not None:
        count_stmt = count_stmt.where(CanonicalSeries.publisher_id == publisher_id)
    total = db.scalar(count_stmt) or 0
    return CanonicalSeriesListResponse(
        items=[CanonicalSeriesSummary.model_validate(item) for item in items],
        total=total,
    )


@app.get("/canonical-series/{series_id}", response_model=CanonicalSeriesRead)
def get_canonical_series(series_id: int, db: Session = Depends(get_db)) -> CanonicalSeriesRead:
    stmt = (
        select(CanonicalSeries)
        .options(selectinload(CanonicalSeries.publisher), selectinload(CanonicalSeries.issues))
        .where(CanonicalSeries.id == series_id)
    )
    series = db.scalars(stmt).first()
    if series is None:
        raise HTTPException(status_code=404, detail=f"Canonical series {series_id} not found")
    return CanonicalSeriesRead.model_validate(series)


@app.get("/canonical-issues", response_model=CanonicalIssueListResponse)
def list_canonical_issues(
    db: Session = Depends(get_db),
    series_id: int | None = Query(default=None, ge=1),
    event_id: int | None = Query(default=None, ge=1),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CanonicalIssueListResponse:
    stmt = select(CanonicalIssue)
    if series_id is not None:
        stmt = stmt.where(CanonicalIssue.series_id == series_id)
    if event_id is not None:
        stmt = stmt.where(CanonicalIssue.event_id == event_id)
    stmt = stmt.order_by(CanonicalIssue.sort_order.asc(), CanonicalIssue.id.asc()).offset(offset).limit(limit)
    items = db.scalars(stmt).all()

    count_stmt = select(func.count()).select_from(CanonicalIssue)
    if series_id is not None:
        count_stmt = count_stmt.where(CanonicalIssue.series_id == series_id)
    if event_id is not None:
        count_stmt = count_stmt.where(CanonicalIssue.event_id == event_id)
    total = db.scalar(count_stmt) or 0
    return CanonicalIssueListResponse(
        items=[CanonicalIssueSummary.model_validate(item) for item in items],
        total=total,
    )


@app.get("/canonical-issues/{issue_id}", response_model=CanonicalIssueRead)
def get_canonical_issue(issue_id: int, db: Session = Depends(get_db)) -> CanonicalIssueRead:
    stmt = (
        select(CanonicalIssue)
        .options(
            selectinload(CanonicalIssue.series),
            selectinload(CanonicalIssue.event),
            selectinload(CanonicalIssue.issue_matches).selectinload(IssueMatch.local_issue),
        )
        .where(CanonicalIssue.id == issue_id)
    )
    issue = db.scalars(stmt).first()
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Canonical issue {issue_id} not found")
    response = CanonicalIssueRead.model_validate(issue)
    response.cover_url = _canonical_issue_cover_url(issue)
    response.page_count = issue.page_count
    response.pages = _canonical_issue_pages(issue)
    issue_key = _issue_state_key(canonical_issue_id=issue.id)
    state = db.scalar(select(UserIssueState).where(UserIssueState.issue_key == issue_key)) if issue_key is not None else None
    response.is_read = bool(state is not None and state.is_read)
    return response


@app.get("/canonical-issues/{issue_id}/pages/{page_index}")
def get_canonical_issue_page_image(issue_id: int, page_index: int, db: Session = Depends(get_db)) -> FileResponse:
    issue = db.get(CanonicalIssue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Canonical issue {issue_id} not found")
    if issue.provider_name != "MangaPill" or not issue.provider_url:
        raise HTTPException(status_code=404, detail="This issue does not expose provider-backed pages.")

    pages = fetch_mangapill_chapter_pages(issue.provider_url)
    if page_index < 1 or page_index > len(pages):
        raise HTTPException(status_code=404, detail=f"Page {page_index} not found")

    image_url = pages[page_index - 1]
    cached_path, content_type = ensure_remote_cover_image(
        cache_key=f"canonical-issue-{issue_id}-page-{page_index}",
        image_url=image_url,
        referer_url=issue.provider_url,
    )
    if cached_path is None or not cached_path.exists():
        raise HTTPException(status_code=404, detail="Page image unavailable")
    return FileResponse(cached_path, media_type=content_type, filename=cached_path.name)


@app.put("/canonical-issues/{issue_id}/read-state", response_model=IssueStateRead)
def set_canonical_issue_read_state(issue_id: int, payload: IssueStateWrite, db: Session = Depends(get_db)) -> IssueStateRead:
    issue = db.get(CanonicalIssue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail=f"Canonical issue {issue_id} not found")
    state = _upsert_issue_state(
        db,
        canonical_issue_id=issue.id,
        read=payload.read,
        mark_opened=payload.mark_opened,
    )
    return IssueStateRead.model_validate(state)


@app.get("/reading-paths", response_model=ReadingPathListResponse)
def list_reading_paths(
    db: Session = Depends(get_db),
    event_id: int | None = Query(default=None, ge=1),
    sort: ReadingPathSort = Query(default="latest_published_desc"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> ReadingPathListResponse:
    stmt = select(ReadingPath).options(
        selectinload(ReadingPath.event).selectinload(Event.publisher),
        selectinload(ReadingPath.catalog_collection).selectinload(CatalogCollection.tags),
        selectinload(ReadingPath.catalog_collection).selectinload(CatalogCollection.items),
        selectinload(ReadingPath.catalog_collection)
        .selectinload(CatalogCollection.continuity_group)
        .selectinload(ContinuityGroup.collections),
        selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.canonical_issue),
        selectinload(ReadingPath.entries)
        .selectinload(ReadingPathEntry.canonical_issue)
        .selectinload(CanonicalIssue.series)
        .selectinload(CanonicalSeries.publisher),
        selectinload(ReadingPath.entries)
        .selectinload(ReadingPathEntry.canonical_issue)
        .selectinload(CanonicalIssue.issue_matches)
        .selectinload(IssueMatch.local_issue),
        selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue),
    )
    if event_id is not None:
        stmt = stmt.where(ReadingPath.event_id == event_id)
    items = db.scalars(stmt).all()
    canonical_issue_ids = {
        catalog_item.canonical_issue_id
        for item in items
        if item.catalog_collection is not None
        for catalog_item in item.catalog_collection.items
        if catalog_item.canonical_issue_id is not None
    }
    issue_ids = {
        catalog_item.issue_id
        for item in items
        if item.catalog_collection is not None
        for catalog_item in item.catalog_collection.items
        if catalog_item.issue_id is not None
    }
    state_map = _read_state_map(db, canonical_issue_ids=canonical_issue_ids, issue_ids=issue_ids)
    summaries = [
        _reading_path_summary(item, collection=item.catalog_collection, state_map=state_map)
        for item in items
    ]
    if sort == "latest_published_desc":
        summaries.sort(key=lambda item: _sort_key_latest_desc(item.latest_published_on, item.title))
    elif sort == "latest_published_asc":
        summaries.sort(key=lambda item: _sort_key_latest_asc(item.latest_published_on, item.title))
    else:
        summaries.sort(key=lambda item: (_natural_text_sort_key(item.title), item.id))

    count_stmt = select(func.count()).select_from(ReadingPath)
    if event_id is not None:
        count_stmt = count_stmt.where(ReadingPath.event_id == event_id)
    total = db.scalar(count_stmt) or 0
    return ReadingPathListResponse(items=summaries[offset : offset + limit], total=total)


@app.get("/reading-paths/covers", response_model=ReadingPathCoverBatchResponse)
def get_reading_path_covers(
    ids: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
) -> ReadingPathCoverBatchResponse:
    requested_ids: list[int] = []
    seen_ids: set[int] = set()
    for raw_id in ids.split(","):
        value = raw_id.strip()
        if not value:
            continue
        try:
            parsed_id = int(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid reading path id: {value}") from exc
        if parsed_id in seen_ids:
            continue
        seen_ids.add(parsed_id)
        requested_ids.append(parsed_id)

    if not requested_ids:
        return ReadingPathCoverBatchResponse(items=[])

    reading_paths = db.scalars(
        select(ReadingPath)
        .options(
            selectinload(ReadingPath.cover_asset),
            selectinload(ReadingPath.entries)
            .selectinload(ReadingPathEntry.canonical_issue)
            .selectinload(CanonicalIssue.series),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue).selectinload(Issue.series),
        )
        .where(ReadingPath.id.in_(requested_ids))
    ).all()
    path_by_id = {reading_path.id: reading_path for reading_path in reading_paths}

    items: list[ReadingPathCoverRead] = []
    for reading_path_id in requested_ids:
        reading_path = path_by_id.get(reading_path_id)
        if reading_path is None:
            items.append(ReadingPathCoverRead(reading_path_id=reading_path_id))
            continue
        fallback_query = _reading_path_cover_query(reading_path)
        try:
            provider_cover_url = _reading_path_provider_cover_url(reading_path)
            if provider_cover_url:
                items.append(
                    ReadingPathCoverRead(
                        reading_path_id=reading_path_id,
                        image_url=f"/reading-paths/{reading_path_id}/cover-image",
                        query=fallback_query,
                    )
                )
                continue
            query, expected_series_title, expected_issue_number, expected_year = _reading_path_download_context(reading_path)
            asset = ensure_reading_path_cover_asset(
                db,
                reading_path_id=reading_path.id,
                query=query,
                expected_series_title=expected_series_title,
                expected_issue_number=expected_issue_number,
                expected_year=expected_year,
            )
            items.append(
                ReadingPathCoverRead(
                    reading_path_id=reading_path_id,
                    image_url=f"/reading-paths/{reading_path_id}/cover-image" if asset.status == "ready" and asset.cached_path else None,
                    post_url=asset.post_url,
                    post_title=asset.post_title,
                    query=asset.query,
                )
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to resolve reading path cover for collection %s", reading_path_id)
            items.append(ReadingPathCoverRead(reading_path_id=reading_path_id, query=fallback_query))
    db.commit()
    return ReadingPathCoverBatchResponse(items=items)


@app.get("/reading-paths/{reading_path_id}/cover-image")
def get_reading_path_cover_image(reading_path_id: int, db: Session = Depends(get_db)) -> FileResponse:
    reading_path = db.scalars(
        select(ReadingPath)
        .options(
            selectinload(ReadingPath.cover_asset),
            selectinload(ReadingPath.entries)
            .selectinload(ReadingPathEntry.canonical_issue)
            .selectinload(CanonicalIssue.series),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue).selectinload(Issue.series),
        )
        .where(ReadingPath.id == reading_path_id)
    ).first()
    if reading_path is None:
        raise HTTPException(status_code=404, detail=f"Reading path {reading_path_id} not found")

    provider_cover_url = _reading_path_provider_cover_url(reading_path)
    if provider_cover_url:
        cached_path, content_type = ensure_remote_cover_image(
            cache_key=f"reading-path-{reading_path_id}-provider-cover",
            image_url=provider_cover_url,
            referer_url=_provider_cover_referer(provider_cover_url, reading_path.source_url),
        )
        if cached_path is None or not cached_path.exists():
            raise HTTPException(status_code=404, detail="Cover image unavailable")
        return FileResponse(cached_path, media_type=content_type, filename=cached_path.name)

    query, expected_series_title, expected_issue_number, expected_year = _reading_path_download_context(reading_path)
    try:
        asset = ensure_reading_path_cover_asset(
            db,
            reading_path_id=reading_path.id,
            query=query,
            expected_series_title=expected_series_title,
            expected_issue_number=expected_issue_number,
            expected_year=expected_year,
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to resolve cached cover image for collection %s", reading_path_id)
        raise HTTPException(status_code=404, detail="Cover image unavailable") from exc

    if asset.status != "ready" or not asset.cached_path:
        raise HTTPException(status_code=404, detail="Cover image unavailable")

    cached_path = Path(asset.cached_path)
    if not cached_path.exists():
        raise HTTPException(status_code=404, detail="Cached cover image missing")

    db.commit()
    return FileResponse(cached_path, media_type=asset.content_type or "image/jpeg")


@app.post("/reading-paths/{reading_path_id}/download", response_model=ReadingPathDownloadResponse)
def download_reading_path_issue(reading_path_id: int, db: Session = Depends(get_db)) -> ReadingPathDownloadResponse:
    reading_path = db.scalars(
        select(ReadingPath)
        .options(
            selectinload(ReadingPath.cover_asset),
            selectinload(ReadingPath.entries)
            .selectinload(ReadingPathEntry.canonical_issue)
            .selectinload(CanonicalIssue.series),
            selectinload(ReadingPath.entries)
            .selectinload(ReadingPathEntry.canonical_issue)
            .selectinload(CanonicalIssue.issue_matches)
            .selectinload(IssueMatch.local_issue)
            .selectinload(Issue.archives),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue).selectinload(Issue.series),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue).selectinload(Issue.archives),
        )
        .where(ReadingPath.id == reading_path_id)
    ).first()
    if reading_path is None:
        raise HTTPException(status_code=404, detail=f"Reading path {reading_path_id} not found")
    issue_entries = [entry for entry in reading_path.entries if entry.entry_type == "issue"]
    missing_entries = [entry for entry in issue_entries if not _entry_has_streamable_local_match(entry)]
    if not missing_entries:
        return ReadingPathDownloadResponse(
            reading_path_id=reading_path_id,
            downloaded_issue_count=0,
            skipped_issue_count=len(issue_entries),
        )

    imported_paths: list[str] = []
    result = PersistResult()
    downloaded_issue_count = 0
    skipped_issue_count = len(issue_entries) - len(missing_entries)

    for entry in missing_entries:
        if entry.canonical_issue is not None and entry.canonical_issue.provider_name == "MangaPill":
            item_paths, item_result = _download_provider_issue_to_library(db, entry.canonical_issue)
            imported_paths.extend(item_paths)
            result = result.merge(item_result)
            downloaded_issue_count += 1
            continue
        query, expected_series_title, expected_issue_number, expected_year = _reading_path_entry_download_context(entry)
        cover = fetch_getcomics_cover(
            query,
            expected_series_title=expected_series_title,
            expected_issue_number=expected_issue_number,
            expected_year=expected_year,
        )
        if not cover.post_url:
            skipped_issue_count += 1
            continue
        item_paths, item_result = _download_post_to_library(db, cover.post_url)
        imported_paths.extend(item_paths)
        result = result.merge(item_result)
        downloaded_issue_count += 1

    return ReadingPathDownloadResponse(
        reading_path_id=reading_path_id,
        imported_paths=imported_paths,
        downloaded_issue_count=downloaded_issue_count,
        skipped_issue_count=skipped_issue_count,
        series_created=result.series_created,
        series_updated=result.series_updated,
        issues_created=result.issues_created,
        issues_updated=result.issues_updated,
        archives_created=result.archives_created,
        archives_updated=result.archives_updated,
    )


@app.post("/reading-paths/{reading_path_id}/entries/{entry_id}/download", response_model=ReadingPathDownloadResponse)
def download_reading_path_entry(reading_path_id: int, entry_id: int, db: Session = Depends(get_db)) -> ReadingPathDownloadResponse:
    reading_path = db.scalars(
        select(ReadingPath)
        .options(
            selectinload(ReadingPath.entries)
            .selectinload(ReadingPathEntry.canonical_issue)
            .selectinload(CanonicalIssue.series),
            selectinload(ReadingPath.entries)
            .selectinload(ReadingPathEntry.canonical_issue)
            .selectinload(CanonicalIssue.issue_matches)
            .selectinload(IssueMatch.local_issue)
            .selectinload(Issue.archives),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue).selectinload(Issue.series),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue).selectinload(Issue.archives),
        )
        .where(ReadingPath.id == reading_path_id)
    ).first()
    if reading_path is None:
        raise HTTPException(status_code=404, detail=f"Reading path {reading_path_id} not found")

    entry = next((item for item in reading_path.entries if item.id == entry_id), None)
    if entry is None or entry.entry_type != "issue":
        raise HTTPException(status_code=404, detail=f"Reading path entry {entry_id} not found")
    if _entry_has_streamable_local_match(entry):
        return ReadingPathDownloadResponse(
            reading_path_id=reading_path_id,
            entry_id=entry_id,
            downloaded_issue_count=0,
            skipped_issue_count=1,
        )

    if entry.canonical_issue is not None and entry.canonical_issue.provider_name == "MangaPill":
        imported_paths, result = _download_provider_issue_to_library(db, entry.canonical_issue)
        return ReadingPathDownloadResponse(
            reading_path_id=reading_path_id,
            entry_id=entry_id,
            imported_paths=imported_paths,
            downloaded_issue_count=1,
            skipped_issue_count=0,
            series_created=result.series_created,
            series_updated=result.series_updated,
            issues_created=result.issues_created,
            issues_updated=result.issues_updated,
            archives_created=result.archives_created,
            archives_updated=result.archives_updated,
        )

    query, expected_series_title, expected_issue_number, expected_year = _reading_path_entry_download_context(entry)
    cover = fetch_getcomics_cover(
        query,
        expected_series_title=expected_series_title,
        expected_issue_number=expected_issue_number,
        expected_year=expected_year,
    )
    if not cover.post_url:
        raise HTTPException(status_code=502, detail="No downloadable GetComics post was resolved for this issue.")

    imported_paths, result = _download_post_to_library(db, cover.post_url)
    return ReadingPathDownloadResponse(
        reading_path_id=reading_path_id,
        entry_id=entry_id,
        post_url=cover.post_url,
        imported_paths=imported_paths,
        downloaded_issue_count=1,
        skipped_issue_count=0,
        series_created=result.series_created,
        series_updated=result.series_updated,
        issues_created=result.issues_created,
        issues_updated=result.issues_updated,
        archives_created=result.archives_created,
        archives_updated=result.archives_updated,
    )


@app.get("/reading-paths/{reading_path_id}", response_model=ReadingPathRead)
def get_reading_path(reading_path_id: int, db: Session = Depends(get_db)) -> ReadingPathRead:
    stmt = (
        select(ReadingPath)
        .options(
            selectinload(ReadingPath.event),
            selectinload(ReadingPath.event).selectinload(Event.publisher),
            selectinload(ReadingPath.catalog_collection).selectinload(CatalogCollection.tags),
            selectinload(ReadingPath.catalog_collection).selectinload(CatalogCollection.items),
            selectinload(ReadingPath.catalog_collection)
            .selectinload(CatalogCollection.continuity_group)
            .selectinload(ContinuityGroup.collections),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue).selectinload(Issue.archives),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.series),
            selectinload(ReadingPath.entries)
            .selectinload(ReadingPathEntry.canonical_issue)
            .options(
                selectinload(CanonicalIssue.series),
                selectinload(CanonicalIssue.series).selectinload(CanonicalSeries.publisher),
                selectinload(CanonicalIssue.issue_matches)
                .selectinload(IssueMatch.local_issue)
                .selectinload(Issue.archives),
            ),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.canonical_series),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.story_arc),
        )
        .where(ReadingPath.id == reading_path_id)
    )
    reading_path = db.scalars(stmt).first()
    if reading_path is None:
        raise HTTPException(status_code=404, detail=f"Reading path {reading_path_id} not found")
    response = ReadingPathRead.model_validate(reading_path)
    collection = reading_path.catalog_collection
    canonical_issue_ids = (
        {item.canonical_issue_id for item in collection.items if item.canonical_issue_id is not None}
        if collection is not None
        else set()
    )
    issue_ids = (
        {item.issue_id for item in collection.items if item.issue_id is not None}
        if collection is not None
        else set()
    )
    state_map = _read_state_map(db, canonical_issue_ids=canonical_issue_ids, issue_ids=issue_ids)
    summary = _reading_path_summary(reading_path, collection=collection, state_map=state_map)
    response.issue_count = summary.issue_count
    response.series_count = summary.series_count
    response.latest_issue_label = summary.latest_issue_label
    response.first_published_on = summary.first_published_on
    response.latest_published_on = summary.latest_published_on
    response.unread_count = summary.unread_count
    response.is_complete = summary.is_complete
    response.last_read_at = summary.last_read_at
    response.continuity_group_id = summary.continuity_group_id
    response.previous_collection_id = summary.previous_collection_id
    response.next_collection_id = summary.next_collection_id
    response.tags = summary.tags
    response.access_mode = summary.access_mode
    for orm_entry, response_entry in zip(reading_path.entries, response.entries, strict=False):
        if (
            orm_entry.canonical_issue is not None
            and orm_entry.canonical_issue.provider_name
            and _canonical_issue_cover_url(orm_entry.canonical_issue)
        ):
            response_entry.cover_url = f"/reading-paths/{reading_path.id}/entries/{orm_entry.id}/cover-image"
        elif orm_entry.canonical_issue is not None and _canonical_issue_cover_url(orm_entry.canonical_issue):
            response_entry.cover_url = _canonical_issue_cover_url(orm_entry.canonical_issue)
        else:
            response_entry.cover_url = f"/reading-paths/{reading_path.id}/entries/{orm_entry.id}/cover-image"
        response_entry.issue_key = _issue_state_key(issue_id=orm_entry.issue_id, canonical_issue_id=orm_entry.canonical_issue_id)
        state = state_map.get(response_entry.issue_key) if response_entry.issue_key is not None else None
        response_entry.is_read = bool(state is not None and state.is_read)
        streamable_issue = _entry_streamable_local_issue(orm_entry)
        if streamable_issue is not None:
            response_entry.matched_issue = _issue_summary(streamable_issue)
    return response


@app.put("/reading-paths/{reading_path_id}/entries/{entry_id}/read-state", response_model=IssueStateRead)
def set_reading_path_entry_state(
    reading_path_id: int,
    entry_id: int,
    payload: IssueStateWrite,
    db: Session = Depends(get_db),
) -> IssueStateRead:
    entry = db.scalars(
        select(ReadingPathEntry)
        .options(selectinload(ReadingPathEntry.canonical_issue), selectinload(ReadingPathEntry.issue))
        .where(ReadingPathEntry.id == entry_id, ReadingPathEntry.reading_path_id == reading_path_id)
    ).first()
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Reading path entry {entry_id} not found")
    state = _upsert_issue_state(
        db,
        issue_id=entry.issue_id,
        canonical_issue_id=entry.canonical_issue_id,
        read=payload.read,
        mark_opened=payload.mark_opened,
    )
    return IssueStateRead.model_validate(state)


@app.get("/reading-paths/{reading_path_id}/entries/{entry_id}/cover-image")
def get_reading_path_entry_cover_image(
    reading_path_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
) -> FileResponse:
    entry = db.scalars(
        select(ReadingPathEntry)
        .options(
            selectinload(ReadingPathEntry.reading_path),
            selectinload(ReadingPathEntry.canonical_issue).selectinload(CanonicalIssue.series),
            selectinload(ReadingPathEntry.issue).selectinload(Issue.series),
        )
        .where(ReadingPathEntry.id == entry_id, ReadingPathEntry.reading_path_id == reading_path_id)
    ).first()
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Reading path entry {entry_id} not found")

    if entry.canonical_issue is not None and entry.canonical_issue.provider_name and _canonical_issue_cover_url(entry.canonical_issue):
        image_url = _provider_issue_cover_url(entry.canonical_issue) or _canonical_issue_cover_url(entry.canonical_issue)
        referer_source = entry.reading_path.source_url if entry.reading_path is not None else entry.canonical_issue.provider_url
        cached_path, content_type = ensure_remote_cover_image(
            cache_key=f"reading-path-{reading_path_id}-entry-{entry_id}-provider-cover",
            image_url=image_url,
            referer_url=_provider_cover_referer(image_url, referer_source),
        )
        if cached_path is None or not cached_path.exists():
            raise HTTPException(status_code=404, detail="Cover image unavailable")
        return FileResponse(cached_path, media_type=content_type, filename=cached_path.name)

    query, expected_series_title, expected_issue_number, expected_year = _reading_path_entry_cover_context(entry)
    try:
        cached_path, content_type, cover = ensure_query_cover_image(
            cache_key=f"reading-path-{reading_path_id}-entry-{entry_id}",
            query=query,
            expected_series_title=expected_series_title,
            expected_issue_number=expected_issue_number,
            expected_year=expected_year,
        )
    except Exception as exc:
        logger.exception(
            "Failed to resolve cached cover image for collection %s entry %s",
            reading_path_id,
            entry_id,
        )
        raise HTTPException(status_code=404, detail="Cover image unavailable") from exc
    if cached_path is None or not cached_path.exists():
        raise HTTPException(status_code=404, detail=f"Cover image unavailable for query: {cover.query}")

    return FileResponse(cached_path, media_type=content_type, filename=cached_path.name)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Comic Library API", "docs": "/docs", "health": "/health"}


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
