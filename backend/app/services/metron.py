"""Import real issue data from Metron, the open comic database.

Why Metron rather than the Grand Comics Database: measured on 2026-09-07,
anonymous GCD allows about thirty requests an hour and supports no filtering at
all, which puts a 2019-2026 backfill in the hundreds of hours. Metron answers
both problems.

What the API actually does, verified against metron.cloud on 2026-09-10:

- HTTP Basic auth against a free account. The credential is the *username*, not
  the email address the account was registered with -- an email is rejected with
  ``{"detail": "Invalid username/password."}``.
- Documented limits are 20 requests/minute and 5,000/day, so requests are spaced
  just over three seconds apart. Exceeding them appears to block the caller's IP
  outright rather than returning 429, so the limiter is not optional.
- Filtering works and is the whole point. ``/api/series/?name=X&year_began=Y``
  finds one series; ``/api/issue/?series_id=N`` lists its issues 100 to a page.
- An issue record carries ``number``, ``cover_date``, ``store_date`` and an
  ``image`` cover URL, which is everything the catalogue was guessing at.

The scope is deliberately narrow: only the series this catalogue already curates
are looked up, roughly 120 of them, which is a few hundred requests rather than
the ~13,000 issues a whole-publisher sweep would pull in and never show. Metron
data corrects and extends canonical issues; it never becomes the catalogue's
identity, which stays keyed on (publisher, title, start year).
"""
from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CanonicalIssue,
    CanonicalIssueSource,
    CanonicalSeries,
    Publisher,
)
from .library import issue_sort_order

METRON_API_ROOT = "https://metron.cloud/api"
SOURCE_NAME = "Metron"
USER_AGENT = "panelstack/1.0 (personal comic library; +https://noah.lincke.org/panels)"

# 20 requests/minute is the documented ceiling; three seconds keeps us under it
# with room for clock drift.
REQUEST_INTERVAL_SECONDS = 3.2

# Metron's own publisher ids, confirmed against /api/publisher/?name=.
PUBLISHER_IDS = {"dc": 2, "marvel": 1}

Fetch = Callable[[str], dict[str, Any]]


class MetronAuthError(RuntimeError):
    """Raised when Metron rejects the configured credentials."""


class MetronThrottledError(RuntimeError):
    """Raised when Metron asks us to back off."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"Metron throttled the client; retry in {retry_after_seconds}s.")
        self.retry_after_seconds = retry_after_seconds


@dataclass
class SeriesResult:
    """What one curated series' import did."""

    title: str
    start_year: int | None
    metron_series_id: int | None = None
    issues_created: int = 0
    issues_updated: int = 0
    dates_corrected: int = 0
    covers_added: int = 0
    # Issues we hold that Metron has no record of, almost always the monthly
    # extrapolation that filled the catalogue out to the present.
    unmatched_issue_numbers: list[str] = field(default_factory=list)
    requests_made: int = 0


@dataclass
class BackfillResult:
    series: list[SeriesResult] = field(default_factory=list)
    requests_made: int = 0
    stopped_early: bool = False

    @property
    def issues_created(self) -> int:
        return sum(result.issues_created for result in self.series)

    @property
    def dates_corrected(self) -> int:
        return sum(result.dates_corrected for result in self.series)

    @property
    def covers_added(self) -> int:
        return sum(result.covers_added for result in self.series)


def metron_credentials() -> tuple[str, str] | None:
    """The configured Metron login, if there is one.

    Metron authenticates on the username. An account registered with an email
    address still logs in as the part before the "@", so that normalisation is
    applied rather than failing with an unhelpful 401.
    """
    username = os.getenv("METRON_USERNAME", "").strip()
    password = os.getenv("METRON_PASSWORD", "")
    if not username or not password:
        return None
    return username.split("@", 1)[0], password


def build_fetcher(
    session: requests.Session | None = None, *, interval: float = REQUEST_INTERVAL_SECONDS
) -> Fetch:
    """A rate-limited JSON fetcher that counts the requests it spends."""
    credentials = metron_credentials()
    if credentials is None:
        raise MetronAuthError("Set METRON_USERNAME and METRON_PASSWORD to import from Metron.")

    http = session or requests.Session()
    http.headers.setdefault("User-Agent", USER_AGENT)
    if http.auth is None:
        http.auth = credentials
    last_call = [0.0]

    def fetch(url: str) -> dict[str, Any]:
        wait = interval - (time.monotonic() - last_call[0])
        if wait > 0:
            time.sleep(wait)
        response = http.get(url, timeout=45)
        last_call[0] = time.monotonic()
        if response.status_code == 401:
            raise MetronAuthError(
                "Metron rejected the credentials. The username is the account name, not the email."
            )
        if response.status_code == 429:
            raise MetronThrottledError(int(response.headers.get("Retry-After", "60")))
        response.raise_for_status()
        fetch.calls += 1  # type: ignore[attr-defined]
        return response.json()

    fetch.calls = 0  # type: ignore[attr-defined]
    return fetch


def _paginate(url: str, fetch: Fetch) -> Iterator[dict[str, Any]]:
    while url:
        payload = fetch(url)
        yield from payload.get("results", [])
        url = payload.get("next") or ""


