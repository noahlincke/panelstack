from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import patch

import requests

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.models import (
    Base,
    CanonicalIssue,
    CanonicalIssueSource,
    CanonicalSeries,
    GcdImportState,
    GcdSeriesLookup,
)
from backend.app.services.gcd import (
    GcdThrottledError,
    build_fetcher,
    gcd_credentials,
    iso_week_of,
    run_backfill,
    sync_week,
)

API = "https://www.comics.org/api"

# Shapes copied from live responses on 2026-09-01.
DC_SERIES = f"{API}/series/100/?format=json"
INDIE_SERIES = f"{API}/series/200/?format=json"


def weekly_page(rows: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"count": len(rows), "next": next_url, "previous": None, "results": rows}


def row(issue_id: int, series_url: str, descriptor: str, variant_of: Any = None) -> dict[str, Any]:
    return {
        "api_url": f"{API}/issue/{issue_id}/?format=json",
        "series_name": "Detective Comics (1937 series)",
        "descriptor": descriptor,
        "publication_date": "",
        "price": "",
        "page_count": None,
        "variant_of": variant_of,
        "series": series_url,
    }


class StubFetch:
    """Serves canned GCD payloads and counts calls the way the real fetcher does."""

    def __init__(self, payloads: dict[str, Any], throttle_after: int | None = None) -> None:
        self.payloads = payloads
        self.throttle_after = throttle_after
        self.calls = 0
        self.urls: list[str] = []

    def __call__(self, url: str) -> dict[str, Any]:
        if self.throttle_after is not None and self.calls >= self.throttle_after:
            raise GcdThrottledError(3417)
        self.calls += 1
        self.urls.append(url)
        return self.payloads[url]


def default_payloads() -> dict[str, Any]:
    week_url = f"{API}/issue/on_sale_weekly/2025/week/10/?format=json"
    return {
        week_url: weekly_page(
            [
                row(1, DC_SERIES, "1094"),
                row(2, INDIE_SERIES, "7"),
                row(3, DC_SERIES, "1095"),
                row(4, DC_SERIES, "1094", variant_of=f"{API}/issue/1/?format=json"),
            ]
        ),
        DC_SERIES: {
            "api_url": DC_SERIES,
            "name": "Detective Comics",
            "year_began": 1937,
            "publisher": f"{API}/publisher/54/?format=json",
        },
        INDIE_SERIES: {
            "api_url": INDIE_SERIES,
            "name": "A-DO",
            "year_began": 2024,
            "publisher": f"{API}/publisher/8945/?format=json",
        },
        f"{API}/issue/1/?format=json": {
            "number": "1094",
            "title": "Mercy of the Father",
            "key_date": "2025-02-26",
            "on_sale_date": "2025-02-26",
            "page_count": "24",
        },
        f"{API}/issue/3/?format=json": {
            "number": "1095",
            "title": "",
            "key_date": "2025-03-05",
            "on_sale_date": "2025-03-05",
            "page_count": None,
        },
    }


class GcdSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()

    def tearDown(self) -> None:
        self.db.close()

    def test_only_target_publishers_are_imported(self) -> None:
        fetch = StubFetch(default_payloads())
        result = sync_week(self.db, year=2025, week=10, fetch=fetch)

        self.assertEqual(result.issues_imported, 2)
        titles = {issue.title for issue in self.db.scalars(select(CanonicalIssue))}
        self.assertEqual(titles, {"Mercy of the Father", "Detective Comics #1095"})
        self.assertEqual(self.db.scalar(select(func.count()).select_from(CanonicalSeries)), 1)

    def test_variant_printings_are_skipped(self) -> None:
        fetch = StubFetch(default_payloads())
        sync_week(self.db, year=2025, week=10, fetch=fetch)
        self.assertNotIn(f"{API}/issue/4/?format=json", fetch.urls)

    def test_series_publisher_edge_is_cached_across_rows(self) -> None:
        fetch = StubFetch(default_payloads())
        sync_week(self.db, year=2025, week=10, fetch=fetch)
        # Three DC rows share one series, so it is resolved exactly once.
        self.assertEqual(fetch.urls.count(DC_SERIES), 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(GcdSeriesLookup)), 2)

    def test_provenance_is_recorded_separately_from_identity(self) -> None:
        sync_week(self.db, year=2025, week=10, fetch=StubFetch(default_payloads()))
        sources = list(self.db.scalars(select(CanonicalIssueSource)))
        self.assertEqual(len(sources), 2)
        for source in sources:
            self.assertEqual(source.source_name, "Grand Comics Database")
            self.assertTrue(source.source_url.startswith(API))
            self.assertIsNotNone(source.canonical_issue_id)

    def test_rerunning_a_week_does_not_duplicate_issues(self) -> None:
        sync_week(self.db, year=2025, week=10, fetch=StubFetch(default_payloads()))
        second = sync_week(self.db, year=2025, week=10, fetch=StubFetch(default_payloads()))
        self.assertEqual(second.issues_imported, 0)
        self.assertEqual(second.issues_updated, 2)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(CanonicalIssue)), 2)

    def test_a_rerun_costs_no_issue_requests(self) -> None:
        sync_week(self.db, year=2025, week=10, fetch=StubFetch(default_payloads()))
        fetch = StubFetch(default_payloads())
        sync_week(self.db, year=2025, week=10, fetch=fetch)
        self.assertEqual([url for url in fetch.urls if "/issue/" in url and "on_sale" not in url], [])


class GcdBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()

    def tearDown(self) -> None:
        self.db.close()

    def _payloads_for_weeks(self, weeks: list[tuple[int, int]]) -> dict[str, Any]:
        payloads = default_payloads()
        for year, week in weeks:
            payloads[f"{API}/issue/on_sale_weekly/{year}/week/{week}/?format=json"] = weekly_page([])
        return payloads

    def test_backfill_respects_the_week_budget(self) -> None:
        weeks = [(2025, week) for week in range(1, 12)]
        fetch = StubFetch(self._payloads_for_weeks(weeks))
        result = run_backfill(self.db, start=(2025, 1), end=(2025, 11), fetch=fetch, max_weeks=3)

        self.assertEqual(result.weeks_completed, 3)
        self.assertEqual(result.stopped_at, (2025, 4))
        self.assertEqual(self.db.get(GcdImportState, 1).last_week, 3)

    def test_backfill_resumes_from_the_cursor(self) -> None:
        weeks = [(2025, week) for week in range(1, 12)]
        payloads = self._payloads_for_weeks(weeks)
        run_backfill(self.db, start=(2025, 1), end=(2025, 11), fetch=StubFetch(payloads), max_weeks=3)
        fetch = StubFetch(payloads)
        run_backfill(self.db, start=(2025, 1), end=(2025, 11), fetch=fetch, max_weeks=2)

        self.assertIn(f"{API}/issue/on_sale_weekly/2025/week/4/?format=json", fetch.urls)
        self.assertNotIn(f"{API}/issue/on_sale_weekly/2025/week/1/?format=json", fetch.urls)
        self.assertEqual(self.db.get(GcdImportState, 1).weeks_completed, 5)

    def test_a_throttle_stops_cleanly_and_keeps_the_cursor(self) -> None:
        weeks = [(2025, week) for week in range(1, 12)]
        fetch = StubFetch(self._payloads_for_weeks(weeks), throttle_after=2)
        result = run_backfill(self.db, start=(2025, 1), end=(2025, 11), fetch=fetch, max_weeks=10)

        self.assertEqual(result.throttled_for_seconds, 3417)
        self.assertEqual(result.stopped_at, (2025, 3))
        self.assertEqual(self.db.get(GcdImportState, 1).last_week, 2)

    def test_backfill_respects_the_request_budget(self) -> None:
        weeks = [(2025, week) for week in range(1, 12)]
        fetch = StubFetch(self._payloads_for_weeks(weeks))
        result = run_backfill(self.db, start=(2025, 1), end=(2025, 11), fetch=fetch, max_requests=4)

        self.assertLessEqual(result.requests_made, 5)
        self.assertIsNotNone(result.stopped_at)


class IsoWeekTests(unittest.TestCase):
    def test_iso_week_matches_the_on_sale_week_url(self) -> None:
        from datetime import date

        self.assertEqual(iso_week_of(date(2025, 3, 5)), (2025, 10))


if __name__ == "__main__":
    unittest.main()


class CredentialTests(unittest.TestCase):
    """Anonymous GCD allows about thirty requests an hour, so a login matters."""

    def test_no_credentials_means_anonymous(self) -> None:
        with patch.dict(os.environ, {"GCD_USERNAME": "", "GCD_PASSWORD": ""}):
            self.assertIsNone(gcd_credentials())

    def test_a_username_without_a_password_is_not_used(self) -> None:
        with patch.dict(os.environ, {"GCD_USERNAME": "reader", "GCD_PASSWORD": ""}):
            self.assertIsNone(gcd_credentials())

    def test_the_fetcher_authenticates_when_configured(self) -> None:
        session = requests.Session()
        with patch.dict(os.environ, {"GCD_USERNAME": "reader", "GCD_PASSWORD": "secret"}):
            build_fetcher(session)
        self.assertEqual(session.auth, ("reader", "secret"))

    def test_an_explicit_session_auth_is_left_alone(self) -> None:
        session = requests.Session()
        session.auth = ("someone", "else")
        with patch.dict(os.environ, {"GCD_USERNAME": "reader", "GCD_PASSWORD": "secret"}):
            build_fetcher(session)
        self.assertEqual(session.auth, ("someone", "else"))
