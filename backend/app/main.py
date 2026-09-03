from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
import errno
import fcntl
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
import mimetypes
import requests
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload
import comics

from .db import SessionLocal, engine, ensure_runtime_schema, get_db
from .auth import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_DAYS,
    auth_enabled,
    create_session_cookie,
    verify_password,
    OPDS_TOKEN_PARAM,
    opds_access_token,
    verify_basic_auth,
    verify_opds_token,
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
    ReadingList,
    ReadingListItem,
    ReadingPathEntry,
    Series,
    StoryArc,
    UserIssueState,
)
from .routers import ingest_router
from .schemas import (
    AppSettingsRead,
    AppSettingsWrite,
    ArchivePageListResponse,
    ArchivePageRead,
    CanonicalIssueListResponse,
    CanonicalIssueRead,
    CanonicalIssueSummary,
    CanonicalSeriesListResponse,
    CanonicalSeriesRead,
    CanonicalSeriesSummary,
    CatalogCollectionListResponse,
    CatalogCollectionSummary,
    CatalogFacetRead,
    CatalogFacetsResponse,
    ChronologyEntryRead,
    ChronologyResponse,
    DestinationSpaceRead,
    DownloadEstimateResponse,
    DownloadEstimateWrite,
    DownloadQueueItemRead,
    DownloadQueueRead,
    DownloadStartWrite,
    DownloadTargetRead,
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
    ReaderIssueRead,
    ReadingPathCoverBatchResponse,
    ReadingPathCoverRead,
    ReadingPathDownloadResponse,
    ReadingPathListResponse,
    ReadingPathRead,
    ReadingListItemRead,
    ReadingListItemsWrite,
    ReadingListListResponse,
    ReadingListRead,
    ReadingListSummary,
    ReadingListWrite,
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
from .services import opds
from .services.catalog_query import (
    catalog_chronology,
    catalog_collections,
    catalog_facets,
    catalog_publishers,
    catalog_series,
    owned_counts,
)
from .services.downloads_queue import QUEUE, DownloadTarget, QueueItem, destination_space, resolve_targets
from .services.ingest import ComicMetadata, PageRecord, ScanResult
from .services.stream_buffer import (
    StreamBufferTooLargeError,
    find_stream_archive,
    store_stream_archive,
    stream_buffer_key,
    stream_buffer_max_bytes,
)

SeriesSort = Literal["title", "latest_published_desc", "latest_published_asc"]
ReadingPathSort = Literal["title", "latest_published_desc", "latest_published_asc"]
REPO_ROOT = Path(__file__).resolve().parents[2]
APP_SETTINGS_PATH = REPO_ROOT / "backend" / "data" / "app_settings.json"
logger = logging.getLogger(__name__)
_INITIALIZATION_LOCK = threading.Lock()
_INITIALIZED = False
SQLITE_IN_CLAUSE_CHUNK_SIZE = 500


def _hosted_deployment() -> bool:
    return os.getenv("PANELSTACK_HOSTED_DEPLOYMENT") == "1"


def _remote_cover_fetch_enabled() -> bool:
    return os.getenv("PANELSTACK_ENABLE_REMOTE_COVER_FETCH", "1") != "0"


def _default_download_root() -> Path:
    return (Path.home() / "Documents" / "COMICS").expanduser().resolve()


def _normalize_download_root(value: str) -> Path:
    raw_value = value.strip()
    if not raw_value:
        raise HTTPException(status_code=422, detail="Download folder is required.")
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _read_app_settings_payload() -> dict[str, str]:
    if not APP_SETTINGS_PATH.exists():
        return {}
    try:
        payload = json.loads(APP_SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Ignoring invalid app settings file at %s", APP_SETTINGS_PATH)
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_app_settings_payload(payload: dict[str, str]) -> None:
    APP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = APP_SETTINGS_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary_path.replace(APP_SETTINGS_PATH)


def _downloads_root() -> Path:
    configured = _read_app_settings_payload().get("download_root")
    if isinstance(configured, str) and configured.strip():
        return _normalize_download_root(configured)
    return _default_download_root()


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
    if issue.provider_name == "MangaPill" and issue.provider_url and _remote_cover_fetch_enabled():
        return _mangapill_first_page_image(issue.provider_url) or _canonical_issue_cover_url(issue)
    return _canonical_issue_cover_url(issue)


def _canonical_issue_pages(issue: CanonicalIssue) -> list[ArchivePageRead]:
    if issue.provider_name != "MangaPill" or not issue.provider_url:
        return []
    try:
        page_urls = fetch_mangapill_chapter_pages(issue.provider_url)
    except Exception:
        logger.exception("Failed to resolve MangaPill pages for canonical issue %s", issue.id)
        return []
    return [
        ArchivePageRead(
            index=index,
            relative_path=f"page-{index}",
            media_type="image/jpeg",
            image_url=f"/canonical-issues/{issue.id}/pages/{index}",
        )
        for index, image_url in enumerate(page_urls, start=1)
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

    states: list[UserIssueState] = []
    for id_batch in _chunked_ids(canonical_issue_ids):
        states.extend(
            db.scalars(select(UserIssueState).where(UserIssueState.canonical_issue_id.in_(id_batch))).all()
        )
    for id_batch in _chunked_ids(issue_ids):
        states.extend(db.scalars(select(UserIssueState).where(UserIssueState.issue_id.in_(id_batch))).all())
    return {state.issue_key: state for state in states}


def _chunked_ids(values: set[int], chunk_size: int = SQLITE_IN_CLAUSE_CHUNK_SIZE) -> list[list[int]]:
    ordered_values = sorted(values)
    return [ordered_values[index : index + chunk_size] for index in range(0, len(ordered_values), chunk_size)]


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


def _reading_path_first_issue_entry(reading_path: ReadingPath) -> ReadingPathEntry | None:
    issue_entries = [entry for entry in reading_path.entries if entry.entry_type == "issue"]
    if not issue_entries:
        return None
    return min(issue_entries, key=lambda entry: (entry.sort_order, entry.id))


def _reading_path_collected_edition_entry(reading_path: ReadingPath) -> ReadingPathEntry | None:
    collection_entries = [entry for entry in reading_path.entries if entry.entry_type == "collection"]
    if not collection_entries:
        return None
    return max(collection_entries, key=lambda entry: (entry.sort_order, entry.id))


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
    collection_entry = _reading_path_collected_edition_entry(reading_path)
    collection_label = _reading_path_issue_label(collection_entry)
    if collection_label:
        return collection_label
    # The first issue of a run always exists once it has launched; the last
    # curated issue may still be months from publication.
    first_issue_label = _reading_path_issue_label(_reading_path_first_issue_entry(reading_path))
    return first_issue_label or reading_path.title


def _expected_getcomics_title(issue: CanonicalIssue | Issue) -> str:
    if issue.issue_kind == "collection" and issue.title:
        return issue.title
    return issue.series.title


def _reading_path_cover_context(reading_path: ReadingPath) -> tuple[str | None, str | None, int | None]:
    entry = _reading_path_collected_edition_entry(reading_path) or _reading_path_first_issue_entry(reading_path)
    if entry is None:
        return None, None, None
    if entry.canonical_issue is not None:
        issue = entry.canonical_issue
        year = issue.published_on.year if issue.published_on is not None else None
        return _expected_getcomics_title(issue), issue.issue_number, year
    if entry.issue is not None:
        issue = entry.issue
        year = issue.published_on.year if issue.published_on is not None else None
        return _expected_getcomics_title(issue), issue.issue_number, year
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


def _reading_path_curated_cover_url(reading_path: ReadingPath) -> str | None:
    asset = reading_path.cover_asset
    if asset is not None and asset.status == "ready" and asset.source_image_url:
        return asset.source_image_url
    return None


def _reading_path_ready_cover_url(reading_path: ReadingPath) -> str | None:
    curated_cover_url = _reading_path_curated_cover_url(reading_path)
    if curated_cover_url:
        return curated_cover_url
    asset = reading_path.cover_asset
    if asset is not None and asset.status == "ready" and asset.cached_path:
        return f"/reading-paths/{reading_path.id}/cover-image"
    provider_cover_url = _reading_path_provider_cover_url(reading_path)
    if provider_cover_url:
        return (
            f"/reading-paths/{reading_path.id}/cover-image"
            if _remote_cover_fetch_enabled() or _should_proxy_provider_cover_url(provider_cover_url)
            else provider_cover_url
        )
    return None


# Hosts whose covers are proxied even when remote cover fetching is otherwise off.
# Publisher CDNs are included because they vary hotlink policy by referer, so a
# direct <img> from the browser is far less reliable than fetching server-side.
PROXIED_COVER_HOSTS = (
    "cdn.readdetectiveconan.com/file/mangapill/",
    "i0.wp.com/getcomics.org/",
    "getcomics.org/share/uploads/",
    "cdn.marvel.com/",
    "static.dc.com/",
    "i.annihil.us/",
    "comicvine.gamespot.com/a/uploads/",
)


def _should_proxy_provider_cover_url(image_url: str) -> bool:
    lowered = image_url.lower()
    return any(host in lowered for host in PROXIED_COVER_HOSTS)


def _provider_cover_cache_key(image_url: str) -> str:
    return f"provider-cover-{hashlib.sha1(image_url.encode('utf-8')).hexdigest()[:16]}"


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
        return query, _expected_getcomics_title(issue), issue.issue_number, year
    if entry.issue is not None:
        issue = entry.issue
        year = issue.published_on.year if issue.published_on is not None else None
        return query, _expected_getcomics_title(issue), issue.issue_number, year
    return query, None, None, None


def _reading_path_entry_cover_context(entry: ReadingPathEntry) -> tuple[str, str | None, str | None, int | None]:
    return _reading_path_entry_download_context(entry)


def _entry_has_local_match(entry: ReadingPathEntry) -> bool:
    return _entry_local_issue(entry) is not None


def _entry_local_issue(entry: ReadingPathEntry) -> Issue | None:
    if entry.issue is not None:
        return entry.issue
    if entry.canonical_issue is None:
        return None
    primary_match = next(
        (
            match
            for match in entry.canonical_issue.issue_matches
            if match.is_primary and match.local_issue is not None
        ),
        None,
    )
    if primary_match is not None:
        return primary_match.local_issue
    fallback_match = next((match for match in entry.canonical_issue.issue_matches if match.local_issue is not None), None)
    return fallback_match.local_issue if fallback_match is not None else None


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


def _entry_getcomics_post_url(entry: ReadingPathEntry) -> str | None:
    """Resolve a source post, loosening the query for collected editions.

    A trade's subtitle is the least reliable part of its name, so an exact miss
    retries without it rather than failing the download.
    """
    query, expected_series_title, expected_issue_number, expected_year = _reading_path_entry_download_context(entry)
    candidates = (
        opds.collected_edition_queries(query)
        if entry.entry_type == "collection"
        else [query]
    )
    for candidate in candidates:
        cover = fetch_getcomics_cover(
            candidate,
            expected_series_title=expected_series_title,
            expected_issue_number=expected_issue_number,
            expected_year=expected_year,
        )
        if cover.post_url:
            return cover.post_url
    return None


def _entry_resolved_getcomics_post_url(entry: ReadingPathEntry) -> str:
    post_url = _entry_getcomics_post_url(entry)
    if not post_url:
        raise HTTPException(status_code=502, detail="No downloadable GetComics post was resolved for this issue.")
    return post_url


def _issue_downloadable_archive(issue: Issue) -> Archive | None:
    return next(
        (
            archive
            for archive in issue.archives
            if archive.storage_path and Path(archive.storage_path).exists() and Path(archive.storage_path).is_file()
        ),
        None,
    )


def _iter_local_file(path: Path, *, chunk_size: int = 1024 * 1024):
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _iter_remote_response(response: requests.Response, session: requests.Session, *, chunk_size: int = 1024 * 1024):
    try:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                yield chunk
    finally:
        response.close()
        session.close()


def _response_content_length(response: requests.Response) -> int | None:
    try:
        return int(response.headers.get("content-length") or 0) or None
    except ValueError:
        return None


@dataclass(frozen=True)
class EntryDownload:
    """One prepared download, including whatever range the mirror agreed to."""

    chunks: object
    filename: str
    media_type: str
    size_bytes: int | None = None
    content_range: str | None = None
    status_code: int = 200


def _close_download(download: "EntryDownload") -> None:
    """Release a prepared stream that will not be read."""
    closer = getattr(download.chunks, "close", None)
    if callable(closer):
        closer()


def _prepare_entry_device_download(
    entry: ReadingPathEntry, *, range_header: str | None = None
) -> EntryDownload:
    local_issue = _entry_local_issue(entry)
    if local_issue is not None:
        archive = _issue_downloadable_archive(local_issue)
        if archive is not None:
            path = Path(archive.storage_path)
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return EntryDownload(
                chunks=_iter_local_file(path),
                filename=archive.original_filename or path.name,
                media_type=media_type,
                size_bytes=path.stat().st_size,
            )

    if entry.canonical_issue is not None and entry.canonical_issue.provider_name == "MangaPill":
        raise HTTPException(status_code=409, detail="This source supports in-browser streaming only right now.")

    source_url = _entry_resolved_getcomics_post_url(entry)
    session = comics.build_session(False)
    request_headers = {"Range": range_header} if range_header else None
    try:
        plan = comics.resolve_download_plan(source_url, session, preferred_host=None)
        response = session.get(
            plan.resolved_url, timeout=60, allow_redirects=True, stream=True, headers=request_headers
        )
        comics.ensure_success(response, plan.resolved_url)
    except comics.ComicDownloadError as exc:
        session.close()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except requests.RequestException as exc:
        session.close()
        raise HTTPException(status_code=502, detail=f"Request failed for {source_url}: {exc}") from exc

    filename = comics.infer_filename(plan.post_title, response, plan.resolved_url)
    # Mirrors serve archives as octet-stream, which tells an OPDS reader nothing.
    # The extension is the reliable signal for what this actually is.
    media_type = opds.archive_media_type(filename)
    return EntryDownload(
        chunks=_iter_remote_response(response, session),
        filename=filename,
        media_type=media_type,
        size_bytes=_response_content_length(response),
        content_range=response.headers.get("content-range"),
        status_code=206 if response.status_code == 206 else 200,
    )


def _buffered_entry_archive(entry: ReadingPathEntry) -> Archive:
    cache_key = stream_buffer_key(entry.reading_path_id, entry.id)
    cached_archive = find_stream_archive(cache_key)
    if cached_archive is not None:
        return cached_archive

    source_url = _entry_resolved_getcomics_post_url(entry)
    session = comics.build_session(False)
    request_headers = {"Range": range_header} if range_header else None
    try:
        plan = comics.resolve_download_plan(source_url, session, preferred_host=None)
        response = session.get(
            plan.resolved_url, timeout=60, allow_redirects=True, stream=True, headers=request_headers
        )
        comics.ensure_success(response, plan.resolved_url)
        content_length = _response_content_length(response)
        if content_length is not None and content_length > stream_buffer_max_bytes():
            raise StreamBufferTooLargeError("Archive exceeds the configured stream buffer size limit.")
        filename = comics.infer_filename(plan.post_title, response, plan.resolved_url)
        archive = store_stream_archive(
            cache_key=cache_key,
            filename=filename,
            chunks=response.iter_content(chunk_size=1024 * 1024),
            source_url=plan.resolved_url,
            max_bytes=stream_buffer_max_bytes(),
        )
        response.close()
        session.close()
        return archive
    except comics.ComicDownloadError as exc:
        session.close()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except StreamBufferTooLargeError as exc:
        session.close()
        raise HTTPException(
            status_code=413,
            detail="This archive is too large to stream from this server. Download it to your device instead.",
        ) from exc
    except OSError as exc:
        session.close()
        if _is_storage_full_os_error(exc):
            raise HTTPException(status_code=507, detail=_storage_full_detail()) from exc
        raise
    except requests.RequestException as exc:
        session.close()
        raise HTTPException(status_code=502, detail=f"Request failed for {source_url}: {exc}") from exc


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
        normalized_line = line.strip()
        if normalized_line.startswith("Saved archive: "):
            imported_paths.append(normalized_line.removeprefix("Saved archive: ").strip())
        elif normalized_line.startswith("Archive:"):
            imported_paths.append(normalized_line.removeprefix("Archive:").strip())
        elif normalized_line.startswith("Extracted to: "):
            extracted_path = normalized_line.removeprefix("Extracted to: ").strip()
            if extracted_path:
                imported_paths.append(extracted_path)
        elif normalized_line.startswith("Extract:"):
            extracted_path = normalized_line.removeprefix("Extract:").strip()
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


def _link_imported_paths_to_entry(db: Session, imported_paths: list[str], entry: ReadingPathEntry) -> None:
    if entry.canonical_issue_id is None:
        return
    resolved_paths = [str(Path(raw_path).expanduser().resolve()) for raw_path in imported_paths]
    if not resolved_paths:
        return

    archives = db.scalars(
        select(Archive)
        .options(selectinload(Archive.issue).selectinload(Issue.canonical_matches))
        .where(or_(Archive.storage_path.in_(resolved_paths), Archive.extracted_path.in_(resolved_paths)))
    ).all()
    for archive in archives:
        if archive.issue is None:
            continue
        for match in archive.issue.canonical_matches:
            match.is_primary = False
        existing = next(
            (
                match
                for match in archive.issue.canonical_matches
                if match.canonical_issue_id == entry.canonical_issue_id
            ),
            None,
        )
        if existing is None:
            db.add(
                IssueMatch(
                    local_issue_id=archive.issue.id,
                    canonical_issue_id=entry.canonical_issue_id,
                    match_strategy="downloaded-reading-path-entry",
                    confidence_score=100,
                    is_primary=True,
                    note=f"Linked downloaded archive to reading path entry {entry.id}.",
                )
            )
        else:
            existing.match_strategy = "downloaded-reading-path-entry"
            existing.confidence_score = 100
            existing.is_primary = True
            existing.note = f"Linked downloaded archive to reading path entry {entry.id}."
    db.commit()


def _download_post_to_library(
    db: Session, post_url: str, *, destination: Path | None = None
) -> tuple[list[str], PersistResult]:
    downloads_root = destination or _downloads_root()
    downloads_root.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(REPO_ROOT / "comics.py"),
        post_url,
        "--output-dir",
        str(downloads_root),
        # Keep the archive as one file; the viewer reads inside it.
        "--no-extract",
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
        if _is_storage_full_error(error_output):
            raise HTTPException(status_code=507, detail=_storage_full_detail())
        raise HTTPException(status_code=502, detail=error_output)

    imported_paths = _parse_download_output(completed.stdout)
    try:
        return _import_downloaded_paths(db, imported_paths)
    except OSError as exc:
        if _is_storage_full_os_error(exc):
            raise HTTPException(status_code=507, detail=_storage_full_detail()) from exc
        raise
    except Exception as exc:
        if _is_storage_full_error(str(exc)):
            raise HTTPException(status_code=507, detail=_storage_full_detail()) from exc
        logger.exception("Downloaded GetComics archive could not be imported from %s", post_url)
        raise HTTPException(status_code=502, detail=f"Downloaded archive could not be imported: {exc}") from exc


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
    return _downloads_root() / directory_name


def _provider_download_binary(url: str, *, referer_url: str | None = None) -> tuple[bytes, str | None]:
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer_url:
        headers["Referer"] = referer_url
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.content, response.headers.get("Content-Type")


def _is_storage_full_error(value: str) -> bool:
    lowered = value.lower()
    return "disk quota exceeded" in lowered or "no space left on device" in lowered


def _is_storage_full_os_error(exc: OSError) -> bool:
    return exc.errno in {errno.EDQUOT, errno.ENOSPC}


def _storage_full_detail() -> str:
    return "Server download storage is full. Free space on the hosted account before downloading more issues."


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

    _downloads_root().mkdir(parents=True, exist_ok=True)
    target_dir = _provider_issue_directory(issue)
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    for index, page_url in enumerate(page_urls, start=1):
        content, content_type = _provider_download_binary(page_url, referer_url=issue.provider_url)
        extension = _provider_extension(page_url, content_type)
        page_path = target_dir / f"{index:04d}{extension}"
        try:
            page_path.write_bytes(content)
        except OSError as exc:
            if _is_storage_full_os_error(exc):
                raise HTTPException(status_code=507, detail=_storage_full_detail()) from exc
            raise

    try:
        scan = _provider_scan(issue, target_dir)
        result = persist_scans(db, [scan])
    except OSError as exc:
        if _is_storage_full_os_error(exc):
            raise HTTPException(status_code=507, detail=_storage_full_detail()) from exc
        raise
    except Exception as exc:
        if _is_storage_full_error(str(exc)):
            raise HTTPException(status_code=507, detail=_storage_full_detail()) from exc
        logger.exception("Downloaded provider issue could not be imported for canonical issue %s", issue.id)
        raise HTTPException(status_code=502, detail=f"Downloaded issue could not be imported: {exc}") from exc

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


def initialize_application() -> None:
    global _INITIALIZED
    with _INITIALIZATION_LOCK:
        if _INITIALIZED:
            return
        lock_path = REPO_ROOT / "backend" / ".panelstack-init.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                Base.metadata.create_all(bind=engine)
                ensure_runtime_schema()
                with SessionLocal() as db:
                    sync_curation_data(db)
                    if os.getenv("PANELSTACK_SYNC_PROVIDERS_ON_STARTUP", "1") != "0":
                        sync_mangapill_catalog(db, MANGAPILL_DATA_PATH)
                    sync_catalog_data(db)
                _INITIALIZED = True
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_application()
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
    request_path = request.url.path
    api_prefix_index = request_path.find("/api/")
    if api_prefix_index >= 0:
        request_path = request_path[api_prefix_index + len("/api") :]
    if request_path in {"/auth/session", "/auth/login", "/health"} or request_path.startswith("/auth/"):
        return await call_next(request)

    if request_path.startswith("/opds") and verify_opds_token(
        request.query_params.get(OPDS_TOKEN_PARAM)
    ):
        return await call_next(request)

    if verify_basic_auth(request.headers.get("authorization")):
        return await call_next(request)

    session_state = verify_session_cookie(request.cookies.get(SESSION_COOKIE_NAME))
    if not session_state.authenticated:
        if request_path.startswith("/opds"):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Panel Stack"'},
                media_type="text/plain",
                content="Authentication required.",
            )
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
    if _hosted_deployment():
        raise HTTPException(status_code=400, detail="Open Downloads is only available when Panel Stack is running locally.")
    downloads_root = _downloads_root()
    try:
        _open_path_in_file_manager(downloads_root)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="No file manager command is available on this system.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to open downloads folder: {exc}") from exc
    return {"path": str(downloads_root.resolve())}


@app.get("/settings", response_model=AppSettingsRead)
def get_app_settings() -> AppSettingsRead:
    return AppSettingsRead(
        download_root=str(_downloads_root()),
        default_download_root=str(_default_download_root()),
        hosted_deployment=_hosted_deployment(),
        opds_token=opds_access_token() if auth_enabled() else None,
    )


@app.put("/settings", response_model=AppSettingsRead)
def update_app_settings(payload: AppSettingsWrite) -> AppSettingsRead:
    download_root = _normalize_download_root(payload.download_root)
    try:
        download_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"Unable to create download folder: {exc}") from exc

    settings_payload = _read_app_settings_payload()
    settings_payload["download_root"] = str(download_root)
    _write_app_settings_payload(settings_payload)
    return AppSettingsRead(
        download_root=str(download_root),
        default_download_root=str(_default_download_root()),
        hosted_deployment=_hosted_deployment(),
    )


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


