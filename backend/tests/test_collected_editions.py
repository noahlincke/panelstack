from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from apply_collected_editions import EDITIONS_PATH, apply_edition  # noqa: E402

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import _series_download_entries
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

        entries = [entry for _, entry in _series_download_entries([self.path])]

        self.assertEqual(len(entries), 4)

    def test_a_trade_replaces_the_issues_it_collects(self) -> None:
        for number in range(1, 7):
            self._entry(str(number), "issue", number)
        self._entry("1-6", "collection", 1000)
        self.db.commit()
        self.db.refresh(self.path)

        entries = [entry for _, entry in _series_download_entries([self.path])]

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].entry_type, "collection")

    def test_issues_published_after_the_trade_still_come_through(self) -> None:
        for number in range(1, 9):
            self._entry(str(number), "issue", number)
        self._entry("1-6", "collection", 1000)
        self.db.commit()
        self.db.refresh(self.path)

        entries = [entry for _, entry in _series_download_entries([self.path])]
        numbers = [entry.canonical_issue.issue_number for entry in entries]

        self.assertEqual(numbers, ["1-6", "7", "8"])

    def test_two_trades_cover_their_own_ranges(self) -> None:
        for number in range(1, 15):
            self._entry(str(number), "issue", number)
        self._entry("1-6", "collection", 1000)
        self._entry("7-12", "collection", 1001)
        self.db.commit()
        self.db.refresh(self.path)

        numbers = [entry.canonical_issue.issue_number for _, entry in _series_download_entries([self.path])]

        self.assertEqual(numbers, ["1-6", "7-12", "13", "14"])


if __name__ == "__main__":
    unittest.main()


class ApplyCollectedEditionsTests(unittest.TestCase):
    """Trade contents are edited into the curation data from verified listings.

    Absolute Batman Vol. 2 was recorded as collecting #7-12 when it collects
    #7-14, so #13 and #14 showed up in reading lists as loose issues a trade
    already covered.
    """

    def _payload(self) -> dict:
        return {
            "series": [
                {
                    "slug": "absolute-batman-2024",
                    "title": "Absolute Batman",
                    "issues": [
                        {
                            "issue_number": "7-12",
                            "issue_kind": "collection",
                            "title": "Absolute Batman Vol. 2 (TPB)",
                            "published_on": "2026-01-01",
                        }
                    ],
                }
            ],
            "reading_paths": [
                {
                    "slug": "absolute-batman-2024-vol-2",
                    "entries": [
                        {"canonical_issue_key": f"absolute-batman-2024#{n}", "entry_type": "issue"}
                        for n in range(7, 15)
                    ]
                    + [
                        {
                            "canonical_issue_key": "absolute-batman-2024#7-12",
                            "entry_type": "collection",
                            "sort_order": 1000,
                        }
                    ],
                }
            ],
        }

    def _edition(self, **overrides) -> dict:
        edition = {
            "series_slug": "absolute-batman-2024",
            "title": "Absolute Batman Vol. 2 - Abomination (TPB)",
            "first_issue": 7,
            "last_issue": 14,
            "published_on": "2026-01-01",
        }
        edition.update(overrides)
        return edition

    def test_an_overlapping_trade_is_replaced_rather_than_duplicated(self) -> None:
        payload = self._payload()
        apply_edition(payload, self._edition())
        trades = [i for i in payload["series"][0]["issues"] if i["issue_kind"] == "collection"]
        self.assertEqual([t["issue_number"] for t in trades], ["7-14"])
        self.assertEqual(trades[0]["title"], "Absolute Batman Vol. 2 - Abomination (TPB)")

    def test_the_stale_entry_is_swapped_on_the_volume_that_holds_the_first_issue(self) -> None:
        payload = self._payload()
        apply_edition(payload, self._edition())
        entries = payload["reading_paths"][0]["entries"]
        collections = [e for e in entries if e["entry_type"] == "collection"]
        self.assertEqual([e["canonical_issue_key"] for e in collections], ["absolute-batman-2024#7-14"])

    def test_a_second_non_overlapping_trade_can_share_a_volume(self) -> None:
        payload = self._payload()
        apply_edition(payload, self._edition())
        apply_edition(
            payload,
            self._edition(title="Absolute Batman Vol. 3 (TPB)", first_issue=15, last_issue=18),
        )
        entries = payload["reading_paths"][0]["entries"]
        collections = sorted(e["canonical_issue_key"] for e in entries if e["entry_type"] == "collection")
        # #15 is not on this volume, so Vol. 3 finds no host and is not attached.
        self.assertEqual(collections, ["absolute-batman-2024#7-14"])

    def test_applying_twice_changes_nothing_the_second_time(self) -> None:
        payload = self._payload()
        apply_edition(payload, self._edition())
        self.assertEqual(apply_edition(payload, self._edition()), [])

    def test_an_unknown_series_is_reported_not_silently_skipped(self) -> None:
        changes = apply_edition(self._payload(), self._edition(series_slug="does-not-exist"))
        self.assertTrue(changes[0].startswith("!"))


