from __future__ import annotations

import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.main import download_reading_path_entry_file, get_reading_path_entry_viewer_issue
from backend.app.models import Base, CanonicalIssue, CanonicalSeries, Publisher, ReadingPath, ReadingPathEntry
from backend.app.services.curation import sync_curation_data
from backend.app.services.reader import list_archive_pages
from backend.app.services.stream_buffer import StreamBufferTooLargeError, find_stream_archive, prune_stream_buffer, store_stream_archive


def sample_curation_payload() -> dict:
    return {
        "publishers": [{"slug": "test", "name": "Test Comics"}],
        "series": [
            {
                "slug": "sample-hero-2024",
                "publisher_slug": "test",
                "title": "Sample Hero",
                "volume": 1,
                "start_year": 2024,
                "issues": [
                    {
                        "issue_number": "1",
                        "title": "Sample Hero #1",
                        "published_on": "2024-01-01",
                    }
                ],
            }
        ],
        "reading_paths": [
            {
                "slug": "sample-event-main",
                "title": "Sample Event Main Path",
                "status": "published",
                "entries": [
                    {
                        "sort_order": 10,
                        "canonical_issue_key": "sample-hero-2024#1",
                        "entry_type": "issue",
                        "importance": "main",
                    }
                ],
            }
        ],
    }


class StreamBufferTests(unittest.TestCase):
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

    def test_prepare_buffered_archive_for_reader(self) -> None:
        cache_root = Path(self.temp_dir.name) / "stream-buffer"
        archive_bytes = self._cbz_bytes({"001.jpg": b"page-one", "002.jpg": b"page-two"})

        archive = store_stream_archive(
            cache_key="reading-path-1-entry-10",
            filename="Sample Hero 001.cbz",
            chunks=[archive_bytes],
            cache_root=cache_root,
            max_bytes=10 * 1024 * 1024,
        )

        self.assertEqual(archive.original_filename, "Sample Hero 001.cbz")
        self.assertEqual(Path(archive.storage_path).parent.name, "reading-path-1-entry-10")

        loaded = find_stream_archive("reading-path-1-entry-10", cache_root=cache_root)
        self.assertIsNotNone(loaded)
        pages = list_archive_pages(loaded)

        self.assertEqual([page.relative_path for page in pages], ["001.jpg", "002.jpg"])

    def test_prune_stream_buffer_evicts_oldest_entries(self) -> None:
        cache_root = Path(self.temp_dir.name) / "stream-buffer"
        library_root = Path(self.temp_dir.name) / "library"
        library_root.mkdir()
        library_file = library_root / "library.cbz"
        library_file.write_bytes(b"library-file")

        first = store_stream_archive(
            cache_key="reading-path-1-entry-10",
            filename="first.cbz",
            chunks=[self._cbz_bytes({"001.jpg": b"a" * 50})],
            cache_root=cache_root,
            max_bytes=10 * 1024 * 1024,
        )
        time.sleep(0.02)
        second = store_stream_archive(
            cache_key="reading-path-1-entry-11",
            filename="second.cbz",
            chunks=[self._cbz_bytes({"001.jpg": b"b" * 50})],
            cache_root=cache_root,
            max_bytes=10 * 1024 * 1024,
        )

        prune_stream_buffer(max_bytes=(second.size_bytes or 0) + 1, cache_root=cache_root)

        self.assertFalse(Path(first.storage_path).exists())
        self.assertTrue(Path(second.storage_path).exists())
        self.assertTrue(library_file.exists())

    def test_store_stream_archive_rejects_archive_larger_than_cap(self) -> None:
        cache_root = Path(self.temp_dir.name) / "stream-buffer"

        with self.assertRaises(StreamBufferTooLargeError):
            store_stream_archive(
                cache_key="reading-path-1-entry-10",
                filename="oversized.cbz",
                chunks=[self._cbz_bytes({"001.jpg": b"a" * 200})],
                cache_root=cache_root,
                max_bytes=120,
            )

    def test_entry_device_download_headers(self) -> None:
        payload_path = self._write_payload(sample_curation_payload())

        with self.session_factory() as db:
            sync_curation_data(db, payload_path)
            entry = db.scalar(select(ReadingPathEntry))
            reading_path = db.scalar(select(ReadingPath))

            with patch(
                "backend.app.main._prepare_entry_device_download",
                return_value=([b"cbz-bytes"], "Sample Hero 001.cbz", "application/vnd.comicbook+zip", 9),
                create=True,
            ):
                response = download_reading_path_entry_file(reading_path.id, entry.id, db)

        self.assertEqual(response.media_type, "application/vnd.comicbook+zip")
        self.assertIn('attachment; filename="Sample Hero 001.cbz"', response.headers["content-disposition"])
        self.assertEqual(response.headers["content-length"], "9")

    def test_entry_viewer_issue_uses_buffered_archive_pages(self) -> None:
        payload_path = self._write_payload(sample_curation_payload())
        buffered_archive = store_stream_archive(
            cache_key="reading-path-1-entry-1",
            filename="Sample Hero 001.cbz",
            chunks=[self._cbz_bytes({"001.jpg": b"page-one", "002.jpg": b"page-two"})],
            cache_root=Path(self.temp_dir.name) / "stream-buffer",
            max_bytes=10 * 1024 * 1024,
        )

        with self.session_factory() as db:
            sync_curation_data(db, payload_path)
            entry = db.scalar(select(ReadingPathEntry))
            reading_path = db.scalar(select(ReadingPath))

            with patch("backend.app.main._buffered_entry_archive", return_value=buffered_archive):
                issue = get_reading_path_entry_viewer_issue(reading_path.id, entry.id, db)

        self.assertEqual(issue.reading_path_id, reading_path.id)
        self.assertEqual(issue.reading_path_entry_id, entry.id)
        self.assertEqual(issue.page_count, 2)
        self.assertEqual([page.relative_path for page in issue.pages], ["001.jpg", "002.jpg"])

    def test_entry_device_download_rejects_mangapill(self) -> None:
        with self.session_factory() as db:
            publisher = Publisher(slug="test", name="Test Comics")
            series = CanonicalSeries(
                slug="manga-series",
                title="Manga Series",
                publisher=publisher,
            )
            issue = CanonicalIssue(
                legacy_key="manga-series#1",
                issue_number="1",
                issue_kind="issue",
                title="Manga Series #1",
                sort_order=1,
                provider_name="MangaPill",
                provider_url="https://mangapill.com/chapters/1-1000/sample",
                series=series,
            )
            reading_path = ReadingPath(slug="manga-path", title="Manga Path", status="published")
            entry = ReadingPathEntry(
                reading_path=reading_path,
                canonical_issue=issue,
                sort_order=10,
                entry_type="issue",
                importance="main",
            )
            db.add_all([publisher, series, issue, reading_path, entry])
            db.commit()
            db.refresh(reading_path)
            db.refresh(entry)

            with self.assertRaises(HTTPException) as raised:
                download_reading_path_entry_file(reading_path.id, entry.id, db)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("stream", str(raised.exception.detail).lower())

    @staticmethod
    def _cbz_bytes(files: dict[str, bytes]) -> bytes:
        root = tempfile.TemporaryDirectory()
        try:
            archive_path = Path(root.name) / "sample.cbz"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name, payload in files.items():
                    archive.writestr(name, payload)
            return archive_path.read_bytes()
        finally:
            root.cleanup()


if __name__ == "__main__":
    unittest.main()