@app.api_route(
    "/reading-paths/{reading_path_id}/entries/{entry_id}/download", methods=["GET", "HEAD"]
)
def download_reading_path_entry_file(
    reading_path_id: int, entry_id: int, request: Request, db: Session = Depends(get_db)
) -> Response:
    entry = db.scalars(
        select(ReadingPathEntry)
        .options(
            selectinload(ReadingPathEntry.issue).selectinload(Issue.archives),
            selectinload(ReadingPathEntry.canonical_issue)
            .selectinload(CanonicalIssue.issue_matches)
            .selectinload(IssueMatch.local_issue)
            .selectinload(Issue.archives),
        )
        .where(ReadingPathEntry.reading_path_id == reading_path_id, ReadingPathEntry.id == entry_id)
    ).first()
    if entry is None or entry.entry_type not in {"issue", "collection"}:
        raise HTTPException(status_code=404, detail=f"Reading path entry {entry_id} not found")

    download = _prepare_entry_device_download(entry, range_header=request.headers.get("range"))
    headers = {
        "Content-Disposition": f'attachment; filename="{download.filename}"',
        # Download managers check this before offering resume.
        "Accept-Ranges": "bytes",
    }
    if download.size_bytes is not None:
        headers["Content-Length"] = str(download.size_bytes)
    if download.content_range:
        headers["Content-Range"] = download.content_range

    if request.method == "HEAD":
        # A HEAD probe wants the headers only; iterating the body would pull the
        # whole archive off the mirror for nothing.
        _close_download(download)
        return Response(status_code=download.status_code, media_type=download.media_type, headers=headers)

    return StreamingResponse(
        download.chunks, status_code=download.status_code, media_type=download.media_type, headers=headers
    )


