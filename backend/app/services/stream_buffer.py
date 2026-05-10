from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
from typing import Iterable

from ..models import Archive
from .ingest import scan_source

BASE_DIR = Path(__file__).resolve().parents[2]
STREAM_BUFFER_DIR = BASE_DIR / "data" / "cache" / "stream_buffer"
DEFAULT_STREAM_BUFFER_MAX_BYTES = 500 * 1024 * 1024


class StreamBufferTooLargeError(RuntimeError):
    pass


def stream_buffer_root(cache_root: Path | None = None) -> Path:
    return (cache_root or STREAM_BUFFER_DIR).resolve()


def stream_buffer_max_bytes() -> int:
    configured = os.getenv("PANELSTACK_STREAM_BUFFER_MAX_BYTES")
    if not configured:
        return DEFAULT_STREAM_BUFFER_MAX_BYTES
    try:
        return max(1, int(configured))
    except ValueError:
        return DEFAULT_STREAM_BUFFER_MAX_BYTES


def stream_buffer_key(reading_path_id: int, entry_id: int) -> str:
    return f"reading-path-{reading_path_id}-entry-{entry_id}"


def store_stream_archive(
    cache_key: str,
    filename: str,
    chunks: Iterable[bytes],
    *,
    cache_root: Path | None = None,
    max_bytes: int | None = None,
    source_url: str | None = None,
) -> Archive:
    root = stream_buffer_root(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    limit = max_bytes or stream_buffer_max_bytes()
    prune_stream_buffer(max_bytes=limit, cache_root=root)

    entry_dir = root / _safe_cache_key(cache_key)
    temp_dir = root / f".{entry_dir.name}.tmp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    if entry_dir.exists():
        shutil.rmtree(entry_dir, ignore_errors=True)

    temp_dir.mkdir(parents=True, exist_ok=True)
    archive_path = temp_dir / filename
    with archive_path.open("wb") as handle:
        for chunk in chunks:
            if chunk:
                handle.write(chunk)

    temp_dir.replace(entry_dir)
    archive_path = entry_dir / filename
    _touch_entry(entry_dir, archive_path)
    archive = _archive_for_path(archive_path, source_url=source_url, status="buffered")
    if (archive.size_bytes or 0) > limit:
        shutil.rmtree(entry_dir, ignore_errors=True)
        raise StreamBufferTooLargeError("Archive exceeds the configured stream buffer size limit.")
    prune_stream_buffer(max_bytes=limit, cache_root=root, exclude_keys={cache_key})
    return archive


def find_stream_archive(cache_key: str, *, cache_root: Path | None = None) -> Archive | None:
    root = stream_buffer_root(cache_root)
    entry_dir = root / _safe_cache_key(cache_key)
    if not entry_dir.exists() or not entry_dir.is_dir():
        return None

    files = sorted(candidate for candidate in entry_dir.iterdir() if candidate.is_file())
    if not files:
        return None

    archive_path = files[0]
    _touch_entry(entry_dir, archive_path)
    return _archive_for_path(archive_path, status="buffered")


def prune_stream_buffer(
    *,
    max_bytes: int | None = None,
    cache_root: Path | None = None,
    exclude_keys: set[str] | None = None,
) -> None:
    root = stream_buffer_root(cache_root)
    if not root.exists():
        return

    entries = _buffer_entries(root)
    total_bytes = sum(size_bytes for _, size_bytes, _ in entries)
    limit = max_bytes or stream_buffer_max_bytes()
    excluded = {_safe_cache_key(value) for value in (exclude_keys or set())}

    for entry_dir, size_bytes, _ in sorted(entries, key=lambda item: item[2]):
        if total_bytes <= limit:
            break
        if entry_dir.name in excluded:
            continue
        shutil.rmtree(entry_dir, ignore_errors=True)
        total_bytes -= size_bytes


def _archive_for_path(path: Path, *, source_url: str | None = None, status: str = "buffered") -> Archive:
    scan = scan_source(path)
    archive_format = scan.archive_format or ("directory" if path.is_dir() else path.suffix.lower().lstrip("."))
    size_bytes = path.stat().st_size if path.is_file() else _directory_size(path)
    return Archive(
        storage_path=str(path.resolve()),
        original_filename=path.name,
        source_url=source_url,
        archive_format=archive_format or "bin",
        page_count=scan.page_count,
        size_bytes=size_bytes,
        status=status,
    )


def _safe_cache_key(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-")
    return normalized or "stream-buffer"


def _buffer_entries(root: Path) -> list[tuple[Path, int, float]]:
    entries: list[tuple[Path, int, float]] = []
    for candidate in root.iterdir():
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        size_bytes = _directory_size(candidate)
        touched_at = max(
            [candidate.stat().st_mtime, *[child.stat().st_mtime for child in candidate.rglob("*")]],
            default=candidate.stat().st_mtime,
        )
        entries.append((candidate, size_bytes, touched_at))
    return entries


def _directory_size(path: Path) -> int:
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _touch_entry(entry_dir: Path, archive_path: Path) -> None:
    entry_dir.mkdir(parents=True, exist_ok=True)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    os.utime(entry_dir, None)
    if archive_path.exists():
        os.utime(archive_path, None)
