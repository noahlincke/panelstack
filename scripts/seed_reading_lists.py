#!/usr/bin/env python3
"""Turn the recommendation brief's sections into reading lists.

Each list is a section of the brief. Collections are resolved by reading-path
slug, and anything that fails to resolve is reported rather than silently
dropped, so a missing catalogue entry is visible.

    python3 scripts/seed_reading_lists.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from backend.app.db import SessionLocal, engine  # noqa: E402
from backend.app.main import _collection_download_entries  # noqa: E402
from backend.app.models import Base, ReadingList, ReadingListItem, ReadingPath, ReadingPathEntry  # noqa: E402

# name -> ordered reading-path slugs.
LISTS: dict[str, list[str]] = {
    "Flight tier 1 — almost guaranteed": [
        "house-of-x-powers-of-x-2019-vol-1",
        "absolute-batman-2024-vol-1",
        "ultimate-spider-man-2024-first-year",
        "worlds-finest-2022-vol-1",
    ],
    "Flight tier 2 — likely rabbit holes": [
        "immortal-x-men-2022-vol-1",
        "x-men-red-2022-vol-1",
        "daredevil-2019-vol-1",
        "green-lantern-2023-vol-1",
        "absolute-wonder-woman-2024-vol-1",
    ],
    "Flight tier 3 — different flavour": [
        "far-sector-2019-vol-1",
        "human-target-2021-vol-1",
        "immortal-hulk-2018-vol-1",
        "supergirl-woman-of-tomorrow-2021-vol-1",
    ],
    "DC shortlist": [
        "absolute-batman-2024-vol-1",
        "worlds-finest-2022-vol-1",
        "green-lantern-2023-vol-1",
        "absolute-wonder-woman-2024-vol-1",
        "absolute-green-lantern-2025-vol-1",
        "nightwing-2021-vol-1",
        "batman-the-knight-2022-vol-1",
        "far-sector-2019-vol-1",
        "supergirl-woman-of-tomorrow-2021-vol-1",
        "human-target-2021-vol-1",
    ],
    "Krakoa era, the selective route": [
        "house-of-x-powers-of-x-2019-vol-1",
        "x-men-2019-vol-1",
        "x-men-2019-vol-2",
        "hellions-2020-vol-1",
        "x-of-swords-2020-vol-1",
        "immortal-x-men-2022-vol-1",
        "immortal-x-men-2022-vol-2",
        "x-men-red-2022-vol-1",
        "x-men-red-2022-vol-2",
        "axe-judgment-day-2022-vol-1",
    ],
    "Krakoa, the full collapse": [
        "fall-of-the-house-of-x-2024-vol-1",
        "rise-of-the-powers-of-x-2024-vol-1",
    ],
    "Marvel outside the X-Men": [
        "daredevil-2019-vol-1",
        "devils-reign-2021-vol-1",
        "daredevil-2022-vol-1",
        "immortal-hulk-2018-vol-1",
        "moon-knight-2021-vol-1",
        "doctor-strange-2023-vol-1",
        "scarlet-witch-2023-vol-1",
    ],
    "The Ultimate Universe, start to finish": [
        "ultimate-invasion-2023-vol-1",
        "ultimate-spider-man-2024-first-year",
        "ultimates-2024-first-wave",
        "ultimate-black-panther-2024-opening-arc",
        "ultimate-x-men-2024-vol-1",
        "ultimate-wolverine-2025-opening-arc",
        "ultimate-endgame-2026-vol-1",
    ],
    "Absolute Universe": [
        "absolute-batman-2024-vol-1",
        "absolute-batman-2024-vol-2",
        "absolute-wonder-woman-2024-vol-1",
        "absolute-superman-2024-vol-1",
        "absolute-flash-2025-vol-1",
        "absolute-green-lantern-2025-vol-1",
        "absolute-martian-manhunter-2025-vol-1",
        "absolute-green-arrow-2026-vol-1",
        "absolute-catwoman-2026-vol-1",
    ],
    "Absolute Batman": [
        "absolute-batman-2024-vol-1",
        "absolute-batman-2024-vol-2",
    ],
    "Just read the damn book": [
        "batman-year-one-1987-vol-1",
        "batman-the-long-halloween-1996-vol-1",
        "batman-the-black-mirror-2010-vol-1",
        "batman-snyder-capullo-2011-vol-1",
        "gotham-central-2003-vol-1",
        "all-star-superman-2005-vol-1",
        "kingdom-come-1996-vol-1",
        "green-lantern-rebirth-2004-vol-1",
        "ultimate-spider-man-2000-vol-1",
        "new-x-men-2001-vol-1",
        "astonishing-x-men-2004-vol-1",
        "uncanny-x-force-2010-vol-1",
        "daredevil-bendis-2001-vol-1",
        "hawkeye-2012-vol-1",
        "vision-2015-vol-1",
    ],
}


def entry_title(entry: ReadingPathEntry) -> str:
    if entry.canonical_issue is not None:
        return entry.canonical_issue.title or f"Issue {entry.canonical_issue.issue_number}"
    return entry.label or f"Entry {entry.id}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Rebuild each seeded list from scratch, dropping items curation no longer produces.",
    )
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    missing: list[str] = []

    with SessionLocal() as db:
        for name, slugs in LISTS.items():
            paths = []
            for slug in slugs:
                path = db.scalars(
                    select(ReadingPath)
                    .options(
                        selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.canonical_issue),
                        selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue),
                    )
                    .where(ReadingPath.slug == slug)
                ).first()
                if path is None:
                    missing.append(f"{name}: {slug}")
                    continue
                paths.append(path)

            downloadable = {path.id: _collection_download_entries(path) for path in paths}
            total_entries = sum(len(entries) for entries in downloadable.values())
            print(f"{name}: {len(paths)}/{len(slugs)} collections, {total_entries} issues")
            if args.dry_run:
                continue

            reading_list = db.scalar(select(ReadingList).where(ReadingList.name == name))
            if reading_list is None:
                reading_list = ReadingList(name=name, description="Seeded from the recommendation brief.")
                db.add(reading_list)
                db.flush()

            if args.replace:
                for stale in list(reading_list.items):
                    db.delete(stale)
                db.flush()
                db.refresh(reading_list)

            existing = {item.reading_path_entry_id for item in reading_list.items}
            sort_order = max((item.sort_order for item in reading_list.items), default=-1) + 1
            for path in paths:
                for entry in downloadable[path.id]:
                    if entry.id in existing:
                        continue
                    db.add(
                        ReadingListItem(
                            reading_list_id=reading_list.id,
                            reading_path_id=path.id,
                            reading_path_entry_id=entry.id,
                            title=entry_title(entry),
                            sort_order=sort_order,
                        )
                    )
                    existing.add(entry.id)
                    sort_order += 1
            db.commit()

    if missing:
        print("\nUnresolved collections:")
        for item in missing:
            print(f"  {item}")
        return 1
    print("\nEvery referenced collection resolved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
