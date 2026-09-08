#!/usr/bin/env python3
"""Build the curated reading lists.

Each list targets one thing: a character's run, a team, a line, or a crossover.
Lists reference whole series rather than individual volumes, and expand to that
series' collected editions plus whatever issues no trade covers yet — so a trade
released later replaces its issues the next time this runs.

    python3 scripts/seed_reading_lists.py [--replace] [--dry-run]
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
from backend.app.main import _series_download_entries  # noqa: E402
from backend.app.models import (  # noqa: E402
    Base,
    CanonicalSeries,
    CatalogCollection,
    Publisher,
    ReadingList,
    ReadingListItem,
    ReadingPath,
    ReadingPathEntry,
)

# Each entry is (series title, start year) so a title reused across eras — three
# different Daredevil runs, two Nightwings — resolves to exactly one of them.
Series = tuple[str, int | None]

LISTS: dict[str, list[Series]] = {
    # --- Characters ------------------------------------------------------
    "Batman: Current Run": [("Batman", 2016)],
    "Detective Comics: Tom Taylor Run": [("Detective Comics", 1937)],
    "Nightwing: Tom Taylor Run": [("Nightwing", 2021)],
    "Superman: Current Run": [("Superman", 2023), ("Action Comics", 2016), ("Superman Unlimited", 2025)],
    "Green Lantern: Current Run": [("Green Lantern", 2023)],
    "Wonder Woman: Tom King Run": [("Wonder Woman", 2023)],
    "The Flash: Current Run": [("The Flash", 2023)],
    "Green Arrow: Current Run": [("Green Arrow", 2023)],
    "Amazing Spider-Man: Current Run": [("The Amazing Spider-Man", 2025)],
    "Daredevil: Chip Zdarsky Run": [
        ("Daredevil", 2019),
        ("Devil's Reign", 2021),
        ("Daredevil", 2022),
    ],
    "Immortal Hulk: Complete": [("Immortal Hulk", 2018)],
    "Moon Knight: Jed MacKay Run": [("Moon Knight", 2021)],
    "Doctor Strange: Jed MacKay Run": [("Doctor Strange", 2023), ("Doctor Strange of Asgard", 2025)],
    "Batman: Scott Snyder Run": [("Batman", 2011)],
    "Batman and Robin: Current Run": [("Batman and Robin", 2023)],
    "Shazam!: Current Run": [("Shazam!", 2023)],
    "Iron Man: Current Run": [("Iron Man", 2024)],
    "Captain America: Current Run": [("Captain America", 2025)],
    "Deadpool: Current Run": [("Deadpool", 2024)],
    "Wolverine: Current Run": [("Wolverine", 2024)],
    "Thor: Immortal Thor Run": [("Immortal Thor", 2023)],
    "Hulk: Current Run": [("Incredible Hulk", 2023)],
    "Daredevil: Complete Modern Run": [
        ("Daredevil", 2001),
        ("Daredevil", 2019),
        ("Devil's Reign", 2021),
        ("Daredevil", 2022),
        ("Daredevil", 2023),
    ],
    "Green Lantern: From Rebirth": [
        ("Green Lantern: Rebirth", 2004),
        ("Far Sector", 2019),
        ("Green Lantern", 2023),
    ],
    # --- Teams -----------------------------------------------------------
    "X-Men: Krakoa Era": [
        ("House of X / Powers of X", 2019),
        ("X-Men", 2019),
        ("Marauders", 2019),
        ("Excalibur", 2019),
        ("X-Force", 2019),
        ("New Mutants", 2019),
        ("Hellions", 2020),
        ("X-Factor", 2020),
        ("X of Swords", 2020),
        ("Immortal X-Men", 2022),
        ("X-Men Red", 2022),
        ("A.X.E.: Judgment Day", 2022),
        ("Fall of the House of X", 2024),
        ("Rise of the Powers of X", 2024),
    ],
    "X-Men: From the Ashes": [
        ("X-Men", 2024),
        ("Uncanny X-Men", 2024),
        ("Exceptional X-Men", 2024),
        ("NYX", 2024),
        ("Phoenix", 2024),
        ("Storm", 2024),
    ],
    "Avengers: Current Run": [("Avengers", 2023), ("West Coast Avengers", 2024)],
    "Justice League Unlimited": [("Justice League Unlimited", 2024)],
    "Fantastic Four: Current Run": [("Fantastic Four", 2022)],
    "Titans: Current Run": [("Titans", 2023)],
    # --- Lines -----------------------------------------------------------
    "Absolute Batman": [("Absolute Batman", 2024)],
    "Absolute Universe": [
        ("Absolute Batman", 2024),
        ("Absolute Wonder Woman", 2024),
        ("Absolute Superman", 2024),
        ("Absolute Flash", 2025),
        ("Absolute Green Lantern", 2025),
        ("Absolute Martian Manhunter", 2025),
        ("Absolute Green Arrow", 2026),
        ("Absolute Catwoman", 2026),
    ],
    "Ultimate Universe": [
        ("Ultimate Invasion", 2023),
        ("Ultimate Spider-Man", 2024),
        ("Ultimates", 2024),
        ("Ultimate Black Panther", 2024),
        ("Ultimate X-Men", 2024),
        ("Ultimate Wolverine", 2025),
        ("Ultimate Endgame", 2026),
    ],
    # --- Crossovers ------------------------------------------------------
    "Absolute Power": [("Absolute Power", 2024)],
    "Dark Nights: Death Metal": [("Dark Nights: Death Metal", 2020)],
    "Dark Crisis on Infinite Earths": [("Dark Crisis on Infinite Earths", 2022)],
    "A.X.E.: Judgment Day": [("A.X.E.: Judgment Day", 2022)],
    "X of Swords": [("X of Swords", 2020)],
    "Devil's Reign": [("Devil's Reign", 2021)],
    "House of M": [("House of M", 2005), ("New Avengers: Illuminati", 2006)],
    "Infinite Crisis": [("Countdown to Infinite Crisis", 2005), ("Crisis on Infinite Earths", 1985)],
    "DCeased": [("DCeased", 2019)],
    # --- Standalone ------------------------------------------------------
    "Batman: Essential Classics": [
        ("Batman: Year One", 1987),
        ("Batman: The Long Halloween", 1996),
        ("Batman: The Black Mirror", 2010),
        ("Batman", 2011),
        ("Gotham Central", 2003),
        ("Batman: The Knight", 2022),
    ],
    "Superman: Essential Classics": [("All-Star Superman", 2005), ("Kingdom Come", 1996)],
    "X-Men: Essential Classics": [
        ("New X-Men", 2001),
        ("Astonishing X-Men", 2004),
        ("Uncanny X-Force", 2010),
    ],
    "Spider-Man: Original Ultimate Run": [("Ultimate Spider-Man", 2000)],
    "Modern Standalone Greats": [
        ("Far Sector", 2019),
        ("The Human Target", 2021),
        ("Supergirl: Woman of Tomorrow", 2021),
        ("Strange Adventures", 2020),
        ("Batman/Superman: World's Finest", 2022),
        ("Hawkeye", 2012),
        ("The Vision", 2015),
        ("Scarlet Witch", 2023),
    ],
}


def entry_title(entry: ReadingPathEntry) -> str:
    if entry.canonical_issue is not None:
        return entry.canonical_issue.title or f"Issue {entry.canonical_issue.issue_number}"
    return entry.label or f"Entry {entry.id}"


def reading_paths_for(db, title: str, start_year: int | None) -> list[ReadingPath]:
    """Every volume of one series, in publication order."""
    stmt = select(CanonicalSeries.id).where(CanonicalSeries.title == title)
    if start_year is not None:
        stmt = stmt.where(CanonicalSeries.start_year == start_year)
    series_ids = list(db.scalars(stmt))
    if not series_ids:
        return []
    path_ids = list(
        db.scalars(
            select(CatalogCollection.reading_path_id)
            .where(
                CatalogCollection.canonical_series_id.in_(series_ids),
                CatalogCollection.reading_path_id.is_not(None),
            )
            .order_by(CatalogCollection.first_published_on.asc(), CatalogCollection.sequence_number.asc())
        )
    )
    if not path_ids:
        return []
    paths = {
        path.id: path
        for path in db.scalars(
            select(ReadingPath)
            .options(
                selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.canonical_issue),
                selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue),
            )
            .where(ReadingPath.id.in_(path_ids))
        )
    }
    return [paths[pid] for pid in path_ids if pid in paths]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace", action="store_true", help="Rebuild each list from scratch.")
    parser.add_argument("--prune", action="store_true", help="Delete lists this script no longer defines.")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    missing: list[str] = []

    with SessionLocal() as db:
        if args.prune and not args.dry_run:
            for stale in db.scalars(select(ReadingList).where(ReadingList.name.not_in(list(LISTS)))):
                print(f"pruning {stale.name!r}")
                db.delete(stale)
            db.commit()

        for name, series_refs in LISTS.items():
            grouped: list[list[ReadingPath]] = []
            for title, year in series_refs:
                found = reading_paths_for(db, title, year)
                if not found:
                    missing.append(f"{name}: {title} ({year})")
                    continue
                grouped.append(found)
            paths = [path for series_paths in grouped for path in series_paths]

            # Coverage is worked out per series, so a trade on one volume still
            # stands in for issues that live on the next one.
            shelf = [pair for series_paths in grouped for pair in _series_download_entries(series_paths)]
            print(f"{name}: {len(paths)} volumes, {len(shelf)} items")
            if args.dry_run:
                continue

            reading_list = db.scalar(select(ReadingList).where(ReadingList.name == name))
            if reading_list is None:
                reading_list = ReadingList(name=name, description="Curated by Panel Stack.")
                db.add(reading_list)
                db.flush()
            if args.replace:
                for stale_item in list(reading_list.items):
                    db.delete(stale_item)
                db.flush()
                db.refresh(reading_list)

            existing = {item.reading_path_entry_id for item in reading_list.items}
            sort_order = max((item.sort_order for item in reading_list.items), default=-1) + 1
            for path, entry in shelf:
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
        print(f"\n{len(missing)} unresolved series:")
        for item in missing:
            print(f"  {item}")
        return 1
    print("\nEvery referenced series resolved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
