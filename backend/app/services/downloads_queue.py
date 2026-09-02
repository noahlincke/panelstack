"""Bulk downloads for a selection of issues.

This is a local-only workflow: it writes archives to a chosen folder on the
machine running the app. The hosted deployment refuses every downloads route,
so nothing here ever runs against lincke.org.
"""
from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

# Size resolution runs serially. Concurrent workers made GetComics return 429 and
# the mirrors return 403, which surfaced as "no source found" for most of a
# selection. One request at a time with a pause between is slower and correct.
SIZE_RESOLUTION_DELAY_SECONDS = 1.2


@dataclass(frozen=True)
class DownloadTarget:
    """One selected issue or collected edition."""

    reading_path_id: int
    entry_id: int
    title: str


@dataclass(frozen=True)
class ResolvedTarget:
    reading_path_id: int
    entry_id: int
    title: str
    size_bytes: int | None = None
    post_url: str | None = None
    status: str = "ready"
    detail: str | None = None


@dataclass(frozen=True)
class DestinationSpace:
    path: str
    total_bytes: int
    free_bytes: int
    exists: bool


@dataclass(frozen=True)
class QueueItem:
    reading_path_id: int
    entry_id: int
    title: str
    size_bytes: int | None
    status: str = "pending"
    detail: str | None = None


@dataclass
class QueueSnapshot:
    id: str
    destination: str
    status: str
    items: list[QueueItem]
    started_at: str
    finished_at: str | None = None
    error: str | None = None

    @property
    def completed_count(self) -> int:
        return sum(1 for item in self.items if item.status in {"done", "skipped", "failed"})


@dataclass
class _QueueState:
    snapshot: QueueSnapshot
    cancel_requested: bool = False
    thread: threading.Thread | None = field(default=None, repr=False)


def destination_space(path: Path) -> DestinationSpace:
    """Report free space for `path`, walking up to the nearest existing parent."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    return DestinationSpace(
        path=str(path),
        total_bytes=usage.total,
        free_bytes=usage.free,
        exists=path.exists(),
    )


def resolve_targets(
    targets: Sequence[DownloadTarget],
    resolver: Callable[[DownloadTarget], tuple[int | None, str | None]],
    *,
    delay_seconds: float = SIZE_RESOLUTION_DELAY_SECONDS,
) -> list[ResolvedTarget]:
    """Resolve each target's real archive size, one at a time, in order."""

    def resolve_one(target: DownloadTarget) -> ResolvedTarget:
        try:
            size_bytes, post_url = resolver(target)
        except Exception as exc:  # noqa: BLE001 - one bad source must not fail the batch
            return ResolvedTarget(
                reading_path_id=target.reading_path_id,
                entry_id=target.entry_id,
                title=target.title,
                status="unavailable",
                detail=str(exc),
            )
        return ResolvedTarget(
            reading_path_id=target.reading_path_id,
            entry_id=target.entry_id,
            title=target.title,
            size_bytes=size_bytes,
            post_url=post_url,
            status="ready" if post_url else "unavailable",
            detail=None if post_url else "No downloadable source was found.",
        )

    resolved: list[ResolvedTarget] = []
    for index, target in enumerate(targets):
        if index and delay_seconds:
            time.sleep(delay_seconds)
        resolved.append(resolve_one(target))
    return resolved


class DownloadQueue:
    """A single sequential download queue for the local app."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: _QueueState | None = None

    def snapshot(self) -> QueueSnapshot | None:
        with self._lock:
            if self._state is None:
                return None
            return replace(self._state.snapshot, items=list(self._state.snapshot.items))

    def is_running(self) -> bool:
        with self._lock:
            return self._state is not None and self._state.snapshot.status == "running"

    def start(
        self,
        *,
        destination: Path,
        items: Sequence[QueueItem],
        runner: Callable[[QueueItem], str | None],
    ) -> QueueSnapshot:
        """Begin downloading `items` one at a time.

        `runner` performs one download and returns an optional detail message. It
        raises to mark the item failed.
        """
        with self._lock:
            if self._state is not None and self._state.snapshot.status == "running":
                raise RuntimeError("A download queue is already running.")
            snapshot = QueueSnapshot(
                id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"),
                destination=str(destination),
                status="running",
                items=list(items),
                started_at=datetime.now(timezone.utc).isoformat(),
            )
            state = _QueueState(snapshot=snapshot)
            self._state = state

        thread = threading.Thread(target=self._drain, args=(state, runner), daemon=True)
        state.thread = thread
        thread.start()
        return snapshot

    def cancel(self) -> None:
        """Stop after the in-flight item finishes; a partial archive is never left behind."""
        with self._lock:
            if self._state is not None:
                self._state.cancel_requested = True

    def _update_item(self, state: _QueueState, index: int, **changes: object) -> None:
        with self._lock:
            state.snapshot.items[index] = replace(state.snapshot.items[index], **changes)

    def _drain(self, state: _QueueState, runner: Callable[[QueueItem], str | None]) -> None:
        for index, item in enumerate(list(state.snapshot.items)):
            with self._lock:
                if state.cancel_requested:
                    break
            self._update_item(state, index, status="downloading")
            try:
                detail = runner(item)
            except Exception as exc:  # noqa: BLE001 - a failed issue must not stop the queue
                self._update_item(state, index, status="failed", detail=str(exc))
                continue
            self._update_item(state, index, status="done", detail=detail)

        with self._lock:
            cancelled = state.cancel_requested
            for index, item in enumerate(state.snapshot.items):
                if item.status in {"pending", "downloading"}:
                    state.snapshot.items[index] = replace(item, status="skipped", detail="Cancelled.")
            state.snapshot.status = "cancelled" if cancelled else "complete"
            state.snapshot.finished_at = datetime.now(timezone.utc).isoformat()


QUEUE = DownloadQueue()