@app.get("/reading-paths/{reading_path_id}/entries/{entry_id}/viewer", response_model=ReaderIssueRead)
def get_reading_path_entry_viewer_issue(
    reading_path_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
) -> ReaderIssueRead:
    entry = db.scalars(
        select(ReadingPathEntry)
        .options(
            selectinload(ReadingPathEntry.reading_path),
            selectinload(ReadingPathEntry.canonical_series),
            selectinload(ReadingPathEntry.canonical_issue),
        )
        .where(ReadingPathEntry.reading_path_id == reading_path_id, ReadingPathEntry.id == entry_id)
    ).first()
    if entry is None or entry.entry_type not in {"issue", "collection"}:
        raise HTTPException(status_code=404, detail=f"Reading path entry {entry_id} not found")
    if _entry_local_issue(entry) is not None:
        raise HTTPException(status_code=409, detail="This entry should be opened through the local library viewer.")
    if entry.canonical_issue is not None and entry.canonical_issue.provider_name == "MangaPill":
        raise HTTPException(status_code=409, detail="This entry should be opened through provider streaming.")

    archive = _buffered_entry_archive(entry)
    try:
        pages = list_archive_pages(archive)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    title = entry.canonical_issue.title if entry.canonical_issue is not None and entry.canonical_issue.title else (
        entry.label or "Issue"
    )
    issue_number = (
        entry.canonical_issue.issue_number
        if entry.canonical_issue is not None
        else (entry.issue.issue_number if entry.issue is not None else str(entry.sort_order))
    )
    published_on = entry.canonical_issue.published_on if entry.canonical_issue is not None else None
    issue_key = _issue_state_key(issue_id=entry.issue_id, canonical_issue_id=entry.canonical_issue_id)
    state = db.scalar(select(UserIssueState).where(UserIssueState.issue_key == issue_key)) if issue_key is not None else None

    return ReaderIssueRead(
        id=f"reading-path-entry:{reading_path_id}:{entry_id}",
        issue_number=issue_number,
        title=title,
        published_on=published_on,
        summary=entry.note,
        page_count=len(pages),
        cover_url=_provider_issue_cover_url(entry.canonical_issue) if entry.canonical_issue is not None else None,
        reading_path_id=reading_path_id,
        reading_path_entry_id=entry_id,
        canonical_issue_id=entry.canonical_issue_id,
        is_read=bool(state is not None and state.is_read),
        pages=[
            ArchivePageRead(
                index=page.index,
                relative_path=page.relative_path,
                media_type=page.media_type,
                image_url=f"/reading-paths/{reading_path_id}/entries/{entry_id}/pages/{page.index}",
            )
            for page in pages
        ],
    )


