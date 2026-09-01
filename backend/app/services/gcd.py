"""Bounded, resumable import from the Grand Comics Database public API.

What the API actually does, verified against www.comics.org:

- No account or key is required.
- ``/api/issue/on_sale_weekly/{year}/week/{week}/`` is the only date-scoped entry
  point. It pages 50 at a time and a normal week holds ~370 issues across every
  publisher in the database.
- Nothing supports filtering. ``/api/series/?publisher=54`` ignores the parameter
  and returns all 232k series, so the publisher of a weekly row can only be
  learned by following its ``series`` link.
- Anonymous use is throttled hard. Measured on 2026-09-01: roughly fifty requests
  in a few minutes returned ``429`` with ``Retry-After: 3417``, i.e. an hourly
  window. Every run is therefore request-budgeted and a throttle is a clean stop
  that keeps the cursor, not an error.
- A weekly row carries ``series``, ``series_name`` and ``descriptor`` but no
  publisher and no dates worth trusting.

That shape drives the design: page through one week, resolve each *unseen* series
once and cache the series->publisher edge, then fetch full issue records only for
the handful of rows that turned out to be DC or Marvel. A week costs roughly
8 list requests plus ~30 issue requests once the series cache is warm, which is
what makes both the local backfill and the weekly reconciliation quota-safe.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CanonicalIssue,
    CanonicalIssueSource,
    CanonicalSeries,
    GcdImportState,
    GcdSeriesLookup,
    Publisher,
)
from .library import issue_sort_order, slugify

GCD_API_ROOT = "https://www.comics.org/api"
SOURCE_NAME = "Grand Comics Database"
USER_AGENT = "panelstack/1.0 (personal comic library; +https://noah.lincke.org/panels)"

# Verified against /api/publisher/{id}/: 54 is DC, 78 is Marvel.
TARGET_PUBLISHERS = {"54": ("dc", "DC Comics"), "78": ("marvel", "Marvel")}

# The API is a volunteer-run service, so requests are spaced out.
REQUEST_INTERVAL_SECONDS = 0.7

Fetch = Callable[[str], dict[str, Any]]


class GcdThrottledError(RuntimeError):
    """Raised when GCD asks us to back off. Carries the server's own wait."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"GCD throttled the client; retry in {retry_after_seconds}s.")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class WeekResult:
    year: int
    week: int
    rows_scanned: int = 0
    series_resolved: int = 0
    issues_imported: int = 0
    issues_updated: int = 0
    requests_made: int = 0


@dataclass(frozen=True)
class BackfillResult:
    weeks_completed: int
    issues_imported: int
    requests_made: int
    stopped_at: tuple[int, int] | None
    throttled_for_seconds: int | None = None


def _identifier(url: str, kind: str) -> str:
    match = re.search(rf"/{kind}/(\d+)/", url)
    if match is None:
        raise ValueError(f"Unexpected GCD {kind} URL: {url}")
    return match.group(1)


def build_fetcher(session: requests.Session | None = None, *, interval: float = REQUEST_INTERVAL_SECONDS) -> Fetch:
    """A polite JSON fetcher that also counts what it used."""
    http = session or requests.Session()
    http.headers.setdefault("User-Agent", USER_AGENT)
    last_call = [0.0]

    def fetch(url: str) -> dict[str, Any]:
        wait = interval - (time.monotonic() - last_call[0])
        if wait > 0:
            time.sleep(wait)
        response = http.get(url, timeout=30)
        last_call[0] = time.monotonic()
        if response.status_code == 429:
            raise GcdThrottledError(int(response.headers.get("Retry-After", "3600")))
        response.raise_for_status()
        fetch.calls += 1  # type: ignore[attr-defined]
        return response.json()

    fetch.calls = 0  # type: ignore[attr-defined]
    return fetch


def iter_week_rows(year: int, week: int, fetch: Fetch) -> Iterator[dict[str, Any]]:
    url = f"{GCD_API_ROOT}/issue/on_sale_weekly/{year}/week/{week}/?format=json"
    while url:
        payload = fetch(url)
        yield from payload.get("results", [])
        url = payload.get("next")


def _series_lookup(db: Session, series_url: str, fetch: Fetch) -> GcdSeriesLookup:
    series_id = _identifier(series_url, "series")
    cached = db.get(GcdSeriesLookup, series_id)
    if cached is not None:
        return cached
    payload = fetch(series_url)
    lookup = GcdSeriesLookup(
        gcd_series_id=series_id,
        gcd_publisher_id=_identifier(str(payload["publisher"]), "publisher"),
        name=str(payload["name"]),
        year_began=payload.get("year_began"),
    )
    db.add(lookup)
    # The series->publisher edge is the expensive thing to acquire, so it is
    # committed immediately and survives a throttle mid-week.
    db.commit()
    return lookup


def _publisher(db: Session, gcd_publisher_id: str) -> Publisher:
    slug, name = TARGET_PUBLISHERS[gcd_publisher_id]
    publisher = db.scalar(select(Publisher).where(Publisher.slug == slug))
    if publisher is None:
        publisher = Publisher(slug=slug, name=name)
        db.add(publisher)
        db.flush()
    return publisher


