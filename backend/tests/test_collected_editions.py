from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import _collection_download_entries
from backend.app.models import Base, CanonicalIssue, CanonicalSeries, Publisher, ReadingPath, ReadingPathEntry


class CollectedEditionPreferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()
        publisher = Publisher(slug="dc", name="DC Comics")
        self.db.add(publisher)
        self.db.flush()
        self.series = CanonicalSeries(slug="absolute-batman", title="Absolute Batman", publisher_id=publisher.id)
        self.db.add(self.series)
        self.db.flush()
        self.path = ReadingPath(slug="absolute-batman-vol-1", title="Absolute Batman", status="published")
        self.db.add(self.path)
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()

    def _entry(self, issue_number: str, entry_type: str, sort_order: int) -> ReadingPathEntry:
        issue = CanonicalIssue(
            series_id=self.series.id,
            legacy_key=f"absolute-batman#{issue_number}",
            issue_number=issue_number,
            issue_kind="collection" if entry_type == "collection" else "issue",
            title=f"Absolute Batman #{issue_number}",
            sort_order=sort_order,
        )
        self.db.add(issue)
        self.db.flush()
        entry = ReadingPathEntry(
            reading_path_id=self.path.id,
            canonical_issue_id=issue.id,
            sort_order=sort_order,
            entry_type=entry_type,
            importance="main",
        )
        self.db.add(entry)
        self.db.flush()
        return entry

    def test_without_a_trade_every_issue_is_downloaded(self) -> None:
        for number in range(1, 5):
            self._entry(str(number), "issue", number)
        self.db.commit()
        self.db.refresh(self.path)

        entries = _collection_download_entries(self.path)

        self.assertEqual(len(entries), 4)

    def test_a_trade_replaces_the_issues_it_collects(self) -> None:
        for number in range(1, 7):
            self._entry(str(number), "issue", number)
        self._entry("1-6", "collection", 1000)
        self.db.commit()
        self.db.refresh(self.path)

        entries = _collection_download_entries(self.path)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].entry_type, "collection")

    def test_issues_published_after_the_trade_still_come_through(self) -> None:
        for number in range(1, 9):
            self._entry(str(number), "issue", number)
        self._entry("1-6", "collection", 1000)
        self.db.commit()
        self.db.refresh(self.path)

        entries = _collection_download_entries(self.path)
        numbers = [entry.canonical_issue.issue_number for entry in entries]

        self.assertEqual(numbers, ["1-6", "7", "8"])

    def test_two_trades_cover_their_own_ranges(self) -> None:
        for number in range(1, 15):
            self._entry(str(number), "issue", number)
        self._entry("1-6", "collection", 1000)
        self._entry("7-12", "collection", 1001)
        self.db.commit()
        self.db.refresh(self.path)

        numbers = [entry.canonical_issue.issue_number for entry in _collection_download_entries(self.path)]

        self.assertEqual(numbers, ["1-6", "7-12", "13", "14"])


if __name__ == "__main__":
    unittest.main()