class ShippedCollectedEditionDataTests(unittest.TestCase):
    def test_every_recorded_edition_names_a_real_range_and_a_source(self) -> None:
        editions = json.loads(EDITIONS_PATH.read_text())["editions"]
        self.assertTrue(editions)
        for edition in editions:
            with self.subTest(edition["title"]):
                self.assertLessEqual(edition["first_issue"], edition["last_issue"])
                self.assertTrue(edition["source"].startswith("https://"))
                self.assertRegex(edition["published_on"], r"^\d{4}-\d{2}-\d{2}$")

    def test_no_two_editions_of_a_series_overlap(self) -> None:
        editions = json.loads(EDITIONS_PATH.read_text())["editions"]
        by_series: dict[str, list[tuple[int, int]]] = {}
        for edition in editions:
            by_series.setdefault(edition["series_slug"], []).append((edition["first_issue"], edition["last_issue"]))
        for series_slug, spans in by_series.items():
            spans.sort()
            for earlier, later in zip(spans, spans[1:]):
                with self.subTest(series_slug):
                    self.assertLess(earlier[1], later[0])


class SeriesShelfTests(unittest.TestCase):
    """Coverage is a property of the series, not of one volume.

    A trade lives on whichever volume holds its first issue, so Detective Comics
    Vol. 3 sits on the volume starting at #1101 while #1102-1106 sit on the next
    one. Working coverage out a volume at a time offered those five as loose
    downloads even though the book already collects them.
    """

    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()
        publisher = Publisher(slug="dc", name="DC Comics")
        self.db.add(publisher)
        self.db.flush()
        self.series = CanonicalSeries(slug="detective-comics", title="Detective Comics", publisher_id=publisher.id)
        self.db.add(self.series)
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()

    def _path(self, slug: str, issue_numbers: list[str], trade: str | None) -> ReadingPath:
        path = ReadingPath(slug=slug, title=slug, status="published")
        self.db.add(path)
        self.db.flush()
        for order, number in enumerate(issue_numbers):
            self._entry(path, number, "issue", (order + 1) * 10)
        if trade is not None:
            self._entry(path, trade, "collection", 1000)
        return path

    def _entry(self, path: ReadingPath, number: str, entry_type: str, sort_order: int) -> None:
        issue = CanonicalIssue(
            series_id=self.series.id,
            legacy_key=f"detective#{number}",
            issue_number=number,
            issue_kind="collection" if entry_type == "collection" else "issue",
            title=f"Detective Comics #{number}",
            sort_order=sort_order,
        )
        self.db.add(issue)
        self.db.flush()
        self.db.add(
            ReadingPathEntry(
                reading_path_id=path.id,
                canonical_issue_id=issue.id,
                sort_order=sort_order,
                entry_type=entry_type,
                importance="main",
            )
        )
        self.db.flush()

    def _titles(self, paths: list[ReadingPath]) -> list[str]:
        return [entry.canonical_issue.title for _, entry in _series_download_entries(paths)]

    def test_a_trade_covers_issues_that_live_on_a_later_volume(self) -> None:
        first = self._path("vol-1", ["1101", "1102"], "1101-1106")
        second = self._path("vol-2", ["1103", "1104", "1105", "1106", "1107"], None)
        self.assertEqual(
            self._titles([first, second]),
            ["Detective Comics #1101-1106", "Detective Comics #1107"],
        )

    def test_trades_lead_their_own_volume(self) -> None:
        path = self._path("vol-3", ["15", "16", "17", "18", "19", "20"], "15-18")
        self.assertEqual(
            self._titles([path]),
            ["Detective Comics #15-18", "Detective Comics #19", "Detective Comics #20"],
        )

    def test_a_series_with_no_trades_offers_every_issue(self) -> None:
        path = self._path("vol-1", ["1", "2", "3"], None)
        self.assertEqual(len(self._titles([path])), 3)

    def test_the_owning_volume_travels_with_each_entry(self) -> None:
        first = self._path("vol-1", ["1101"], "1101-1106")
        second = self._path("vol-2", ["1107"], None)
        owners = [path.slug for path, _ in _series_download_entries([first, second])]
        self.assertEqual(owners, ["vol-1", "vol-2"])
