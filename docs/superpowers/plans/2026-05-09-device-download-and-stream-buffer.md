# Dual Download and Stream Buffer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add client-device downloads for supported sources while keeping in-browser reading backed by a capped, disposable server stream buffer.

**Architecture:** Keep the current local-library import flow available, but stop using it for the visible "download" action. Add a new attachment response for device downloads, a separate buffered-reader path for unresolved remote issues, and frontend browser-local download state that stays out of the server library model.

**Tech Stack:** FastAPI, SQLAlchemy models already in repo, existing `comics.py` mirror resolution, React + Vite frontend, browser `localStorage`.

---

### Task 1: Lock down backend behavior with failing tests

**Files:**
- Modify: `backend/tests/test_library_persistence.py`

- [ ] Add a failing test that a buffered archive can be prepared under a dedicated stream-buffer root and listed through the existing reader helpers without creating a persisted library record.
- [ ] Run: `backend/.venv/bin/python -m unittest backend.tests.test_library_persistence.LibraryPersistenceTests.test_prepare_buffered_archive_for_reader`
- [ ] Add a failing test that stream-buffer pruning removes oldest buffered entries once the configured cap is exceeded and leaves unrelated library files alone.
- [ ] Run: `backend/.venv/bin/python -m unittest backend.tests.test_library_persistence.LibraryPersistenceTests.test_prune_stream_buffer_evicts_oldest_entries`
- [ ] Add a failing test that the new entry download response advertises attachment headers for a supported GetComics-style source and rejects MangaPill device downloads.
- [ ] Run: `backend/.venv/bin/python -m unittest backend.tests.test_library_persistence.LibraryPersistenceTests.test_entry_device_download_headers`

### Task 2: Add backend stream-buffer and device-download primitives

**Files:**
- Create: `backend/app/services/stream_buffer.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/schemas.py`

- [ ] Add a focused stream-buffer service that manages a dedicated cache directory, deterministic entry keys, archive writes, atime/mtime touch, size accounting, and cap-based eviction.
- [ ] Reuse existing `comics.py` resolution helpers for GetComics archive selection so download and stream paths share mirror logic.
- [ ] Add a reader-manifest endpoint for reading-path entries that resolves one of three cases:
  - existing local issue archive
  - MangaPill canonical streaming
  - remote archive fetched into the stream buffer
- [ ] Add a GET attachment endpoint for per-entry device downloads:
  - stream-supported source archives proxy through as attachments
  - MangaPill returns a stream-only error for now
- [ ] Keep existing POST `/reading-paths/.../download` endpoints intact so manual library-import flows do not regress during migration.

### Task 3: Switch frontend reading and download UX

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/types.ts`
- Create: `frontend/src/lib/deviceDownloads.ts`
- Modify: `frontend/src/routes/ReadingPathDetailPage.tsx`
- Modify: `frontend/src/routes/ViewerPage.tsx`
- Modify: `frontend/src/components/ReadingPathPoster.tsx`

- [ ] Add client support for the new reader-manifest endpoint and direct device-download URL generation.
- [ ] Add browser-local download-state helpers keyed by reading-path entry.
- [ ] Remove collection-level visible download actions from poster tiles and detail-page header.
- [ ] Make issue/TPB cover clicks continue to open the viewer, but route unresolved remote entries through the new buffered-reader path instead of server-importing them into My Library.
- [ ] Make the explicit download control trigger a browser download immediately and mark the entry as downloaded locally.
- [ ] Keep delete/remove actions clearing the local downloaded marker so downloads can be triggered again.
- [ ] Improve streamed-open feedback with "Preparing stream" / loading states without exposing server cache details.

### Task 4: Verify integrated behavior

**Files:**
- Modify as needed based on failures discovered during verification

- [ ] Run targeted backend tests for the new stream-buffer and download behavior.
- [ ] Run the full backend suite: `backend/.venv/bin/python -m unittest discover -s backend/tests`
- [ ] Run frontend validation: `npm --prefix frontend run build`
- [ ] Manually verify these flows against a local dev instance if feasible:
  - GetComics entry opens in viewer from the detail page without importing into My Library
  - GetComics entry device-download button triggers a browser download
  - MangaPill entry still streams in viewer
  - MangaPill entry does not offer or complete device download
  - buffer eviction does not remove real library content
