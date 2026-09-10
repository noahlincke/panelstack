from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import (  # noqa: E402
    Base,
    CanonicalIssue,
    CanonicalIssueSource,
    CanonicalSeries,
    CanonicalSeriesSource,
    Publisher,
    ReadingList,
    ReadingListItem,
    ReadingPath,
    ReadingPathEntry,
)
from scripts.audit_catalog import audit, prune  # noqa: E402


class AuditTests(unittest.TestCase):
    """A series is only judged when our run sits inside the one Metron describes."""

    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()
        self.publisher = Publisher(slug="dc", name="DC Comics")
        self.db.add(self.publisher)
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()

    def _series(self, slug: str, title: str, numbers: list[str], sourced: list[str]) -> CanonicalSeries:
        series = CanonicalSeries(slug=slug, title=title, publisher_id=self.publisher.id, start_year=2016)
        self.db.add(series)
        self.db.flush()
        for number in numbers:
            issue = CanonicalIssue(
                series_id=series.id,
                legacy_key=f"{slug}#{number}",
                issue_number=number,
                issue_kind="issue",
                title=f"{title} #{number}",
                sort_order=int(number),
            )
            self.db.add(issue)
            self.db.flush()
            if number in sourced:
                self.db.add(
                    CanonicalIssueSource(
                        canonical_issue_id=issue.id,
                        source_name="Metron",
                        source_issue_id=f"{slug}-{number}",
                        source_url="https://metron.cloud/api/issue/1/",
                        last_seen_at=datetime.now(timezone.utc),
                    )
                )
        self.db.commit()
        return series

    def _match(self, series: CanonicalSeries, *, last: str, count: int, title: str = "Batman") -> None:
        self.db.add(
            CanonicalSeriesSource(
                canonical_series_id=series.id,
                source_name="Metron",
                source_series_id="93",
                source_series_title=title,
                source_issue_count=count,
                source_last_issue_number=last,
                source_year_began=2016,
                matched_issue_count=count,
                last_seen_at=datetime.now(timezone.utc),
            )
        )
        self.db.commit()

    def _verdict(self, title: str):
        return next(entry for entry in audit(self.db) if entry.series.title == title)

    def test_issues_past_the_end_of_the_matched_run_are_invented(self) -> None:
        # Batman (2016) really ended at #163; the catalogue carried it to #166.
        series = self._series("batman-2016", "Batman", ["161", "162", "163", "164", "165", "166"], ["161", "162", "163"])
        self._match(series, last="163", count=163)
        entry = self._verdict("Batman")
        self.assertEqual(entry.verdict, "invented")
        self.assertEqual([i.issue_number for i in entry.phantoms], ["164", "165", "166"])

    def test_a_run_that_stops_where_metron_does_is_clean(self) -> None:
        series = self._series("batman-2016", "Batman", ["161", "162", "163"], ["161", "162", "163"])
        self._match(series, last="163", count=163)
        self.assertEqual(self._verdict("Batman").verdict, "clean")

    def test_a_gap_inside_metrons_own_run_is_a_disagreement_not_a_verdict(self) -> None:
        """Our Daredevil (2001) is the 58-issue Bendis run; it matched an 8-issue series."""
        series = self._series("daredevil-2001", "Daredevil", ["1", "2", "3", "4", "5"], ["1", "2"])
        self._match(series, last="3", count=3, title="Daredevil")
        entry = self._verdict("Daredevil")
        self.assertEqual(entry.verdict, "review")
        self.assertEqual(entry.phantoms, [], "a series we cannot judge must never be pruned")

    def test_a_series_metron_never_matched_is_left_alone(self) -> None:
        self._series("hunter-x-hunter-1998", "Hunter x Hunter", ["1", "2", "3"], [])
        entry = self._verdict("Hunter x Hunter")
        self.assertEqual(entry.verdict, "unmatched")
        self.assertEqual(entry.phantoms, [])

    def test_the_verdict_carries_the_evidence_behind_it(self) -> None:
        series = self._series("batman-2016", "Batman", ["163", "164"], ["163"])
        self._match(series, last="163", count=163)
        self.assertIn("163 issues through #163", self._verdict("Batman").reason)

    def test_collected_editions_are_not_mistaken_for_invented_issues(self) -> None:
        series = self._series("batman-2016", "Batman", ["161", "162"], ["161", "162"])
        collected = CanonicalIssue(
            series_id=series.id,
            legacy_key="batman-2016#1-6",
            issue_number="1-6",
            issue_kind="collection",
            title="Batman Vol. 1",
            sort_order=1,
        )
        self.db.add(collected)
        self.db.commit()
        self._match(series, last="162", count=162)
        entry = self._verdict("Batman")
        self.assertNotIn("1-6", [i.issue_number for i in entry.phantoms])


class PruneTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()
        publisher = Publisher(slug="dc", name="DC Comics")
        self.db.add(publisher)
        self.db.flush()
        self.series = CanonicalSeries(slug="batman-2016", title="Batman", publisher_id=publisher.id)
        self.db.add(self.series)
        self.db.flush()
        self.issue = CanonicalIssue(
            series_id=self.series.id, legacy_key="batman-2016#164", issue_number="164",
            issue_kind="issue", title="Batman #164", sort_order=164,
        )
        self.db.add(self.issue)
        self.db.flush()
        path = ReadingPath(slug="batman-2016-vol-4", title="Batman: Vol. 4", status="published")
        self.db.add(path)
        self.db.flush()
        entry = ReadingPathEntry(
            reading_path_id=path.id, canonical_issue_id=self.issue.id,
            sort_order=10, entry_type="issue", importance="core", is_optional=False,
        )
        self.db.add(entry)
        self.db.flush()
        reading_list = ReadingList(name="To read")
        self.db.add(reading_list)
        self.db.flush()
        self.db.add(
            ReadingListItem(
                reading_list_id=reading_list.id, reading_path_id=path.id,
                reading_path_entry_id=entry.id, title="Batman #164", sort_order=1,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_pruning_takes_the_shelf_entry_and_the_list_row_with_it(self) -> None:
        issues, entries, items = prune(self.db, [self.issue])
        self.assertEqual((issues, entries, items), (1, 1, 1))
        self.assertIsNone(self.db.scalar(select(CanonicalIssue).where(CanonicalIssue.issue_number == "164")))
        self.assertEqual(list(self.db.scalars(select(ReadingPathEntry))), [])
        self.assertEqual(
            list(self.db.scalars(select(ReadingListItem))), [],
            "SQLite does not enforce the cascade, so the list row must go explicitly",
        )

    def test_pruning_nothing_touches_nothing(self) -> None:
        self.assertEqual(prune(self.db, []), (0, 0, 0))
        self.assertIsNotNone(self.db.scalar(select(CanonicalIssue)))


if __name__ == "__main__":
    unittest.main()
