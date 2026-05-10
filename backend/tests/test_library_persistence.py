from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.main import (
    _default_download_root,
    _parse_download_output,
    delete_issue,
    get_app_settings,
    open_downloads_folder,
    update_app_settings,
)
from backend.app.models import Archive, Base, Issue, Series
from backend.app.schemas import AppSettingsWrite
from backend.app.services.archive_tools import ArchiveTool
from backend.app.services.ingest import scan_source
from backend.app.services.library import persist_scans
from backend.app.services.reader import archive_page_bytes, list_archive_pages


class LibraryPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test.db"
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(bind=engine)
        self.session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_persist_scan_creates_series_issue_and_archive(self) -> None:
        root = Path(self.temp_dir.name)
        archive_path = root / "Absolute Batman 017 (2026) (Digital) (Pyrate-DCP).cbz"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("001.jpg", b"a")
            archive.writestr("002.jpg", b"b")

        scan = scan_source(archive_path)

        with self.session_factory() as db:
            result = persist_scans(db, [scan])
            series = db.scalar(select(Series))
            issue = db.scalar(select(Issue))
            archive = db.scalar(select(Archive))

        self.assertEqual(result.series_created, 1)
        self.assertEqual(result.issues_created, 1)
        self.assertEqual(result.archives_created, 1)
        self.assertEqual(series.title, "Absolute Batman")
        self.assertEqual(issue.issue_number, "017")
        self.assertEqual(archive.page_count, 2)

    def test_reader_lists_and_loads_archive_pages(self) -> None:
        root = Path(self.temp_dir.name)
        archive_path = root / "Sample 001.cbz"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("001.jpg", b"page-one")
            archive.writestr("002.jpg", b"page-two")

        scan = scan_source(archive_path)

        with self.session_factory() as db:
            persist_scans(db, [scan])
            archive = db.scalar(select(Archive))

        pages = list_archive_pages(archive)
        content, media_type, filename = archive_page_bytes(archive, 2)

        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[1].relative_path, "002.jpg")
        self.assertEqual(content, b"page-two")
        self.assertEqual(media_type, "image/jpeg")
        self.assertEqual(filename, "002.jpg")

    def test_pdf_archive_is_indexed_but_not_streamable(self) -> None:
        root = Path(self.temp_dir.name)
        pdf_path = root / "Sample 001.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Page >>\nendobj\n%%EOF")

        scan = scan_source(pdf_path)

        with self.session_factory() as db:
            persist_scans(db, [scan])
            archive = db.scalar(select(Archive))

        with self.assertRaisesRegex(ValueError, "not streamable"):
            list_archive_pages(archive)

    def test_cbr_archive_is_streamable_via_bsdtar(self) -> None:
        root = Path(self.temp_dir.name)
        archive_path = root / "Sample 001.cbr"
        archive_path.write_bytes(b"rar")

        with patch(
            "backend.app.services.archive_tools.find_rar_tool",
            return_value=ArchiveTool(kind="bsdtar", path="/usr/bin/bsdtar"),
        ), patch(
            "backend.app.services.archive_tools.subprocess.run",
            return_value=type("Completed", (), {"stdout": "Sample 001/001.jpg\nSample 001/002.jpg\n"})(),
        ):
            scan = scan_source(archive_path)

        with self.session_factory() as db:
            persist_scans(db, [scan])
            archive = db.scalar(select(Archive))

        with patch(
            "backend.app.services.archive_tools.find_rar_tool",
            return_value=ArchiveTool(kind="bsdtar", path="/usr/bin/bsdtar"),
        ), patch(
            "backend.app.services.archive_tools.subprocess.run",
            return_value=type("Completed", (), {"stdout": "Sample 001/001.jpg\nSample 001/002.jpg\n"})(),
        ):
            pages = list_archive_pages(archive)

        with patch(
            "backend.app.services.reader.scan_source",
            return_value=scan,
        ), patch(
            "backend.app.services.archive_tools.subprocess.run",
            return_value=type("Completed", (), {"returncode": 0, "stdout": b"page-one", "stderr": b""})(),
        ):
            content, media_type, filename = archive_page_bytes(archive, 1)

        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].relative_path, "Sample 001/001.jpg")
        self.assertEqual(content, b"page-one")
        self.assertEqual(media_type, "image/jpeg")
        self.assertEqual(filename, "001.jpg")

    def test_cbr_archive_is_streamable_via_7zz(self) -> None:
        root = Path(self.temp_dir.name)
        archive_path = root / "Sample 001.cbr"
        archive_path.write_bytes(b"rar")
        sevenzip_output = """
Path = sample.cbr
Type = Rar

Path = Sample 001/001.jpg
Size = 8
Folder = -
"""
        with patch(
            "backend.app.services.archive_tools.find_rar_tool",
            return_value=ArchiveTool(kind="sevenzip", path="/app/bin/7zz"),
        ), patch(
            "backend.app.services.archive_tools.subprocess.run",
            return_value=type("Completed", (), {"stdout": sevenzip_output})(),
        ):
            scan = scan_source(archive_path)

        with self.session_factory() as db:
            persist_scans(db, [scan])
            archive = db.scalar(select(Archive))

        with patch("backend.app.services.archive_tools.rar_support_available", return_value=True), patch(
            "backend.app.services.reader.scan_source",
            return_value=scan,
        ), patch(
            "backend.app.services.archive_tools.find_rar_tool",
            return_value=ArchiveTool(kind="sevenzip", path="/app/bin/7zz"),
        ), patch(
            "backend.app.services.archive_tools.subprocess.run",
            return_value=type("Completed", (), {"returncode": 0, "stdout": b"page-one", "stderr": b""})(),
        ) as run_command:
            content, media_type, filename = archive_page_bytes(archive, 1)

        self.assertEqual(content, b"page-one")
        self.assertEqual(media_type, "image/jpeg")
        self.assertEqual(filename, "001.jpg")
        run_command.assert_called_once()
        self.assertEqual(run_command.call_args.args[0][:3], ["/app/bin/7zz", "x", "-so"])

    def test_delete_issue_removes_files_and_deletes_series_when_last_issue(self) -> None:
        root = Path(self.temp_dir.name)
        extracted = root / "Sample 001"
        extracted.mkdir()
        (extracted / "001.jpg").write_bytes(b"page-one")

        scan = scan_source(extracted)

        with self.session_factory() as db:
            persist_scans(db, [scan])
            issue = db.scalar(select(Issue))
            series = db.scalar(select(Series))

            result = delete_issue(issue.id, db)

            self.assertTrue(result["deleted"])
            self.assertTrue(result["series_deleted"])
            self.assertIsNone(db.get(Issue, issue.id))
            self.assertIsNone(db.get(Series, series.id))

        self.assertFalse(extracted.exists())

    def test_open_downloads_folder_uses_configured_download_root(self) -> None:
        root = Path(self.temp_dir.name) / "downloads"
        settings_path = Path(self.temp_dir.name) / "settings.json"
        settings_path.write_text(f'{{"download_root": "{root}"}}', encoding="utf-8")

        with patch("backend.app.main.APP_SETTINGS_PATH", settings_path), patch(
            "backend.app.main._open_path_in_file_manager"
        ) as open_path:
            result = open_downloads_folder()

        open_path.assert_called_once_with(root.resolve())
        self.assertEqual(result["path"], str(root.resolve()))

    def test_update_app_settings_persists_download_root(self) -> None:
        settings_path = Path(self.temp_dir.name) / "settings.json"
        download_root = Path(self.temp_dir.name) / "custom-downloads"

        with patch("backend.app.main.APP_SETTINGS_PATH", settings_path):
            result = update_app_settings(AppSettingsWrite(download_root=str(download_root)))
            loaded = get_app_settings()

        self.assertEqual(result.download_root, str(download_root.resolve()))
        self.assertEqual(loaded.download_root, str(download_root.resolve()))
        self.assertTrue(download_root.exists())

    def test_default_download_root_uses_documents_panelstack_downloads(self) -> None:
        self.assertEqual(
            _default_download_root(),
            Path.home().joinpath("Documents", "panelstack-downloads").resolve(),
        )

    def test_parse_download_output_accepts_current_cli_labels(self) -> None:
        root = Path(self.temp_dir.name)
        archive = root / "sample.cbz"
        extracted = root / "sample"
        archive.touch()
        extracted.mkdir()
        parsed = _parse_download_output(
            f"Title: Sample\nArchive:  {archive}\nExtract:  {extracted}\nPages: 2 image files\n"
        )

        self.assertEqual(parsed, [str(archive.resolve()), str(extracted.resolve())])


if __name__ == "__main__":
    unittest.main()
