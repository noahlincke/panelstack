from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.models import (
    Base,
    CanonicalIssue,
    CanonicalSeries,
    Event,
    IssueMatch,
    Publisher,
    ReadingPath,
    ReadingPathEntry,
    Series,
    StoryArc,
)
from backend.app.services.curation import CURATION_DATA_PATH, sync_curation_data
from backend.app.services.ingest import scan_source
from backend.app.services.library import persist_scans


def sample_curation_payload() -> dict:
    return {
        "publishers": [{"slug": "test", "name": "Test Comics"}],
        "events": [
            {
                "slug": "sample-event",
                "publisher_slug": "test",
                "title": "Sample Event",
                "status": "published",
            }
        ],
        "story_arcs": [
            {
                "slug": "sample-core",
                "event_slug": "sample-event",
                "title": "Sample Core",
                "status": "published",
            }
        ],
        "series": [
            {
                "slug": "sample-hero-2024",
                "publisher_slug": "test",
                "title": "Sample Hero",
                "aliases": ["The Sample Hero", "Sample-Hero"],
                "volume": 1,
                "start_year": 2024,
                "description": "The canonical Sample Hero launch run.",
                "issues": [
                    {
                        "issue_number": "1",
                        "title": "Sample Hero #1",
                        "published_on": "2024-01-01",
                        "event_slug": "sample-event",
                    }
                ],
            }
        ],
        "reading_paths": [
            {
                "slug": "sample-event-main",
                "event_slug": "sample-event",
                "title": "Sample Event Main Path",
                "status": "published",
                "entries": [
                    {
                        "sort_order": 10,
                        "canonical_issue_key": "sample-hero-2024#1",
                        "story_arc_slug": "sample-core",
                        "entry_type": "issue",
                        "importance": "main",
                    }
                ],
            }
        ],
    }


def sample_volume_payload() -> dict:
    return {
        "publishers": [{"slug": "test", "name": "Test Comics"}],
        "series": [
            {
                "slug": "sample-team-2018",
                "publisher_slug": "test",
                "title": "Sample Team",
                "volume": 1,
                "start_year": 2018,
                "issues": [{"issue_number": "1", "title": "Sample Team #1"}],
            },
            {
                "slug": "sample-team-2024",
                "publisher_slug": "test",
                "title": "Sample Team",
                "volume": 2,
                "start_year": 2024,
                "issues": [{"issue_number": "1", "title": "Sample Team #1"}],
            },
        ],
    }


def sample_specials_payload() -> dict:
    return {
        "publishers": [{"slug": "test", "name": "Test Comics"}],
        "series": [
            {
                "slug": "sample-team-2024",
                "publisher_slug": "test",
                "title": "Sample Team",
                "volume": 2,
                "start_year": 2024,
                "issues": [
                    {"issue_number": "Annual 1", "issue_kind": "annual", "title": "Sample Team Annual 1"},
                    {"issue_number": "Omega", "issue_kind": "one-shot", "title": "Sample Team Omega"},
                    {"issue_number": "1-6", "issue_kind": "collection", "title": "Sample Team Vol. 1"},
                ],
            }
        ],
    }


class CurationSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test.db"
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(bind=engine)
        self.session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_payload(self, payload: dict) -> Path:
        path = Path(self.temp_dir.name) / "curation.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_sync_curation_populates_catalog_and_is_idempotent(self) -> None:
        payload_path = self._write_payload(sample_curation_payload())

        with self.session_factory() as db:
            first = sync_curation_data(db, payload_path)
            first_entry_id = db.scalar(select(ReadingPathEntry.id))

        with self.session_factory() as db:
            second = sync_curation_data(db, payload_path)
            publisher = db.scalar(select(Publisher))
            event = db.scalar(select(Event))
            story_arc = db.scalar(select(StoryArc))
            canonical_series = db.scalar(select(CanonicalSeries))
            canonical_issue = db.scalar(select(CanonicalIssue))
            reading_path = db.scalar(select(ReadingPath))
            entries = db.scalars(select(ReadingPathEntry)).all()

        self.assertEqual(first.publishers_synced, 1)
        self.assertEqual(first.canonical_issues_synced, 1)
        self.assertEqual(first.reading_path_entries_synced, 1)
        self.assertEqual(second.reading_path_entries_synced, 1)
        self.assertEqual(publisher.name, "Test Comics")
        self.assertEqual(event.slug, "sample-event")
        self.assertEqual(story_arc.slug, "sample-core")
        self.assertEqual(canonical_series.title, "Sample Hero")
        self.assertEqual(canonical_issue.legacy_key, "sample-hero-2024#1")
        self.assertEqual(reading_path.slug, "sample-event-main")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].id, first_entry_id)
        self.assertEqual(entries[0].canonical_issue_id, canonical_issue.id)

    def test_persisted_issue_is_matched_to_canonical_issue(self) -> None:
        payload_path = self._write_payload(sample_curation_payload())

        root = Path(self.temp_dir.name)
        archive_path = root / "Sample Hero 001 (2024).cbz"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("001.jpg", b"page-one")

        scan = scan_source(archive_path)

        with self.session_factory() as db:
            sync_curation_data(db, payload_path)
            persist_scans(db, [scan])
            match = db.scalar(select(IssueMatch))
            canonical_issue = db.scalar(select(CanonicalIssue))

        self.assertIsNotNone(match)
        self.assertEqual(match.canonical_issue_id, canonical_issue.id)
        self.assertTrue(match.is_primary)
        self.assertEqual(match.match_strategy, "series-title+issue-number")

    def test_series_is_enriched_from_canonical_match(self) -> None:
        payload_path = self._write_payload(sample_curation_payload())

        root = Path(self.temp_dir.name)
        archive_path = root / "Sample Hero 001 (2024).cbz"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("001.jpg", b"page-one")

        scan = scan_source(archive_path)

        with self.session_factory() as db:
            sync_curation_data(db, payload_path)
            persist_scans(db, [scan])
            series = db.scalar(select(Series))
            canonical_series = db.scalar(select(CanonicalSeries))

        self.assertEqual(series.canonical_series_id, canonical_series.id)
        self.assertEqual(series.canonical_match_strategy, "issue-match-majority")
        self.assertGreaterEqual(series.canonical_match_confidence or 0, 80)
        self.assertEqual(series.publisher, "Test Comics")
        self.assertEqual(series.description, "The canonical Sample Hero launch run.")

    def test_persisted_issue_matches_alias_title_variant(self) -> None:
        payload_path = self._write_payload(sample_curation_payload())

        root = Path(self.temp_dir.name)
        archive_path = root / "The Sample-Hero 001 (2024).cbz"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("001.jpg", b"page-one")

        scan = scan_source(archive_path)

        with self.session_factory() as db:
            sync_curation_data(db, payload_path)
            persist_scans(db, [scan])
            match = db.scalar(select(IssueMatch))
            canonical_issue = db.scalar(select(CanonicalIssue))

        self.assertIsNotNone(match)
        self.assertEqual(match.canonical_issue_id, canonical_issue.id)
        self.assertEqual(match.match_strategy, "series-title+issue-number")

    def test_volume_match_prefers_correct_canonical_series(self) -> None:
        payload_path = self._write_payload(sample_volume_payload())

        root = Path(self.temp_dir.name)
        archive_path = root / "Sample Team Vol. 2 001 (2024).cbz"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("001.jpg", b"page-one")

        scan = scan_source(archive_path)

        with self.session_factory() as db:
            sync_curation_data(db, payload_path)
            persist_scans(db, [scan])
            match = db.scalar(select(IssueMatch).order_by(IssueMatch.id.asc()))
            canonical_issue = db.scalar(select(CanonicalIssue).where(CanonicalIssue.legacy_key == "sample-team-2024#1"))

        self.assertIsNotNone(match)
        self.assertEqual(match.canonical_issue_id, canonical_issue.id)

    def test_series_fallback_disambiguates_same_title_runs_without_issue_match(self) -> None:
        payload_path = self._write_payload(sample_volume_payload())

        root = Path(self.temp_dir.name)
        archive_path = root / "Sample Team Vol. 2 099 (2024).cbz"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("001.jpg", b"page-one")

        scan = scan_source(archive_path)

        with self.session_factory() as db:
            sync_curation_data(db, payload_path)
            persist_scans(db, [scan])
            series = db.scalar(select(Series))
            canonical_series = db.scalar(select(CanonicalSeries).where(CanonicalSeries.slug == "sample-team-2024"))
            match = db.scalar(select(IssueMatch))

        self.assertEqual(series.canonical_series_id, canonical_series.id)
        self.assertEqual(series.canonical_match_strategy, "series-volume+title")
        self.assertIsNone(match)

    def test_annual_one_shot_and_collection_matches(self) -> None:
        payload_path = self._write_payload(sample_specials_payload())
        root = Path(self.temp_dir.name)

        annual_path = root / "Sample Team Annual 001 (2024).cbz"
        special_path = root / "Sample Team Omega One-Shot (2024).cbz"
        collection_path = root / "Sample Team Vol. 1 001-006 TPB (2024).cbz"

        for archive_path in (annual_path, special_path, collection_path):
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("001.jpg", b"page-one")

        scans = [scan_source(annual_path), scan_source(special_path), scan_source(collection_path)]

        with self.session_factory() as db:
            sync_curation_data(db, payload_path)
            persist_scans(db, scans)
            matches = db.scalars(select(IssueMatch).order_by(IssueMatch.id.asc())).all()
            matched_keys = [
                db.scalar(select(CanonicalIssue.legacy_key).where(CanonicalIssue.id == match.canonical_issue_id))
                for match in matches
            ]

        self.assertEqual(len(matches), 3)
        self.assertIn("sample-team-2024#annual 1", matched_keys)
        self.assertIn("sample-team-2024#omega", matched_keys)
        self.assertIn("sample-team-2024#1-6", matched_keys)

    def test_default_curation_seed_includes_house_of_m_and_infinite_crisis(self) -> None:
        with self.session_factory() as db:
            result = sync_curation_data(db, CURATION_DATA_PATH)
            event_slugs = set(db.scalars(select(Event.slug)).all())
            path_slugs = set(db.scalars(select(ReadingPath.slug)).all())
            canonical_series_slugs = set(db.scalars(select(CanonicalSeries.slug)).all())

        self.assertGreaterEqual(result.events_synced, 4)
        self.assertGreaterEqual(result.reading_paths_synced, 80)
        self.assertIn("house-of-m", event_slugs)
        self.assertIn("infinite-crisis", event_slugs)
        self.assertIn("house-of-m-core", path_slugs)
        self.assertIn("infinite-crisis-core", path_slugs)
        self.assertIn("ultimate-spider-man-2024-first-year", path_slugs)
        self.assertIn("batman-2016-vol-1", path_slugs)
        self.assertIn("justice-league-unlimited-2024-vol-2", path_slugs)
        self.assertIn("amazing-spider-man-2025-vol-2", path_slugs)
        self.assertIn("x-men-2024-vol-2", path_slugs)
        self.assertIn("absolute-batman-2024-vol-1", path_slugs)
        self.assertIn("superman-unlimited-2025-vol-1", path_slugs)
        self.assertIn("ultimate-spider-man-2024", canonical_series_slugs)
        self.assertIn("absolute-wonder-woman-2024", canonical_series_slugs)
        self.assertIn("batman-2016", canonical_series_slugs)
        self.assertIn("green-lantern-2023", canonical_series_slugs)
        self.assertIn("amazing-spider-man-2025", canonical_series_slugs)
        self.assertIn("doctor-strange-of-asgard-2025", canonical_series_slugs)


if __name__ == "__main__":
    unittest.main()
