from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import re
from typing import Any, Callable

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CanonicalIssue, CanonicalIssueSource, CanonicalSeries, Publisher
from .library import issue_sort_order, slugify

GCD_API_ROOT = "https://www.comics.org/api"
SOURCE_NAME = "Grand Comics Database"
PUBLISHER_SLUGS = {"DC Comics": "dc", "Marvel": "marvel", "Marvel Comics": "marvel"}


@dataclass(frozen=True)
class GcdSyncResult:
    issues_seen: int = 0
    issues_synced: int = 0
    series_synced: int = 0


def _fetch_json(url: str, get: Callable[..., requests.Response]) -> dict[str, Any]:
    response = get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def _all_results(url: str, get: Callable[..., requests.Response]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    next_url: str | None = url
    while next_url:
        payload = _fetch_json(next_url, get)
        results.extend(payload.get("results", []))
        next_url = payload.get("next")
    return results


def _source_id(url: str) -> str:
    match = re.search(r"/(?:issue|series)/(\d+)/", url)
    if match is None:
        raise ValueError(f"Unexpected GCD API URL: {url}")
    return match.group(1)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _publisher(db: Session, name: str) -> Publisher:
    publisher = db.scalar(select(Publisher).where(Publisher.slug == PUBLISHER_SLUGS[name]))
    if publisher is None:
        publisher = Publisher(slug=PUBLISHER_SLUGS[name], name=name)
        db.add(publisher)
        db.flush()
    return publisher


def _series(db: Session, payload: dict[str, Any], publisher: Publisher) -> tuple[CanonicalSeries, bool]:
    title, start_year = str(payload["name"]), payload.get("year_began")
    series = db.scalar(select(CanonicalSeries).where(CanonicalSeries.publisher_id == publisher.id, CanonicalSeries.title == title, CanonicalSeries.start_year == start_year))
    if series is not None:
        return series, False
    series = CanonicalSeries(slug=f"gcd-{_source_id(str(payload['api_url']))}-{slugify(title)}", title=title, publisher_id=publisher.id, start_year=start_year, end_year=payload.get("year_ended"))
    db.add(series)
    db.flush()
    return series, True


def sync_gcd_on_sale_week(db: Session, *, year: int, week: int, get: Callable[..., requests.Response] = requests.get) -> GcdSyncResult:
    """Import only DC/Marvel records exposed by one public GCD on-sale week."""
    series_cache: dict[str, dict[str, Any]] = {}
    publisher_cache: dict[str, dict[str, Any]] = {}
    synced_issues = synced_series = seen = 0
    week_url = f"{GCD_API_ROOT}/issue/on_sale_weekly/{year}/week/{week}/?format=json"
    for weekly_issue in _all_results(week_url, get):
        series_url = str(weekly_issue["series"])
        series_payload = series_cache.setdefault(series_url, _fetch_json(series_url, get))
        publisher_url = str(series_payload["publisher"])
        publisher_payload = publisher_cache.setdefault(publisher_url, _fetch_json(publisher_url, get))
        publisher_name = str(publisher_payload["name"])
        if publisher_name not in PUBLISHER_SLUGS:
            continue
        seen += 1
        series, was_created = _series(db, series_payload, _publisher(db, publisher_name))
        synced_series += int(was_created)
        issue_url = str(weekly_issue["api_url"])
        issue_payload = _fetch_json(issue_url, get)
        issue_number = str(issue_payload["number"])
        issue = db.scalar(select(CanonicalIssue).where(CanonicalIssue.series_id == series.id, CanonicalIssue.issue_number == issue_number))
        if issue is None:
            issue = CanonicalIssue(series_id=series.id, legacy_key=f"{series.slug}#{issue_number}", issue_number=issue_number, issue_kind="issue", title=issue_payload.get("title") or issue_payload.get("descriptor"), sort_order=issue_sort_order(issue_number), published_on=_parse_date(issue_payload.get("key_date")), summary=issue_payload.get("notes"), cover_url=issue_payload.get("cover") or None, page_count=int(float(issue_payload["page_count"])) if issue_payload.get("page_count") else None)
            db.add(issue)
            db.flush()
            synced_issues += 1
        source_issue_id = _source_id(issue_url)
        source = db.scalar(select(CanonicalIssueSource).where(CanonicalIssueSource.source_name == SOURCE_NAME, CanonicalIssueSource.source_issue_id == source_issue_id))
        if source is None:
            db.add(CanonicalIssueSource(canonical_issue_id=issue.id, source_name=SOURCE_NAME, source_issue_id=source_issue_id, source_url=issue_url, last_seen_at=datetime.now(timezone.utc)))
    db.commit()
    return GcdSyncResult(issues_seen=seen, issues_synced=synced_issues, series_synced=synced_series)
