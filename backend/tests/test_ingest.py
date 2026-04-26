from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from backend.app.routers import ingest as ingest_router
from backend.app.services import IngestService, discover_sources, infer_metadata_from_name, scan_source


class IngestServiceTests(unittest.TestCase):
    def test_metadata_inference_from_release_name(self) -> None:
        metadata = infer_metadata_from_name("Absolute Batman 017 (2026) (Digital) (Pyrate-DCP).cbz")
        self.assertEqual(metadata.title, "Absolute Batman")
        self.assertEqual(metadata.issue, "017")
        self.assertEqual(metadata.issue_kind, "issue")
        self.assertEqual(metadata.year, 2026)
        self.assertEqual(metadata.release_group, "Pyrate-DCP")

    def test_metadata_inference_handles_annuals_and_collections(self) -> None:
        annual = infer_metadata_from_name("Sample Team Annual 001 (2024).cbz")
        collection = infer_metadata_from_name("Sample Team Vol. 2 001-006 TPB (2024).cbz")

        self.assertEqual(annual.title, "Sample Team")
        self.assertEqual(annual.issue, "001")
        self.assertEqual(annual.issue_kind, "annual")
        self.assertEqual(collection.title, "Sample Team")
        self.assertEqual(collection.issue, "001-006")
        self.assertEqual(collection.issue_kind, "collection")

    def test_directory_scan_counts_image_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "001.jpg").write_bytes(b"fake-jpg-1")
            (root / "010.png").write_bytes(b"fake-png-2")
            nested = root / "bonus"
            nested.mkdir()
            (nested / "notes.txt").write_text("ignore me", encoding="utf-8")

            result = scan_source(root)

        self.assertEqual(result.source_kind, "directory")
        self.assertEqual(result.page_count, 2)
        self.assertEqual([page.relative_path for page in result.pages], ["001.jpg", "010.png"])

    def test_zip_scan_counts_archive_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "sample.cbz"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("001.jpg", b"a")
                archive.writestr("002.png", b"b")
                archive.writestr("readme.txt", b"ignore")

            result = scan_source(archive_path)

        self.assertEqual(result.source_kind, "archive")
        self.assertEqual(result.archive_format, "zip")
        self.assertEqual(result.page_count, 2)
        self.assertEqual([page.relative_path for page in result.pages], ["001.jpg", "002.png"])

    def test_cbr_scan_counts_archive_pages_via_bsdtar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "sample.cbr"
            archive_path.write_bytes(b"rar")

            with patch(
                "backend.app.services.ingest.subprocess.run",
                return_value=type("Completed", (), {"stdout": "folder/001.jpg\nfolder/002.png\nreadme.txt\n"})(),
            ):
                result = scan_source(archive_path)

        self.assertEqual(result.source_kind, "archive")
        self.assertEqual(result.archive_format, "rar")
        self.assertEqual(result.page_count, 2)
        self.assertEqual([page.relative_path for page in result.pages], ["folder/001.jpg", "folder/002.png"])

    def test_job_lifecycle_runs_synchronously(self) -> None:
        service = IngestService()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "page-1.jpg").write_bytes(b"fake")

            job = service.submit([root])
            self.assertEqual(job.status.value, "queued")
            completed = service.run(job.job_id)

        self.assertEqual(completed.status.value, "succeeded")
        self.assertEqual(completed.scans[0].page_count, 1)

    def test_router_imports_without_fastapi(self) -> None:
        self.assertTrue(hasattr(ingest_router, "router"))
        self.assertGreaterEqual(len(getattr(ingest_router.router, "routes", [])), 5)

    def test_discover_sources_expands_library_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extracted = root / "Absolute Batman 017 (2026)"
            extracted.mkdir()
            (extracted / "001.jpg").write_bytes(b"page")
            archive_path = root / "Immortal Thor 012 (2025).cbz"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("001.jpg", b"page")

            sources = discover_sources(root)

        self.assertEqual(
            {source.name for source in sources},
            {"Absolute Batman 017 (2026)", "Immortal Thor 012 (2025).cbz"},
        )


if __name__ == "__main__":
    unittest.main()