def find_series_id(title: str, start_year: int | None, fetch: Fetch) -> int | None:
    """Metron's id for one of our series, matched on title and first year."""
    query = f"{METRON_API_ROOT}/series/?name={requests.utils.quote(title)}"
    if start_year is not None:
        query += f"&year_began={start_year}"
    results = fetch(query).get("results", [])
    if not results:
        return None
    # A name search is a contains-match, so prefer the exact title before
    # falling back to whatever came first.
    for row in results:
        if str(row.get("series", "")).rsplit(" (", 1)[0].strip().lower() == title.strip().lower():
            return int(row["id"])
    return int(results[0]["id"])


def iter_series_issues(metron_series_id: int, fetch: Fetch) -> Iterator[dict[str, Any]]:
    yield from _paginate(f"{METRON_API_ROOT}/issue/?series_id={metron_series_id}", fetch)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalize_number(value: Any) -> str:
    return str(value or "").strip()


def sync_series(db: Session, series: CanonicalSeries, fetch: Fetch) -> SeriesResult:
    """Correct and extend one curated series from Metron.

    Existing issues keep their identity; only the facts Metron is authoritative
    about are written. Issues we hold that Metron does not know are reported
    rather than deleted -- they are usually the monthly extrapolation, but that
    is the reader's call to make, not this script's.
    """
    before = getattr(fetch, "calls", 0)
    result = SeriesResult(title=series.title, start_year=series.start_year)

    metron_series_id = find_series_id(series.title, series.start_year, fetch)
    result.metron_series_id = metron_series_id
    if metron_series_id is None:
        result.requests_made = getattr(fetch, "calls", 0) - before
        return result

    existing = {
        _normalize_number(issue.issue_number): issue
        for issue in db.scalars(select(CanonicalIssue).where(CanonicalIssue.series_id == series.id))
    }
    seen: set[str] = set()

    for row in iter_series_issues(metron_series_id, fetch):
        number = _normalize_number(row.get("number"))
        if not number:
            continue
        seen.add(number)
        # Metron's cover_date is the on-book month; store_date is when it
        # actually reached shops. The catalogue reads as a publication timeline,
        # so the cover date is the one that belongs there.
        published_on = _parse_date(row.get("cover_date")) or _parse_date(row.get("store_date"))
        image = row.get("image") or None

        issue = existing.get(number)
        if issue is None:
            issue = CanonicalIssue(
                series_id=series.id,
                legacy_key=f"{series.slug}#{number}",
                issue_number=number,
                issue_kind="issue",
                title=row.get("issue") or f"{series.title} #{number}",
                sort_order=issue_sort_order(number),
                published_on=published_on,
                cover_url=image,
            )
            db.add(issue)
            db.flush()
            existing[number] = issue
            result.issues_created += 1
        else:
            if published_on and issue.published_on != published_on:
                issue.published_on = published_on
                result.dates_corrected += 1
            if image and not issue.cover_url:
                issue.cover_url = image
                result.covers_added += 1
            result.issues_updated += 1

        source_issue_id = str(row["id"])
        source = db.scalar(
            select(CanonicalIssueSource).where(
                CanonicalIssueSource.source_name == SOURCE_NAME,
                CanonicalIssueSource.source_issue_id == source_issue_id,
            )
        )
        if source is None:
            db.add(
                CanonicalIssueSource(
                    canonical_issue_id=issue.id,
                    source_name=SOURCE_NAME,
                    source_issue_id=source_issue_id,
                    source_url=f"{METRON_API_ROOT}/issue/{source_issue_id}/",
                    last_seen_at=datetime.now(timezone.utc),
                )
            )
        else:
            source.last_seen_at = datetime.now(timezone.utc)

    result.unmatched_issue_numbers = sorted(
        (number for number in existing if number and number not in seen and "-" not in number),
        key=issue_sort_order,
    )
    db.commit()
    result.requests_made = getattr(fetch, "calls", 0) - before
    return result


def curated_series(db: Session, publisher_slugs: tuple[str, ...] = ("dc", "marvel")) -> list[CanonicalSeries]:
    """The series this catalogue actually shows, which is all we import."""
    return list(
        db.scalars(
            select(CanonicalSeries)
            .join(Publisher, Publisher.id == CanonicalSeries.publisher_id)
            .where(Publisher.slug.in_(publisher_slugs))
            .order_by(CanonicalSeries.title.asc())
        )
    )


def run_backfill(
    db: Session,
    *,
    fetch: Fetch,
    publisher_slugs: tuple[str, ...] = ("dc", "marvel"),
    max_requests: int | None = None,
    on_series: Callable[[SeriesResult], None] | None = None,
) -> BackfillResult:
    """Walk the curated series, stopping cleanly when the budget runs out."""
    started = getattr(fetch, "calls", 0)
    outcome = BackfillResult()

    for series in curated_series(db, publisher_slugs):
        if max_requests is not None and getattr(fetch, "calls", 0) - started >= max_requests:
            outcome.stopped_early = True
            break
        try:
            result = sync_series(db, series, fetch)
        except MetronThrottledError:
            db.rollback()
            outcome.stopped_early = True
            break
        outcome.series.append(result)
        if on_series is not None:
            on_series(result)

    outcome.requests_made = getattr(fetch, "calls", 0) - started
    return outcome
