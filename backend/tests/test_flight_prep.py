from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from backend.app.services.flight_prep import (
    FlightPrepQueue,
    FlightPrepTarget,
    QueueItem,
    destination_space,
    resolve_targets,
)


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for the queue.")


class DestinationSpaceTests(unittest.TestCase):
    def test_reports_space_for_an_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            space = destination_space(Path(temp_dir))
            self.assertTrue(space.exists)
            self.assertGreater(space.total_bytes, 0)
            self.assertGreater(space.free_bytes, 0)

    def test_walks_up_to_the_nearest_existing_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "not" / "created" / "yet"
            space = destination_space(target)
            self.assertFalse(space.exists)
            self.assertEqual(space.path, str(target))
            self.assertGreater(space.free_bytes, 0)


class ResolveTargetsTests(unittest.TestCase):
    def _target(self, entry_id: int) -> FlightPrepTarget:
        return FlightPrepTarget(reading_path_id=1, entry_id=entry_id, title=f"Issue {entry_id}")

    def test_keeps_caller_order_and_records_sizes(self) -> None:
        targets = [self._target(index) for index in range(6)]
        resolved = resolve_targets(targets, lambda t: (t.entry_id * 1000, f"https://example.test/{t.entry_id}"))
        self.assertEqual([item.entry_id for item in resolved], list(range(6)))
        self.assertEqual([item.size_bytes for item in resolved], [index * 1000 for index in range(6)])
        self.assertTrue(all(item.status == "ready" for item in resolved))

    def test_a_failing_source_does_not_fail_the_batch(self) -> None:
        def resolver(target: FlightPrepTarget) -> tuple[int | None, str | None]:
            if target.entry_id == 1:
                raise RuntimeError("mirror refused")
            return 10, "https://example.test/ok"

        resolved = resolve_targets([self._target(0), self._target(1), self._target(2)], resolver)
        self.assertEqual([item.status for item in resolved], ["ready", "unavailable", "ready"])
        self.assertEqual(resolved[1].detail, "mirror refused")

    def test_a_target_without_a_source_is_unavailable(self) -> None:
        resolved = resolve_targets([self._target(0)], lambda t: (None, None))
        self.assertEqual(resolved[0].status, "unavailable")


class FlightPrepQueueTests(unittest.TestCase):
    def _items(self, count: int) -> list[QueueItem]:
        return [
            QueueItem(reading_path_id=1, entry_id=index, title=f"Issue {index}", size_bytes=100)
            for index in range(count)
        ]

    def test_downloads_run_one_at_a_time(self) -> None:
        queue = FlightPrepQueue()
        concurrent = []
        active = threading.Semaphore(1)

        def runner(item: QueueItem) -> None:
            concurrent.append(active.acquire(blocking=False))
            time.sleep(0.01)
            active.release()
            return None

        queue.start(destination=Path("/tmp"), items=self._items(4), runner=runner)
        wait_until(lambda: queue.snapshot().status == "complete")
        self.assertTrue(all(concurrent), "queue ran downloads in parallel")
        self.assertEqual([item.status for item in queue.snapshot().items], ["done"] * 4)

    def test_a_failed_item_does_not_stop_the_queue(self) -> None:
        queue = FlightPrepQueue()

        def runner(item: QueueItem) -> None:
            if item.entry_id == 1:
                raise RuntimeError("mirror refused")
            return None

        queue.start(destination=Path("/tmp"), items=self._items(3), runner=runner)
        wait_until(lambda: queue.snapshot().status == "complete")
        snapshot = queue.snapshot()
        self.assertEqual([item.status for item in snapshot.items], ["done", "failed", "done"])
        self.assertEqual(snapshot.items[1].detail, "mirror refused")
        self.assertEqual(snapshot.completed_count, 3)

    def test_cancel_marks_the_remaining_items_skipped(self) -> None:
        queue = FlightPrepQueue()
        started = threading.Event()

        def runner(item: QueueItem) -> None:
            started.set()
            time.sleep(0.05)
            return None

        queue.start(destination=Path("/tmp"), items=self._items(5), runner=runner)
        started.wait(timeout=2)
        queue.cancel()
        wait_until(lambda: queue.snapshot().status == "cancelled")
        statuses = [item.status for item in queue.snapshot().items]
        self.assertIn("skipped", statuses)
        self.assertNotIn("pending", statuses)

    def test_a_second_queue_is_refused_while_one_runs(self) -> None:
        queue = FlightPrepQueue()
        release = threading.Event()
        queue.start(destination=Path("/tmp"), items=self._items(1), runner=lambda item: release.wait(timeout=2))
        wait_until(queue.is_running)
        with self.assertRaises(RuntimeError):
            queue.start(destination=Path("/tmp"), items=self._items(1), runner=lambda item: None)
        release.set()
        wait_until(lambda: queue.snapshot().status == "complete")

    def test_no_queue_reports_no_snapshot(self) -> None:
        self.assertIsNone(FlightPrepQueue().snapshot())


if __name__ == "__main__":
    unittest.main()
