# Architecture

## Direction

This project is moving from a one-off downloader into a library + web app with three layers:

- `comics.py`: legacy-compatible downloader CLI
- `backend/`: FastAPI + SQLite API for library metadata, reading paths, and ingest jobs
- `frontend/`: React + TypeScript web UI for browsing series, reading paths, and pages

## Initial Goals

1. Keep the downloader working as a standalone tool.
2. Index downloaded archives into a local library database.
3. Expose a clean API for series, issues, archives, canonical issues, events, and reading paths.
4. Build a minimal web UI with a calm browsing surface and reader.
5. Attach local files to canonical continuity data so "what should I read next?" becomes answerable.

## Core Entities

- Local library:
  - `series`: imported local series record
  - `issues`: imported local issue record
  - `archives`: local file representation for an issue or collection
- Canonical catalog:
  - `publishers`
  - `events`
  - `story_arcs`
  - `canonical_series`
  - `canonical_issues`
- Mapping:
  - `issue_matches`: local issue -> canonical issue links with confidence and strategy
- Curation:
  - `reading_paths`
  - `reading_path_entries`: ordered canonical references inside a path

## Backend Shape

- FastAPI app with routers for:
  - `/health`
  - `/library`
  - `/series`
  - `/issues`
  - `/publishers`
  - `/events`
  - `/story-arcs`
  - `/canonical-series`
  - `/canonical-issues`
  - `/reading-paths`
  - `/ingest`

SQLite is the default local store. SQLAlchemy is the ORM layer for now.

Curated reading data is checked into the repo as JSON and synced into SQLite on startup. That keeps the editorial layer versioned with the codebase instead of living only in an admin panel.

## Frontend Shape

- `Library`: overview dashboard and browsing entrypoint
- `Series`: issue list and metadata
- `Reading Paths`: curated sequences and event guides
- `Viewer`: minimal image/page reader with keyboard navigation

## Immediate Next Steps

1. Show canonical events, arcs, and path details in the frontend.
2. Improve issue matching heuristics beyond plain title + issue number.
3. Support broader curated datasets and source attribution.
4. Add PDF rendering or conversion for viewer compatibility.
