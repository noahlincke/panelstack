from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.main import _should_proxy_provider_cover_url
from backend.app.services.cover_cache import (
    MAX_BYTES_ENV,
    MIN_FREE_ENV,
    cover_cache_max_bytes,
    cover_cache_min_free_bytes,
    cover_cache_usage,
    has_room_for_covers,
    prune_cover_cache,
)


class CoverCacheBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.dirs = (Path(self.temp.name) / "a", Path(self.temp.name) / "b")
        for directory in self.dirs:
            directory.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, directory: Path, name: str, size: int, atime: float) -> Path:
        path = directory / name
        path.write_bytes(b"x" * size)
        os.utime(path, (atime, atime))
        return path

    def test_usage_sums_every_cache_directory(self) -> None:
        self._write(self.dirs[0], "one.jpg", 100, time.time())
        self._write(self.dirs[1], "two.jpg", 250, time.time())
        usage = cover_cache_usage(self.dirs)
        self.assertEqual(usage.total_bytes, 350)
        self.assertEqual(usage.file_count, 2)

    def test_prune_is_a_no_op_under_budget(self) -> None:
        self._write(self.dirs[0], "one.jpg", 100, time.time())
        self.assertEqual(prune_cover_cache(max_bytes=1000, dirs=self.dirs), 0)
        self.assertEqual(cover_cache_usage(self.dirs).file_count, 1)

    def test_prune_evicts_least_recently_used_first(self) -> None:
        now = time.time()
        oldest = self._write(self.dirs[0], "oldest.jpg", 100, now - 3000)
        middle = self._write(self.dirs[0], "middle.jpg", 100, now - 2000)
        newest = self._write(self.dirs[1], "newest.jpg", 100, now)

        reclaimed = prune_cover_cache(max_bytes=150, dirs=self.dirs)

        self.assertGreaterEqual(reclaimed, 150)
        self.assertFalse(oldest.exists())
        self.assertFalse(middle.exists())
        self.assertTrue(newest.exists())

    def test_prune_stops_once_inside_the_budget(self) -> None:
        now = time.time()
        for index in range(4):
            self._write(self.dirs[0], f"cover-{index}.jpg", 100, now - (100 * (4 - index)))
        prune_cover_cache(max_bytes=250, dirs=self.dirs)
        self.assertLessEqual(cover_cache_usage(self.dirs).total_bytes, 250)
        self.assertEqual(cover_cache_usage(self.dirs).file_count, 2)

    def test_budget_is_configurable(self) -> None:
        with patch.dict(os.environ, {MAX_BYTES_ENV: "1048576"}):
            self.assertEqual(cover_cache_max_bytes(), 1048576)
        with patch.dict(os.environ, {MAX_BYTES_ENV: "not-a-number"}):
            self.assertEqual(cover_cache_max_bytes(), 256 * 1024 * 1024)

    def test_caching_stops_when_the_disk_is_nearly_full(self) -> None:
        with patch.dict(os.environ, {MIN_FREE_ENV: str(2**62)}):
            self.assertGreater(cover_cache_min_free_bytes(), 0)
            self.assertFalse(has_room_for_covers(self.dirs))
        self.assertTrue(has_room_for_covers(self.dirs))


class CoverProxyHostTests(unittest.TestCase):
    def test_publisher_cdns_are_proxied(self) -> None:
        for url in (
            "https://cdn.marvel.com/u/prod/marvel/i/mg/3/90/abc/portrait_uncanny.jpg",
            "https://static.dc.com/2025-03/GL_Cv21.jpg",
            "https://i0.wp.com/getcomics.org/share/uploads/2025/02/Detective.jpg",
        ):
            self.assertTrue(_should_proxy_provider_cover_url(url), url)

    def test_unknown_hosts_are_not_proxied(self) -> None:
        self.assertFalse(_should_proxy_provider_cover_url("https://example.test/cover.jpg"))


if __name__ == "__main__":
    unittest.main()