def _canonical_series(db: Session, lookup: GcdSeriesLookup, publisher: Publisher) -> CanonicalSeries:
    series = db.scalar(
        select(CanonicalSeries).where(
            CanonicalSeries.publisher_id == publisher.id,
            CanonicalSeries.title == lookup.name,
            CanonicalSeries.start_year == lookup.year_began,
        )
    )
    if series is not None:
        return series
    series = CanonicalSeries(
        slug=f"gcd-{lookup.gcd_series_id}-{slugify(lookup.name)}",
        title=lookup.name,
        publisher_id=publisher.id,
        start_year=lookup.year_began,
    )
    db.add(series)
    db.flush()
    return series


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _page_count(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def sync_week(db: Session, *, year: int, week: int, fetch: Fetch) -> WeekResult:
    """Import one on-sale week's DC and Marvel issues."""
    before = getattr(fetch, "calls", 0)
    scanned = resolved = imported = updated = 0

    for row in iter_week_rows(year, week, fetch):
        scanned += 1
        if row.get("variant_of"):
            continue
        lookup = _series_lookup(db, str(row["series"]), fetch)
        resolved += 1
        if lookup.gcd_publisher_id not in TARGET_PUBLISHERS:
            continue

        issue_url = str(row["api_url"])
        source_issue_id = _identifier(issue_url, "issue")
        source = db.scalar(
            select(CanonicalIssueSource).where(
                CanonicalIssueSource.source_name == SOURCE_NAME,
                CanonicalIssueSource.source_issue_id == source_issue_id,
            )
        )
        if source is not None:
            source.last_seen_at = datetime.now(timezone.utc)
            updated += 1
            continue

        payload = fetch(issue_url)
        publisher = _publisher(db, lookup.gcd_publisher_id)
        series = _canonical_series(db, lookup, publisher)
        issue_number = str(payload.get("number") or row.get("descriptor") or "").strip()
        if not issue_number:
            continue

        issue = db.scalar(
            select(CanonicalIssue).where(
                CanonicalIssue.series_id == series.id,
                CanonicalIssue.issue_number == issue_number,
            )
        )
        if issue is None:
            issue = CanonicalIssue(
                series_id=series.id,
                legacy_key=f"{series.slug}#{issue_number}",
                issue_number=issue_number,
                issue_kind="issue",
                title=payload.get("title") or f"{lookup.name} #{issue_number}",
                sort_order=issue_sort_order(issue_number),
                published_on=_parse_date(payload.get("key_date")) or _parse_date(payload.get("on_sale_date")),
                page_count=_page_count(payload.get("page_count")),
            )
            db.add(issue)
            db.flush()
            imported += 1

        db.add(
            CanonicalIssueSource(
                canonical_issue_id=issue.id,
                source_name=SOURCE_NAME,
                source_issue_id=source_issue_id,
                source_url=issue_url,
                last_seen_at=datetime.now(timezone.utc),
            )
        )

    db.commit()
    return WeekResult(
        year=year,
        week=week,
        rows_scanned=scanned,
        series_resolved=resolved,
        issues_imported=imported,
        issues_updated=updated,
        requests_made=getattr(fetch, "calls", 0) - before,
    )


def _import_state(db: Session) -> GcdImportState:
    state = db.get(GcdImportState, 1)
    if state is None:
        state = GcdImportState(id=1)
        db.add(state)
        db.flush()
    return state


def _next_week(year: int, week: int) -> tuple[int, int]:
    return (year, week + 1) if week < 52 else (year + 1, 1)


def iso_week_of(day: date) -> tuple[int, int]:
    iso = day.isocalendar()
    return iso.year, iso.week


def run_backfill(
    db: Session,
    *,
    start: tuple[int, int],
    end: tuple[int, int],
    fetch: Fetch,
    max_weeks: int | None = None,
    max_requests: int | None = None,
) -> BackfillResult:
    """Walk weeks forward from the cursor, stopping on either budget.

    Both budgets exist so a run is always bounded: a local backfill can take a
    large ``max_weeks``, while scheduled reconciliation takes a small one.
    """
    state = _import_state(db)
    if state.last_year and state.last_week:
        year, week = _next_week(state.last_year, state.last_week)
    else:
        year, week = start

    weeks = imported = 0
    started_calls = getattr(fetch, "calls", 0)
    while (year, week) <= end:
        if max_weeks is not None and weeks >= max_weeks:
            return BackfillResult(weeks, imported, getattr(fetch, "calls", 0) - started_calls, (year, week))
        if max_requests is not None and getattr(fetch, "calls", 0) - started_calls >= max_requests:
            return BackfillResult(weeks, imported, getattr(fetch, "calls", 0) - started_calls, (year, week))

        try:
            result = sync_week(db, year=year, week=week, fetch=fetch)
        except GcdThrottledError as throttle:
            db.commit()
            return BackfillResult(
                weeks,
                imported,
                getattr(fetch, "calls", 0) - started_calls,
                (year, week),
                throttle.retry_after_seconds,
            )
        imported += result.issues_imported
        weeks += 1
        state.last_year, state.last_week = year, week
        state.weeks_completed += 1
        db.commit()
        year, week = _next_week(year, week)

    return BackfillResult(weeks, imported, getattr(fetch, "calls", 0) - started_calls, None)


def reconcile_recent_weeks(db: Session, *, weeks_back: int = 2, fetch: Fetch | None = None) -> list[WeekResult]:
    """Re-scan the last few on-sale weeks so late GCD edits get picked up."""
    fetch = fetch or build_fetcher()
    today = datetime.now(timezone.utc).date()
    results = []
    for offset in range(weeks_back, -1, -1):
        year, week = iso_week_of(today - timedelta(weeks=offset))
        results.append(sync_week(db, year=year, week=week, fetch=fetch))
    return results
