from __future__ import annotations

import mimetypes
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..models import Archive
from .archive_tools import ArchiveToolError, extract_rar_member_bytes, rar_support_available
from .ingest import scan_source


@dataclass(frozen=True)
class ArchivePage:
    index: int
    relative_path: str
    media_type: str


def media_type_for(path: str) -> str:
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def archive_root(archive: Archive) -> Path:
    if archive.extracted_path:
        return Path(archive.extracted_path)
    return Path(archive.storage_path)


def archive_is_streamable(archive: Archive) -> bool:
    if archive.archive_format == "rar":
        return rar_support_available()
    return archive.archive_format in {"directory", "image", "zip"}


def list_archive_pages(archive: Archive) -> list[ArchivePage]:
    if not archive_is_streamable(archive):
        raise ValueError(f"Archive format '{archive.archive_format}' is indexed but not streamable in the viewer.")
    scan = scan_source(archive_root(archive))
    return [
        ArchivePage(
            index=page.index,
            relative_path=page.relative_path,
            media_type=media_type_for(page.relative_path),
        )
        for page in scan.pages
    ]


def archive_page_bytes(archive: Archive, page_number: int) -> tuple[bytes, str, str]:
    if not archive_is_streamable(archive):
        raise ValueError(f"Archive format '{archive.archive_format}' is indexed but not streamable in the viewer.")
    pages = list_archive_pages(archive)
    if page_number < 1 or page_number > len(pages):
        raise IndexError(f"Page {page_number} is out of range.")

    page = pages[page_number - 1]
    root = archive_root(archive)

    if root.is_dir():
        path = root / page.relative_path
        return path.read_bytes(), page.media_type, path.name

    if root.is_file() and root.suffix.lower() in {".cbz", ".zip"}:
        with zipfile.ZipFile(root) as zipped:
            return zipped.read(page.relative_path), page.media_type, Path(page.relative_path).name

    if root.is_file() and root.suffix.lower() in {".cbr", ".rar"}:
        try:
            return extract_rar_member_bytes(root, page.relative_path), page.media_type, Path(page.relative_path).name
        except ArchiveToolError as exc:
            raise FileNotFoundError(str(exc)) from exc

    if root.is_file():
        return root.read_bytes(), page.media_type, root.name

    raise FileNotFoundError(f"Archive source is not available: {root}")
