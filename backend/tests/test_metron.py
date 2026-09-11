from __future__ import annotations

import os
import unittest
from datetime import date
from typing import Any
from unittest.mock import patch

import requests
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.models import (
    Base,
    CanonicalIssue,
    CanonicalIssueSource,
    CanonicalSeries,
    Publisher,
)
from backend.app.services.metron import (
    METRON_API_ROOT,
    MetronAuthError,
    MetronThrottledError,
    build_fetcher,
    curated_series,
    find_series_id,
    metron_credentials,
    run_backfill,
    sync_series,
)


class CredentialTests(unittest.TestCase):
    """Metron authenticates on the account username, not the email address."""

    def test_an_email_is_reduced_to_the_username(self) -> None:
        with patch.dict(os.environ, {"METRON_USERNAME": "reader@example.com", "METRON_PASSWORD": "s"}):
            self.assertEqual(metron_credentials(), ("reader", "s"))

    def test_a_plain_username_is_left_alone(self) -> None:
        with patch.dict(os.environ, {"METRON_USERNAME": "reader", "METRON_PASSWORD": "s"}):
            self.assertEqual(metron_credentials(), ("reader", "s"))

    def test_a_half_configured_login_is_no_login(self) -> None:
        with patch.dict(os.environ, {"METRON_USERNAME": "reader", "METRON_PASSWORD": ""}):
            self.assertIsNone(metron_credentials())
        with patch.dict(os.environ, {"METRON_USERNAME": "", "METRON_PASSWORD": "s"}):
            self.assertIsNone(metron_credentials())

    def test_building_a_fetcher_without_credentials_says_so(self) -> None:
        with patch.dict(os.environ, {"METRON_USERNAME": "", "METRON_PASSWORD": ""}):
            with self.assertRaises(MetronAuthError):
                build_fetcher()


class FetcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(os.environ, {"METRON_USERNAME": "reader", "METRON_PASSWORD": "secret"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()

    def _session(self, status_code: int, headers: dict[str, str] | None = None) -> requests.Session:
        session = requests.Session()

        class Response:
            def __init__(self) -> None:
                self.status_code = status_code
                self.headers = headers or {}

            def json(self) -> dict[str, Any]:
                return {"results": []}

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise requests.HTTPError(str(self.status_code))

        session.get = lambda *args, **kwargs: Response()  # type: ignore[method-assign]
        return session

    def test_the_session_is_authenticated(self) -> None:
        session = self._session(200)
        build_fetcher(session, interval=0)
        self.assertEqual(session.auth, ("reader", "secret"))

    def test_a_401_explains_the_username_rule(self) -> None:
        fetch = build_fetcher(self._session(401), interval=0)
        with self.assertRaises(MetronAuthError) as raised:
            fetch("https://metron.cloud/api/publisher/")
        self.assertIn("username", str(raised.exception))

    def test_a_429_carries_the_servers_own_wait(self) -> None:
        fetch = build_fetcher(self._session(429, {"Retry-After": "90"}), interval=0)
        with self.assertRaises(MetronThrottledError) as raised:
            fetch("https://metron.cloud/api/publisher/")
        self.assertEqual(raised.exception.retry_after_seconds, 90)

    def test_requests_are_counted(self) -> None:
        fetch = build_fetcher(self._session(200), interval=0)
        fetch("https://metron.cloud/api/publisher/")
        fetch("https://metron.cloud/api/publisher/")
        self.assertEqual(fetch.calls, 2)


class FakeApi:
    """Just enough of Metron to drive the importer."""

    def __init__(self, series: list[dict[str, Any]], issues: dict[int, list[dict[str, Any]]], page_size: int = 100) -> None:
        self.series = series
        self.issues = issues
        self.page_size = page_size
        self.calls = 0

    def __call__(self, url: str) -> dict[str, Any]:
        self.calls += 1
        if "/series/" in url:
            return {"count": len(self.series), "next": None, "results": self.series}
        series_id = int(url.split("series_id=")[1].split("&")[0].split("?")[0])
        rows = self.issues.get(series_id, [])
        offset = int(url.split("offset=")[1].split("&")[0]) if "offset=" in url else 0
        page = rows[offset : offset + self.page_size]
        next_offset = offset + self.page_size
        return {
            "count": len(rows),
            "next": f"{METRON_API_ROOT}/issue/?series_id={series_id}&offset={next_offset}" if next_offset < len(rows) else None,
            "results": page,
        }


class SyncSeriesTests(unittest.TestCase):
    """Metron corrects and extends the catalogue; it never renames it."""

    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()
        publisher = Publisher(slug="dc", name="DC Comics")
        self.db.add(publisher)
        self.db.flush()
        self.series = CanonicalSeries(
            slug="absolute-batman-2024", title="Absolute Batman", publisher_id=publisher.id, start_year=2024
        )
        self.db.add(self.series)
        self.db.flush()
        # Two real issues with guessed monthly dates, plus one that never shipped.
        for number, published_on in (("1", date(2024, 10, 1)), ("2", date(2024, 11, 1)), ("99", date(2026, 9, 1))):
            self.db.add(
                CanonicalIssue(
                    series_id=self.series.id,
                    legacy_key=f"absolute-batman-2024#{number}",
                    issue_number=number,
                    issue_kind="issue",
                    title=f"Absolute Batman #{number}",
                    sort_order=int(number),
                    published_on=published_on,
                )
            )
        self.db.commit()

        self.api = FakeApi(
            series=[{"id": 8477, "series": "Absolute Batman (2024)", "issue_count": 3}],
            issues={
                8477: [
                    {"id": 1, "number": "1", "issue": "Absolute Batman (2024) #1", "cover_date": "2024-12-01", "store_date": "2024-10-09", "image": "https://static.metron.cloud/1.jpg"},
                    {"id": 2, "number": "2", "issue": "Absolute Batman (2024) #2", "cover_date": "2025-01-01", "store_date": "2024-11-13", "image": None},
                    {"id": 3, "number": "3", "issue": "Absolute Batman (2024) #3", "cover_date": "2025-02-01", "store_date": "2024-12-18", "image": "https://static.metron.cloud/3.jpg"},
                ]
            },
        )

    def tearDown(self) -> None:
        self.db.close()

    def test_a_guessed_date_is_replaced_with_the_real_one(self) -> None:
        result = sync_series(self.db, self.series, self.api)
        issue = self.db.scalar(
            select(CanonicalIssue).where(CanonicalIssue.series_id == self.series.id, CanonicalIssue.issue_number == "1")
        )
        self.assertEqual(issue.published_on, date(2024, 12, 1))
        self.assertEqual(result.dates_corrected, 2)

    def test_an_issue_the_catalogue_lacks_is_created(self) -> None:
        result = sync_series(self.db, self.series, self.api)
        self.assertEqual(result.issues_created, 1)
        issue = self.db.scalar(
            select(CanonicalIssue).where(CanonicalIssue.series_id == self.series.id, CanonicalIssue.issue_number == "3")
        )
        self.assertEqual(issue.published_on, date(2025, 2, 1))
        self.assertEqual(issue.cover_url, "https://static.metron.cloud/3.jpg")

    def test_a_cover_is_filled_in_but_never_overwritten(self) -> None:
        existing = self.db.scalar(
            select(CanonicalIssue).where(CanonicalIssue.series_id == self.series.id, CanonicalIssue.issue_number == "2")
        )
        existing.cover_url = "https://getcomics.test/keepme.jpg"
        self.db.commit()
        sync_series(self.db, self.series, self.api)
        self.db.refresh(existing)
        self.assertEqual(existing.cover_url, "https://getcomics.test/keepme.jpg")

    def test_an_extrapolated_issue_is_reported_not_deleted(self) -> None:
        result = sync_series(self.db, self.series, self.api)
        self.assertEqual(result.unmatched_issue_numbers, ["99"])
        survivor = self.db.scalar(
            select(CanonicalIssue).where(CanonicalIssue.series_id == self.series.id, CanonicalIssue.issue_number == "99")
        )
        self.assertIsNotNone(survivor, "deleting is the reader's call, not the importer's")

    def test_provenance_is_recorded_separately_from_identity(self) -> None:
        sync_series(self.db, self.series, self.api)
        sources = list(self.db.scalars(select(CanonicalIssueSource)))
        self.assertEqual(len(sources), 3)
        self.assertEqual({source.source_name for source in sources}, {"Metron"})
        # The series keeps the catalogue's own slug and title.
        self.db.refresh(self.series)
        self.assertEqual(self.series.slug, "absolute-batman-2024")

    def test_running_twice_creates_nothing_the_second_time(self) -> None:
        sync_series(self.db, self.series, self.api)
        second = sync_series(self.db, self.series, self.api)
        self.assertEqual(second.issues_created, 0)
        self.assertEqual(second.dates_corrected, 0)
        self.assertEqual(len(list(self.db.scalars(select(CanonicalIssueSource)))), 3)

    def test_a_series_metron_does_not_have_is_reported_not_fatal(self) -> None:
        api = FakeApi(series=[], issues={})
        result = sync_series(self.db, self.series, api)
        self.assertIsNone(result.metron_series_id)
        self.assertEqual(result.issues_created, 0)

    def test_every_page_of_a_long_run_is_read(self) -> None:
        rows = [
            {"id": 1000 + n, "number": str(n), "issue": f"Long Run #{n}", "cover_date": "2020-01-01", "store_date": None, "image": None}
            for n in range(1, 251)
        ]
        api = FakeApi(series=[{"id": 42, "series": "Absolute Batman (2024)"}], issues={42: rows}, page_size=100)
        result = sync_series(self.db, self.series, api)
        # #1, #2 and #99 already exist in the fixture, so 250 - 3 are new.
        self.assertEqual(result.issues_created, 247)
        self.assertEqual(result.issues_updated, 3)


class SeriesLookupTests(unittest.TestCase):
    def test_an_exact_title_wins_over_a_longer_contains_match(self) -> None:
        api = FakeApi(
            series=[
                {"id": 1, "series": "Absolute Batman and Robin (2026)"},
                {"id": 2, "series": "Absolute Batman (2024)"},
            ],
            issues={},
        )
        self.assertEqual(find_series_id("Absolute Batman", 2024, api), 2)

    def test_the_first_result_is_used_when_nothing_matches_exactly(self) -> None:
        api = FakeApi(series=[{"id": 7, "series": "Batman Beyond (2016)"}], issues={})
        self.assertEqual(find_series_id("Batman Beyond Special", 2016, api), 7)

    def test_no_results_means_no_series(self) -> None:
        self.assertIsNone(find_series_id("Nothing At All", 1999, FakeApi(series=[], issues={})))


class BackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()
        dc = Publisher(slug="dc", name="DC Comics")
        manga = Publisher(slug="shueisha", name="Shueisha")
        self.db.add_all([dc, manga])
        self.db.flush()
        for index in range(3):
            self.db.add(CanonicalSeries(slug=f"dc-{index}", title=f"DC Series {index}", publisher_id=dc.id, start_year=2020))
        self.db.add(CanonicalSeries(slug="manga", title="One Piece", publisher_id=manga.id, start_year=1997))
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_only_the_named_publishers_are_imported(self) -> None:
        titles = {series.title for series in curated_series(self.db, ("dc",))}
        self.assertEqual(titles, {"DC Series 0", "DC Series 1", "DC Series 2"})
        self.assertNotIn("One Piece", titles)

    def test_the_request_budget_stops_the_run_cleanly(self) -> None:
        api = FakeApi(series=[{"id": 1, "series": "DC Series 0 (2020)"}], issues={1: []})
        api.calls = 0
        outcome = run_backfill(self.db, fetch=api, publisher_slugs=("dc",), max_requests=2)
        self.assertTrue(outcome.stopped_early)
        self.assertLess(len(outcome.series), 3)

    def test_a_throttle_stops_the_run_rather_than_failing_it(self) -> None:
        def throttled(url: str) -> dict[str, Any]:
            raise MetronThrottledError(60)

        throttled.calls = 0  # type: ignore[attr-defined]
        outcome = run_backfill(self.db, fetch=throttled, publisher_slugs=("dc",))
        self.assertTrue(outcome.stopped_early)
        self.assertEqual(outcome.series, [])


if __name__ == "__main__":
    unittest.main()
