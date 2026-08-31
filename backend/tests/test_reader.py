from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.models import Archive
from backend.app.services.reader import archive_page_bytes, list_archive_pages


class ReaderMissingSourceTests(unittest.TestCase):
    """A library row can outlive its file, for example after syncing a local DB to the host."""

    def _archive(self, storage_path: Path) -> Archive:
        return Archive(id=1, issue_id=1, storage_path=str(storage_path), archive_format="directory", status="available")

    def test_list_archive_pages_reports_missing_source_as_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = self._archive(Path(temp_dir) / "Detective Comics 1094 (2025)")
            with self.assertRaises(FileNotFoundError):
                list_archive_pages(archive)

    def test_archive_page_bytes_reports_missing_source_as_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = self._archive(Path(temp_dir) / "Detective Comics 1094 (2025)")
            with self.assertRaises(FileNotFoundError):
                archive_page_bytes(archive, 1)


if __name__ == "__main__":
    unittest.main()
