#!/usr/bin/env python3
"""Fetch and cache covers for collections that do not have one yet.

GetComics rate-limits, so this runs serially with a pause and can be re-run: it
only touches collections still missing a cover, and each run can be capped.

    python3 scripts/backfill_covers.py --limit 40
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from backend.app.db import SessionLocal, engine  # noqa: E402
from backend.app.models import Base, CanonicalIssue, ReadingPath, ReadingPathEntry  # noqa: E402

DEFAULT_DELAY_SECONDS = 2.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=40, help="Collections to attempt in this run.")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)

    # Imported here so the module-level env has already been applied.
    from backend.app.main import (  # noqa: PLC0415
        _reading_path_cover_context,
        _reading_path_cover_query,
        _reading_path_ready_cover_url,
    )
    from backend.app.services.covers import ensure_reading_path_cover_asset  # noqa: PLC0415

    attempted = resolved = skipped = 0
    with SessionLocal() as db:
        paths = db.scalars(
            select(ReadingPath)
            .options(
                selectinload(ReadingPath.cover_asset),
                selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.canonical_issue),
                selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue),
            )
            .order_by(ReadingPath.id.asc())
        ).all()

        for reading_path in paths:
            if attempted >= args.limit:
                break
            if _reading_path_ready_cover_url(reading_path):
                skipped += 1
                continue
            series_title, issue_number, year = _reading_path_cover_context(reading_path)
            query = _reading_path_cover_query(reading_path)
            if not query:
                skipped += 1
                continue

            attempted += 1
            try:
                ensure_reading_path_cover_asset(
                    db,
                    reading_path_id=reading_path.id,
                    query=query,
                    expected_series_title=series_title,
                    expected_issue_number=issue_number,
                    expected_year=year,
                )
            except Exception as exc:  # noqa: BLE001 - one bad cover must not stop the run
                print(f"  {reading_path.slug}: {exc}")
            else:
                db.commit()
                if _reading_path_ready_cover_url(reading_path):
                    resolved += 1
                    print(f"  {reading_path.slug}: cover cached")
                else:
                    print(f"  {reading_path.slug}: no cover found")
            time.sleep(args.delay)

    print(f"\nattempted {attempted}, resolved {resolved}, already had covers {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