@app.get("/reading-paths/{reading_path_id}/entries/{entry_id}/pages/{page_number}")
def get_reading_path_entry_page_image(
    reading_path_id: int,
    entry_id: int,
    page_number: int,
    db: Session = Depends(get_db),
) -> Response:
    entry = db.get(ReadingPathEntry, entry_id)
    if entry is None or entry.reading_path_id != reading_path_id:
        raise HTTPException(status_code=404, detail=f"Reading path entry {entry_id} not found")

    archive = _buffered_entry_archive(entry)
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


@app.get("/catalog/facets", response_model=CatalogFacetsResponse)
def get_catalog_facets(db: Session = Depends(get_db)) -> CatalogFacetsResponse:
    facets = catalog_facets(db)
    return CatalogFacetsResponse(
        publishers=[CatalogFacetRead(value=f.value, label=f.label, count=f.count) for f in facets.publishers],
        lines=[CatalogFacetRead(value=f.value, label=f.label, count=f.count) for f in facets.lines],
        characters=[CatalogFacetRead(value=f.value, label=f.label, count=f.count) for f in facets.characters],
        min_year=facets.min_year,
        max_year=facets.max_year,
    )


@app.get("/catalog/collections", response_model=CatalogCollectionListResponse)
def list_catalog_collections(
    db: Session = Depends(get_db),
    publisher: list[str] | None = Query(None),
    line: str | None = Query(None),
    character: str | None = Query(None),
    start: date | None = Query(None),
    end: date | None = Query(None),
    search: str | None = Query(None),
    owned: bool | None = Query(None),
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> CatalogCollectionListResponse:
    collections, total = catalog_collections(
        db,
        publisher=publisher,
        line=line,
        character=character,
        start=start,
        end=end,
        search=search,
        owned=owned,
        limit=limit,
        offset=offset,
    )
    owned_by_collection = owned_counts(db, [collection.id for collection in collections])
    return CatalogCollectionListResponse(
        items=[
            CatalogCollectionSummary(
                id=collection.id,
                slug=collection.slug,
                title=collection.title,
                publisher=collection.publisher.name if collection.publisher else None,
                line=collection.line,
                collection_type=collection.collection_type,
                volume_number=collection.volume_number,
                issue_count=len(collection.items),
                owned_count=owned_by_collection.get(collection.id, 0),
                tags=[tag.tag for tag in collection.tags],
                first_published_on=collection.first_published_on,
                latest_published_on=collection.latest_published_on,
                reading_path_id=collection.reading_path_id,
                cover_url=_reading_path_ready_cover_url(collection.reading_path) if collection.reading_path else None,
            )
            for collection in collections
        ],
        total=total,
    )


@app.get("/catalog/chronology", response_model=ChronologyResponse)
def get_catalog_chronology(
    db: Session = Depends(get_db),
    publisher: list[str] | None = Query(None),
    line: str | None = Query(None),
    character: str | None = Query(None),
    start: date | None = Query(None),
    end: date | None = Query(None),
    search: str | None = Query(None),
    owned: bool | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ChronologyResponse:
    rows, total = catalog_chronology(
        db,
        publisher=publisher,
        line=line,
        character=character,
        start=start,
        end=end,
        search=search,
        owned=owned,
        limit=limit,
        offset=offset,
    )
    return ChronologyResponse(
        items=[
            ChronologyEntryRead(
                canonical_issue_id=row.canonical_issue.id,
                title=row.canonical_issue.title or f"Issue {row.canonical_issue.issue_number}",
                issue_number=row.canonical_issue.issue_number,
                published_on=row.published_on,
                publisher=row.collection.publisher.name if row.collection.publisher else None,
                line=row.collection.line,
                collection_id=row.collection.id,
                collection_title=row.collection.title,
                reading_path_id=row.collection.reading_path_id,
                cover_url=_canonical_issue_cover_url(row.canonical_issue)
                or (_reading_path_ready_cover_url(row.collection.reading_path) if row.collection.reading_path else None),
            )
            for row in rows
        ],
        total=total,
    )


def _collection_download_entries(reading_path: ReadingPath) -> list[ReadingPathEntry]:
    """Prefer trades, then fill in issues the trades do not cover.

    A collected edition is one download instead of six, so when a run has one it
    stands in for the issues it collects. Issues published since the last trade
    still come through individually.
    """
    collected = [entry for entry in reading_path.entries if entry.entry_type == "collection"]
    issues = [entry for entry in reading_path.entries if entry.entry_type == "issue"]
    if not collected:
        return issues

    covered: set[str] = set()
    for entry in collected:
        issue = entry.canonical_issue
        if issue is None or "-" not in issue.issue_number:
            continue
        first, _, last = issue.issue_number.partition("-")
        if first.strip().isdigit() and last.strip().isdigit():
            covered.update(str(number) for number in range(int(first), int(last) + 1))

    uncollected = [
        entry
        for entry in issues
        if entry.canonical_issue is None or entry.canonical_issue.issue_number not in covered
    ]
    return collected + uncollected


def _reading_list(db: Session, reading_list_id: int) -> ReadingList:
    reading_list = db.scalars(
        select(ReadingList).options(selectinload(ReadingList.items)).where(ReadingList.id == reading_list_id)
    ).first()
    if reading_list is None:
        raise HTTPException(status_code=404, detail=f"Reading list {reading_list_id} not found")
    return reading_list


def _reading_list_item_read(item: ReadingListItem, entry: ReadingPathEntry | None) -> ReadingListItemRead:
    return ReadingListItemRead(
        id=item.id,
        reading_path_id=item.reading_path_id,
        entry_id=item.reading_path_entry_id,
        title=item.title,
        sort_order=item.sort_order,
        owned=_entry_has_local_match(entry) if entry is not None else False,
        cover_url=(
            _provider_issue_cover_url(entry.canonical_issue)
            if entry is not None and entry.canonical_issue is not None
            else None
        ),
    )


def _reading_list_read(db: Session, reading_list: ReadingList) -> ReadingListRead:
    entry_ids = [item.reading_path_entry_id for item in reading_list.items]
    entries = {
        entry.id: entry
        for entry in db.scalars(
            select(ReadingPathEntry)
            .options(
                selectinload(ReadingPathEntry.canonical_issue).selectinload(CanonicalIssue.series),
                selectinload(ReadingPathEntry.canonical_issue)
                .selectinload(CanonicalIssue.issue_matches)
                .selectinload(IssueMatch.local_issue)
                .selectinload(Issue.archives),
                selectinload(ReadingPathEntry.issue).selectinload(Issue.archives),
            )
            .where(ReadingPathEntry.id.in_(entry_ids))
        )
    } if entry_ids else {}
    return ReadingListRead(
        id=reading_list.id,
        name=reading_list.name,
        description=reading_list.description,
        items=[_reading_list_item_read(item, entries.get(item.reading_path_entry_id)) for item in reading_list.items],
    )


@app.get("/reading-lists", response_model=ReadingListListResponse)
def list_reading_lists(db: Session = Depends(get_db)) -> ReadingListListResponse:
    lists = db.scalars(
        select(ReadingList).options(selectinload(ReadingList.items)).order_by(ReadingList.name.asc())
    ).all()
    return ReadingListListResponse(
        items=[
            ReadingListSummary(
                id=reading_list.id,
                name=reading_list.name,
                description=reading_list.description,
                item_count=len(reading_list.items),
            )
            for reading_list in lists
        ],
        total=len(lists),
    )


@app.post("/reading-lists", response_model=ReadingListRead, status_code=201)
def create_reading_list(payload: ReadingListWrite, db: Session = Depends(get_db)) -> ReadingListRead:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="A list name is required.")
    if db.scalar(select(ReadingList).where(ReadingList.name == name)) is not None:
        raise HTTPException(status_code=409, detail=f"A list named '{name}' already exists.")
    reading_list = ReadingList(name=name, description=payload.description)
    db.add(reading_list)
    db.commit()
    db.refresh(reading_list)
    return _reading_list_read(db, reading_list)


@app.get("/reading-lists/{reading_list_id}", response_model=ReadingListRead)
def get_reading_list(reading_list_id: int, db: Session = Depends(get_db)) -> ReadingListRead:
    return _reading_list_read(db, _reading_list(db, reading_list_id))


@app.patch("/reading-lists/{reading_list_id}", response_model=ReadingListRead)
def rename_reading_list(
    reading_list_id: int, payload: ReadingListWrite, db: Session = Depends(get_db)
) -> ReadingListRead:
    reading_list = _reading_list(db, reading_list_id)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="A list name is required.")
    reading_list.name = name
    reading_list.description = payload.description
    db.commit()
    return _reading_list_read(db, reading_list)


