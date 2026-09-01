"""Disk budget for the cover caches.

Covers accumulate one small file per issue and nothing ever removed them, so the
three cache directories grew without bound on a host that is already 86% full.
This keeps their combined size under a budget by evicting least-recently-used
files, and refuses to cache at all when the filesystem itself is nearly full.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
CACHE_ROOT = BASE_DIR / "data" / "cache"

COVER_CACHE_DIRS = (
    CACHE_ROOT / "reading_path_covers",
    CACHE_ROOT / "reading_path_entry_covers",
    CACHE_ROOT / "remote_covers",
)

DEFAULT_COVER_CACHE_MAX_BYTES = 256 * 1024 * 1024
# Below this much free space, caching a cover is not worth the risk to the host.
DEFAULT_MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024

MAX_BYTES_ENV = "PANELSTACK_COVER_CACHE_MAX_BYTES"
MIN_FREE_ENV = "PANELSTACK_COVER_CACHE_MIN_FREE_BYTES"


@dataclass(frozen=True)
class CoverCacheUsage:
    total_bytes: int
    file_count: int
    max_bytes: int
    free_bytes: int


def _env_bytes(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def cover_cache_max_bytes() -> int:
    return _env_bytes(MAX_BYTES_ENV, DEFAULT_COVER_CACHE_MAX_BYTES)


def cover_cache_min_free_bytes() -> int:
    return _env_bytes(MIN_FREE_ENV, DEFAULT_MIN_FREE_BYTES)


def _cached_files(dirs: tuple[Path, ...]) -> list[tuple[Path, int, float]]:
    files: list[tuple[Path, int, float]] = []
    for directory in dirs:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            stat = path.stat()
            files.append((path, stat.st_size, stat.st_atime))
    return files


def cover_cache_usage(dirs: tuple[Path, ...] = COVER_CACHE_DIRS) -> CoverCacheUsage:
    files = _cached_files(dirs)
    root = next((directory for directory in dirs if directory.exists()), CACHE_ROOT)
    while not root.exists() and root != root.parent:
        root = root.parent
    return CoverCacheUsage(
        total_bytes=sum(size for _, size, _ in files),
        file_count=len(files),
        max_bytes=cover_cache_max_bytes(),
        free_bytes=shutil.disk_usage(root).free,
    )


def has_room_for_covers(dirs: tuple[Path, ...] = COVER_CACHE_DIRS) -> bool:
    """False when the filesystem is too tight to justify caching another cover."""
    return cover_cache_usage(dirs).free_bytes > cover_cache_min_free_bytes()


def prune_cover_cache(
    *,
    max_bytes: int | None = None,
    dirs: tuple[Path, ...] = COVER_CACHE_DIRS,
) -> int:
    """Evict least-recently-used covers until the caches fit the budget.

    Returns the number of bytes reclaimed.
    """
    limit = max_bytes if max_bytes is not None else cover_cache_max_bytes()
    files = _cached_files(dirs)
    total = sum(size for _, size, _ in files)
    if total <= limit:
        return 0

    reclaimed = 0
    for path, size, _ in sorted(files, key=lambda item: item[2]):
        if total - reclaimed <= limit:
            break
        path.unlink(missing_ok=True)
        reclaimed += size
    return reclaimed