@app.delete("/reading-lists/{reading_list_id}", status_code=204)
def delete_reading_list(reading_list_id: int, db: Session = Depends(get_db)) -> Response:
    db.delete(_reading_list(db, reading_list_id))
    db.commit()
    return Response(status_code=204)


@app.post("/reading-lists/{reading_list_id}/items", response_model=ReadingListRead)
def add_reading_list_items(
    reading_list_id: int, payload: ReadingListItemsWrite, db: Session = Depends(get_db)
) -> ReadingListRead:
    reading_list = _reading_list(db, reading_list_id)
    existing = {item.reading_path_entry_id for item in reading_list.items}
    next_sort = max((item.sort_order for item in reading_list.items), default=-1) + 1
    for candidate in payload.items:
        if candidate.entry_id in existing:
            continue
        db.add(
            ReadingListItem(
                reading_list_id=reading_list.id,
                reading_path_id=candidate.reading_path_id,
                reading_path_entry_id=candidate.entry_id,
                title=candidate.title,
                sort_order=next_sort,
            )
        )
        existing.add(candidate.entry_id)
        next_sort += 1
    db.commit()
    db.refresh(reading_list)
    return _reading_list_read(db, reading_list)


@app.delete("/reading-lists/{reading_list_id}/items/{item_id}", response_model=ReadingListRead)
def remove_reading_list_item(
    reading_list_id: int, item_id: int, db: Session = Depends(get_db)
) -> ReadingListRead:
    reading_list = _reading_list(db, reading_list_id)
    item = next((candidate for candidate in reading_list.items if candidate.id == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Reading list item {item_id} not found")
    db.delete(item)
    db.commit()
    db.refresh(reading_list)
    return _reading_list_read(db, reading_list)


def _require_local_deployment() -> None:
    if _hosted_deployment():
        raise HTTPException(
            status_code=410,
            detail="Downloads run on your own machine. The hosted library is browse and preview only.",
        )


def _download_destination(destination: str | None) -> Path:
    return _normalize_download_root(destination) if destination else _downloads_root()


def _download_entry(db: Session, reading_path_id: int, entry_id: int) -> ReadingPathEntry:
    entry = db.scalars(
        select(ReadingPathEntry)
        .options(
            selectinload(ReadingPathEntry.canonical_issue).selectinload(CanonicalIssue.series),
            selectinload(ReadingPathEntry.issue).selectinload(Issue.series),
        )
        .where(ReadingPathEntry.id == entry_id, ReadingPathEntry.reading_path_id == reading_path_id)
    ).first()
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Reading path entry {entry_id} not found")
    return entry


def _download_post_url(entry: ReadingPathEntry) -> str | None:
    return _entry_getcomics_post_url(entry)


def _download_archive_size(post_url: str, session: requests.Session) -> int | None:
    """Ask the mirror for the real archive size without downloading it."""
    plan = comics.resolve_download_plan(post_url, session, preferred_host=None)
    response = session.get(plan.resolved_url, timeout=60, allow_redirects=True, stream=True)
    try:
        comics.ensure_success(response, plan.resolved_url)
        return _response_content_length(response)
    finally:
        response.close()


@app.post("/downloads/estimate", response_model=DownloadEstimateResponse)
def estimate_downloads(payload: DownloadEstimateWrite) -> DownloadEstimateResponse:
    _require_local_deployment()
    destination = _download_destination(payload.destination)

    # One HTTP session for the whole estimate so connection reuse and cookies
    # carry across targets.
    http = comics.build_session(insecure=False)

    def resolve(target: DownloadTarget) -> tuple[int | None, str | None]:
        with SessionLocal() as session:
            entry = _download_entry(session, target.reading_path_id, target.entry_id)
            post_url = _download_post_url(entry)
        if not post_url:
            return None, None
        return _download_archive_size(post_url, http), post_url

    resolved = resolve_targets(
        [
            DownloadTarget(reading_path_id=t.reading_path_id, entry_id=t.entry_id, title=t.title)
            for t in payload.targets
        ],
        resolve,
    )
    http.close()
    total_bytes = sum(item.size_bytes or 0 for item in resolved)
    space = destination_space(destination)
    return DownloadEstimateResponse(
        targets=[
            DownloadTargetRead(
                reading_path_id=item.reading_path_id,
                entry_id=item.entry_id,
                title=item.title,
                size_bytes=item.size_bytes,
                status=item.status,
                detail=item.detail,
            )
            for item in resolved
        ],
        total_bytes=total_bytes,
        resolved_count=sum(1 for item in resolved if item.status == "ready"),
        unavailable_count=sum(1 for item in resolved if item.status != "ready"),
        destination=DestinationSpaceRead(
            path=space.path,
            total_bytes=space.total_bytes,
            free_bytes=space.free_bytes,
            exists=space.exists,
        ),
        fits=total_bytes < space.free_bytes,
    )


def _download_snapshot_response(snapshot) -> DownloadQueueRead:
    return DownloadQueueRead(
        id=snapshot.id,
        destination=snapshot.destination,
        status=snapshot.status,
        items=[
            DownloadQueueItemRead(
                reading_path_id=item.reading_path_id,
                entry_id=item.entry_id,
                title=item.title,
                size_bytes=item.size_bytes,
                status=item.status,
                detail=item.detail,
            )
            for item in snapshot.items
        ],
        started_at=snapshot.started_at,
        finished_at=snapshot.finished_at,
        completed_count=snapshot.completed_count,
        total_count=len(snapshot.items),
    )


@app.post("/downloads/queue", response_model=DownloadQueueRead)
def start_downloads(payload: DownloadStartWrite) -> DownloadQueueRead:
    _require_local_deployment()
    if not payload.targets:
        raise HTTPException(status_code=422, detail="Select at least one issue to download.")
    destination = _download_destination(payload.destination)
    destination.mkdir(parents=True, exist_ok=True)

    def run(item: QueueItem) -> str | None:
        with SessionLocal() as session:
            entry = _download_entry(session, item.reading_path_id, item.entry_id)
            if _entry_has_local_match(entry):
                return "Already in the library."
            post_url = _download_post_url(entry)
            if not post_url:
                raise RuntimeError("No downloadable source was found.")
            imported_paths, _ = _download_post_to_library(session, post_url, destination=destination)
            _link_imported_paths_to_entry(session, imported_paths, entry)
            return None

    try:
        snapshot = QUEUE.start(
            destination=destination,
            items=[
                QueueItem(
                    reading_path_id=t.reading_path_id,
                    entry_id=t.entry_id,
                    title=t.title,
                    size_bytes=None,
                )
                for t in payload.targets
            ],
            runner=run,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _download_snapshot_response(snapshot)


@app.get("/downloads/queue", response_model=DownloadQueueRead | None)
def get_download_queue() -> DownloadQueueRead | None:
    _require_local_deployment()
    snapshot = QUEUE.snapshot()
    return _download_snapshot_response(snapshot) if snapshot else None


@app.post("/downloads/queue/cancel", response_model=DownloadQueueRead | None)
def cancel_downloads() -> DownloadQueueRead | None:
    _require_local_deployment()
    QUEUE.cancel()
    snapshot = QUEUE.snapshot()
    return _download_snapshot_response(snapshot) if snapshot else None


OPDS_ROOT_ID = "urn:panelstack:opds"


def _opds_link(href: str, token: str | None, redirect: bool = False) -> str:
    """Carry the access token, and the delivery mode, onto every link.

    The token avoids a 401 challenge per request. The delivery mode has to travel
    too, otherwise choosing it on the catalog URL would not reach the acquisition
    links the reader actually fetches.
    """
    parts = []
    if token:
        parts.append(f"{OPDS_TOKEN_PARAM}={token}")
    if redirect:
        parts.append("redirect=1")
    if not parts:
        return href
    separator = "&" if "?" in href else "?"
    return f"{href}{separator}{'&'.join(parts)}"


def _opds_token(request: Request) -> str | None:
    return request.query_params.get(OPDS_TOKEN_PARAM)


def _opds_redirect(request: Request) -> bool:
    return request.query_params.get("redirect") == "1"


def _opds_base(request: Request) -> str:
    """External prefix for OPDS URLs, honouring the /panels mount.

    The passenger middleware rewrites the path before routing, so the request's
    own base_url has already lost the mount prefix; it is added back here.
    """
    root = str(request.base_url).rstrip("/")
    mount = os.getenv("PANELSTACK_BASE_PATH", "").rstrip("/")
    if mount and not root.endswith(mount):
        root = f"{root}{mount}"
    return root


def _opds_response(body: str, kind: str) -> Response:
    media_type = opds.NAVIGATION_TYPE if kind == "navigation" else opds.ACQUISITION_TYPE
    return Response(content=body, media_type=media_type)


def _entry_mirror_url(entry: ReadingPathEntry) -> str | None:
    """Resolve the mirror URL a reader can fetch directly.

    Returns None when the entry is backed by a local file, which has no URL to
    hand out and must be served from disk.
    """
    if _entry_local_issue(entry) is not None and _issue_downloadable_archive(_entry_local_issue(entry)) is not None:
        return None
    if entry.canonical_issue is not None and entry.canonical_issue.provider_name == "MangaPill":
        raise HTTPException(status_code=409, detail="This source supports in-browser streaming only right now.")

    source_url = _entry_resolved_getcomics_post_url(entry)
    session = comics.build_session(False)
    try:
        plan = comics.resolve_download_plan(source_url, session, preferred_host=None)
    except comics.ComicDownloadError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Request failed for {source_url}: {exc}") from exc
    finally:
        session.close()
    return plan.resolved_url


@app.api_route("/opds/download/{reading_path_id}/{entry_id}", methods=["GET", "HEAD"])
def opds_download(
    reading_path_id: int, entry_id: int, request: Request, db: Session = Depends(get_db)
) -> Response:
    """Hand the reader the mirror URL rather than relaying the bytes.

    Proxying a multi-hundred-megabyte archive holds a connection open on the host
    for minutes per file. Several of those at once looks like a connection flood
    to the host firewall, which bans the client mid-download. Redirecting costs
    the host a couple of seconds of resolution and no bandwidth, and the reader
    gets the mirror's own range support for free.
    """
    entry = db.scalars(
        select(ReadingPathEntry)
        .options(
            selectinload(ReadingPathEntry.issue).selectinload(Issue.archives),
            selectinload(ReadingPathEntry.canonical_issue)
            .selectinload(CanonicalIssue.issue_matches)
            .selectinload(IssueMatch.local_issue)
            .selectinload(Issue.archives),
        )
        .where(ReadingPathEntry.reading_path_id == reading_path_id, ReadingPathEntry.id == entry_id)
    ).first()
    if entry is None or entry.entry_type not in {"issue", "collection"}:
        raise HTTPException(status_code=404, detail=f"Reading path entry {entry_id} not found")

    # Two ways to hand a file over, switchable per catalog URL.
    #
    # Default is to relay: the host streams the bytes. Panels made real progress
    # this way, and starts nothing at all when handed a 302 — it appears not to
    # follow redirects on acquisition links. The cost is a connection held open
    # for the whole transfer.
    #
    # redirect=1 hands over the mirror URL instead. The host is free in about a
    # second and the reader inherits the mirror's own resume support, which is
    # better for any reader that does follow redirects.
    if request.query_params.get("redirect") != "1":
        return download_reading_path_entry_file(reading_path_id, entry_id, request, db)

    mirror_url = _entry_mirror_url(entry)
    if mirror_url is None:
        # Local file: there is nothing to redirect to.
        return download_reading_path_entry_file(reading_path_id, entry_id, request, db)
    return RedirectResponse(mirror_url, status_code=302)


@app.api_route("/opds/cover/{reading_path_id}/{entry_id}", methods=["GET", "HEAD"])
def opds_cover(reading_path_id: int, entry_id: int, db: Session = Depends(get_db)) -> FileResponse:
    return get_reading_path_entry_cover_image(reading_path_id, entry_id, db)


def _opds_entry_download_href(
    base: str, reading_path_id: int, entry_id: int, token: str | None = None, redirect: bool = False
) -> str:
    return _opds_link(f"{base}/opds/download/{reading_path_id}/{entry_id}", token, redirect)


def _opds_entry_cover_href(base: str, reading_path_id: int, entry_id: int, token: str | None = None) -> str:
    # Covers are small, so they never need the relay flag.
    return _opds_link(f"{base}/opds/cover/{reading_path_id}/{entry_id}", token)


def _opds_entry_title(entry: ReadingPathEntry) -> str:
    if entry.canonical_issue is not None:
        return entry.canonical_issue.title or f"Issue {entry.canonical_issue.issue_number}"
    return entry.label or f"Entry {entry.id}"


@app.get("/opds")
def opds_root(request: Request) -> Response:
    base = _opds_base(request)
    token = _opds_token(request)
    redirect = _opds_redirect(request)
    body = opds.feed(
        feed_id=OPDS_ROOT_ID,
        title="Panel Stack",
        self_href=_opds_link(f"{base}/opds", token, redirect),
        start_href=_opds_link(f"{base}/opds", token, redirect),
        entries=[
            opds.navigation_entry(
                identifier=f"{OPDS_ROOT_ID}:lists",
                title="Reading lists",
                href=_opds_link(f"{base}/opds/lists", token, redirect),
                summary="Lists you built in Panel Stack.",
            ),
            opds.navigation_entry(
                identifier=f"{OPDS_ROOT_ID}:collections",
                title="Collections",
                href=_opds_link(f"{base}/opds/collections", token, redirect),
                summary="Every curated run and collected edition.",
            ),
        ],
    )
    return _opds_response(body, "navigation")


@app.get("/opds/lists")
def opds_lists(request: Request, db: Session = Depends(get_db)) -> Response:
    base = _opds_base(request)
    token = _opds_token(request)
    redirect = _opds_redirect(request)
    lists = db.scalars(
        select(ReadingList).options(selectinload(ReadingList.items)).order_by(ReadingList.name.asc())
    ).all()
    body = opds.feed(
        feed_id=f"{OPDS_ROOT_ID}:lists",
        title="Reading lists",
        self_href=_opds_link(f"{base}/opds/lists", token, redirect),
        start_href=_opds_link(f"{base}/opds", token, redirect),
        up_href=_opds_link(f"{base}/opds", token, redirect),
        entries=[
            opds.navigation_entry(
                identifier=f"{OPDS_ROOT_ID}:list:{reading_list.id}",
                title=reading_list.name,
                href=_opds_link(f"{base}/opds/lists/{reading_list.id}", token, redirect),
                summary=f"{len(reading_list.items)} issues",
                kind="acquisition",
            )
            for reading_list in lists
        ],
    )
    return _opds_response(body, "navigation")


@app.get("/opds/lists/{reading_list_id}")
def opds_list(reading_list_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    base = _opds_base(request)
    token = _opds_token(request)
    redirect = _opds_redirect(request)
    reading_list = _reading_list(db, reading_list_id)
    entries = []
    for item in reading_list.items:
        entries.append(
            opds.acquisition_entry(
                identifier=f"{OPDS_ROOT_ID}:item:{item.id}",
                title=item.title,
                download_href=_opds_entry_download_href(base, item.reading_path_id, item.reading_path_entry_id, token, redirect),
                media_type=opds.DEFAULT_ARCHIVE_MEDIA_TYPE,
                cover_href=_opds_entry_cover_href(base, item.reading_path_id, item.reading_path_entry_id, token),
            )
        )
    body = opds.feed(
        feed_id=f"{OPDS_ROOT_ID}:list:{reading_list.id}",
        title=reading_list.name,
        self_href=_opds_link(f"{base}/opds/lists/{reading_list.id}", token, redirect),
        start_href=_opds_link(f"{base}/opds", token, redirect),
        up_href=_opds_link(f"{base}/opds/lists", token, redirect),
        entries=entries,
        kind="acquisition",
    )
    return _opds_response(body, "acquisition")


# Manga lines are one continuous work, so a run-year suffix is noise there. For
# Marvel and DC the years are how you tell one Batman run from the next.
YEARED_PUBLISHERS = {"dc", "marvel"}


@app.get("/opds/collections")
def opds_collections(request: Request, db: Session = Depends(get_db)) -> Response:
    """Publishers. The tree is publisher -> series -> issues."""
    base = _opds_base(request)
    token = _opds_token(request)
    redirect = _opds_redirect(request)
    body = opds.feed(
        feed_id=f"{OPDS_ROOT_ID}:collections",
        title="Collections",
        self_href=_opds_link(f"{base}/opds/collections", token, redirect),
        start_href=_opds_link(f"{base}/opds", token, redirect),
        up_href=_opds_link(f"{base}/opds", token, redirect),
        entries=[
            opds.navigation_entry(
                identifier=f"{OPDS_ROOT_ID}:publisher:{publisher.value}",
                title=publisher.label,
                href=_opds_link(f"{base}/opds/collections/{publisher.value}", token, redirect),
                summary=f"{publisher.count} volumes",
            )
            for publisher in catalog_publishers(db)
        ],
    )
    return _opds_response(body, "navigation")


@app.get("/opds/collections/{publisher_slug}")
def opds_publisher_series(publisher_slug: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """A publisher's series, named the way you would look them up."""
    base = _opds_base(request)
    token = _opds_token(request)
    redirect = _opds_redirect(request)
    groups = catalog_series(db, publisher_slug)
    if not groups:
        raise HTTPException(status_code=404, detail=f"No collections for publisher {publisher_slug}")

    with_years = publisher_slug in YEARED_PUBLISHERS
    body = opds.feed(
        feed_id=f"{OPDS_ROOT_ID}:publisher:{publisher_slug}",
        title=groups[0].publisher_name,
        self_href=_opds_link(f"{base}/opds/collections/{publisher_slug}", token, redirect),
        start_href=_opds_link(f"{base}/opds", token, redirect),
        up_href=_opds_link(f"{base}/opds/collections", token, redirect),
        entries=[
            opds.navigation_entry(
                identifier=f"{OPDS_ROOT_ID}:series:{group.canonical_series_id}",
                title=group.display_title(with_years=with_years),
                href=_opds_link(
                    f"{base}/opds/collections/{publisher_slug}/{group.canonical_series_id}", token, redirect
                ),
                summary=f"{group.volume_count} volumes",
                kind="acquisition",
            )
            for group in groups
        ],
    )
    return _opds_response(body, "navigation")


@app.get("/opds/collections/{publisher_slug}/{series_id}")
def opds_series(publisher_slug: str, series_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    """Everything readable in one series, trades first.

    A volume is not a folder here. Each volume contributes its collected edition
    if one exists, or its loose issues if not, so the series reads as one flat
    shelf rather than a stack of single-entry directories.
    """
    base = _opds_base(request)
    token = _opds_token(request)
    redirect = _opds_redirect(request)
    groups = {group.canonical_series_id: group for group in catalog_series(db, publisher_slug)}
    group = groups.get(series_id)
    if group is None:
        raise HTTPException(status_code=404, detail=f"Series {series_id} not found for {publisher_slug}")

    entries = []
    for reading_path_id in group.reading_path_ids:
        reading_path = db.scalars(
            select(ReadingPath)
            .options(selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.canonical_issue))
            .where(ReadingPath.id == reading_path_id)
        ).first()
        if reading_path is None:
            continue
        for entry in _collection_download_entries(reading_path):
            entries.append(
                opds.acquisition_entry(
                    identifier=f"{OPDS_ROOT_ID}:entry:{entry.id}",
                    title=_opds_entry_title(entry),
                    download_href=_opds_entry_download_href(base, reading_path.id, entry.id, token, redirect),
                    media_type=opds.DEFAULT_ARCHIVE_MEDIA_TYPE,
                    cover_href=_opds_entry_cover_href(base, reading_path.id, entry.id, token),
                    updated=(
                        entry.canonical_issue.published_on.strftime("%Y-%m-%dT00:00:00Z")
                        if entry.canonical_issue is not None and entry.canonical_issue.published_on
                        else None
                    ),
                )
            )

    body = opds.feed(
        feed_id=f"{OPDS_ROOT_ID}:series:{series_id}",
        title=group.display_title(with_years=publisher_slug in YEARED_PUBLISHERS),
        self_href=_opds_link(f"{base}/opds/collections/{publisher_slug}/{series_id}", token, redirect),
        start_href=_opds_link(f"{base}/opds", token, redirect),
        up_href=_opds_link(f"{base}/opds/collections/{publisher_slug}", token, redirect),
        entries=entries,
        kind="acquisition",
    )
    return _opds_response(body, "acquisition")


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

    try:
        pages = fetch_mangapill_chapter_pages(issue.provider_url)
    except Exception as exc:
        logger.exception("Failed to resolve MangaPill pages for canonical issue %s", issue_id)
        raise HTTPException(status_code=502, detail="Provider pages are unavailable right now.") from exc
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
            curated_cover_url = _reading_path_curated_cover_url(reading_path)
            if curated_cover_url:
                asset = reading_path.cover_asset
                items.append(
                    ReadingPathCoverRead(
                        reading_path_id=reading_path_id,
                        image_url=curated_cover_url,
                        post_url=asset.post_url if asset is not None else reading_path.source_url,
                        post_title=asset.post_title if asset is not None else reading_path.title,
                        query=asset.query if asset is not None else fallback_query,
                    )
                )
                continue
            provider_cover_url = _reading_path_provider_cover_url(reading_path)
            if provider_cover_url:
                should_proxy_provider_cover = _should_proxy_provider_cover_url(provider_cover_url)
                items.append(
                    ReadingPathCoverRead(
                        reading_path_id=reading_path_id,
                        image_url=(
                            f"/reading-paths/{reading_path_id}/cover-image"
                            if _remote_cover_fetch_enabled() or should_proxy_provider_cover
                            else provider_cover_url
                        ),
                        query=fallback_query,
                    )
                )
                continue
            if not _remote_cover_fetch_enabled():
                asset = reading_path.cover_asset
                items.append(
                    ReadingPathCoverRead(
                        reading_path_id=reading_path_id,
                        image_url=(
                            f"/reading-paths/{reading_path_id}/cover-image"
                            if asset is not None and asset.status == "ready" and asset.cached_path
                            else None
                        ),
                        post_url=asset.post_url if asset is not None else None,
                        post_title=asset.post_title if asset is not None else None,
                        query=asset.query if asset is not None else fallback_query,
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
        if not _remote_cover_fetch_enabled() and not _should_proxy_provider_cover_url(provider_cover_url):
            raise HTTPException(status_code=404, detail="Cover image proxying is disabled")
        cached_path, content_type = ensure_remote_cover_image(
            cache_key=_provider_cover_cache_key(provider_cover_url),
            image_url=provider_cover_url,
            referer_url=_provider_cover_referer(provider_cover_url, reading_path.source_url),
        )
        if cached_path is None or not cached_path.exists():
            raise HTTPException(status_code=404, detail="Cover image unavailable")
        return FileResponse(cached_path, media_type=content_type, filename=cached_path.name)

    if _remote_cover_fetch_enabled():
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
    else:
        asset = reading_path.cover_asset

    if asset is None or asset.status != "ready" or not asset.cached_path:
        raise HTTPException(status_code=404, detail="Cover image unavailable")

    cached_path = Path(asset.cached_path)
    if not cached_path.exists():
        raise HTTPException(status_code=404, detail="Cached cover image missing")

    db.commit()
    return FileResponse(cached_path, media_type=asset.content_type or "image/jpeg")


@app.post("/reading-paths/{reading_path_id}/download", response_model=ReadingPathDownloadResponse)
def download_reading_path_issue(reading_path_id: int, db: Session = Depends(get_db)) -> ReadingPathDownloadResponse:
    if _hosted_deployment():
        raise HTTPException(
            status_code=410,
            detail="Hosted library downloads are disabled. Download the issue directly to your device instead.",
        )
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
    missing_entries = [entry for entry in issue_entries if not _entry_has_local_match(entry)]
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
            _link_imported_paths_to_entry(db, item_paths, entry)
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
        _link_imported_paths_to_entry(db, item_paths, entry)
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
    if _hosted_deployment():
        raise HTTPException(
            status_code=410,
            detail="Hosted library downloads are disabled. Download the issue directly to your device instead.",
        )
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
    if entry is None or entry.entry_type not in {"issue", "collection"}:
        raise HTTPException(status_code=404, detail=f"Reading path entry {entry_id} not found")
    if _entry_has_local_match(entry):
        return ReadingPathDownloadResponse(
            reading_path_id=reading_path_id,
            entry_id=entry_id,
            downloaded_issue_count=0,
            skipped_issue_count=1,
        )

    if entry.canonical_issue is not None and entry.canonical_issue.provider_name == "MangaPill":
        imported_paths, result = _download_provider_issue_to_library(db, entry.canonical_issue)
        _link_imported_paths_to_entry(db, imported_paths, entry)
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
    _link_imported_paths_to_entry(db, imported_paths, entry)
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
            selectinload(ReadingPath.cover_asset),
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
    response.cover_url = _reading_path_ready_cover_url(reading_path)
    response.access_mode = summary.access_mode
    for orm_entry, response_entry in zip(reading_path.entries, response.entries, strict=False):
        canonical_cover_url = (
            _canonical_issue_cover_url(orm_entry.canonical_issue)
            if orm_entry.canonical_issue is not None
            else None
        )
        if (
            orm_entry.canonical_issue is not None
            and orm_entry.canonical_issue.provider_name
            and canonical_cover_url
        ):
            should_proxy_provider_cover = _should_proxy_provider_cover_url(canonical_cover_url)
            response_entry.cover_url = (
                f"/reading-paths/{reading_path.id}/entries/{orm_entry.id}/cover-image"
                if _remote_cover_fetch_enabled() or should_proxy_provider_cover
                else canonical_cover_url
            )
        elif canonical_cover_url:
            response_entry.cover_url = canonical_cover_url
        else:
            response_entry.cover_url = (
                f"/reading-paths/{reading_path.id}/entries/{orm_entry.id}/cover-image"
                if _remote_cover_fetch_enabled()
                else None
            )
        if response_entry.cover_url is None and orm_entry.entry_type == "collection":
            response_entry.cover_url = response.cover_url
        response_entry.issue_key = _issue_state_key(issue_id=orm_entry.issue_id, canonical_issue_id=orm_entry.canonical_issue_id)
        state = state_map.get(response_entry.issue_key) if response_entry.issue_key is not None else None
        response_entry.is_read = bool(state is not None and state.is_read)
        local_issue = _entry_local_issue(orm_entry)
        if local_issue is not None:
            response_entry.matched_issue = _issue_summary(local_issue)
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


def _collection_cover_response(entry: ReadingPathEntry) -> FileResponse | None:
    """The volume's own cover, used when an issue has none of its own.

    Individual issues rarely carry a cover URL, and the host does not fetch
    remote images, so without this an OPDS reader shows a shelf of blank tiles.
    """
    reading_path = entry.reading_path
    asset = reading_path.cover_asset if reading_path is not None else None
    if asset is None or asset.status != "ready" or not asset.cached_path:
        return None
    cached_path = Path(asset.cached_path)
    if not cached_path.exists():
        return None
    return FileResponse(cached_path, media_type=asset.content_type or "image/jpeg", filename=cached_path.name)


@app.get("/reading-paths/{reading_path_id}/entries/{entry_id}/cover-image")
def get_reading_path_entry_cover_image(
    reading_path_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
) -> FileResponse:
    entry = db.scalars(
        select(ReadingPathEntry)
        .options(
            selectinload(ReadingPathEntry.reading_path).selectinload(ReadingPath.cover_asset),
            selectinload(ReadingPathEntry.canonical_issue).selectinload(CanonicalIssue.series),
            selectinload(ReadingPathEntry.issue).selectinload(Issue.series),
        )
        .where(ReadingPathEntry.id == entry_id, ReadingPathEntry.reading_path_id == reading_path_id)
    ).first()
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Reading path entry {entry_id} not found")

    if entry.canonical_issue is not None and _canonical_issue_cover_url(entry.canonical_issue):
        image_url = _provider_issue_cover_url(entry.canonical_issue) or _canonical_issue_cover_url(entry.canonical_issue)
        if not _remote_cover_fetch_enabled() and not _should_proxy_provider_cover_url(image_url):
            fallback = _collection_cover_response(entry)
            if fallback is not None:
                return fallback
            raise HTTPException(status_code=404, detail="Cover image proxying is disabled")
        referer_source = entry.reading_path.source_url if entry.reading_path is not None else entry.canonical_issue.provider_url
        cached_path, content_type = ensure_remote_cover_image(
            cache_key=_provider_cover_cache_key(image_url),
            image_url=image_url,
            referer_url=_provider_cover_referer(image_url, referer_source),
        )
        if cached_path is None or not cached_path.exists():
            raise HTTPException(status_code=404, detail="Cover image unavailable")
        return FileResponse(cached_path, media_type=content_type, filename=cached_path.name)

    query, expected_series_title, expected_issue_number, expected_year = _reading_path_entry_cover_context(entry)
    if not _remote_cover_fetch_enabled():
        fallback = _collection_cover_response(entry)
        if fallback is not None:
            return fallback
        raise HTTPException(status_code=404, detail="Cover image lookup is disabled")
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
