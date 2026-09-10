#!/usr/bin/env python3
"""Write hand-entered run years onto the series that have none.

MangaPill publishes no chapter dates, so every manga series arrived with a start
year guessed from its slug and no end year at all. The chronology treats a series
with no end year as still running, which stretched finished runs — Jujutsu
Kaisen, JoJolion, Vinland Saga — across every year of the board.

Only series listed in backend/data/curation/series_run_years.json are touched,
and only their year columns. Everything else about the series is left alone.

    python3 scripts/apply_series_run_years.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from backend.app.db import SessionLocal  # noqa: E402
from backend.app.models import CanonicalSeries  # noqa: E402

RUN_YEARS_PATH = REPO_ROOT / "backend" / "data" / "curation" / "series_run_years.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    wanted = json.loads(RUN_YEARS_PATH.read_text())["series"]
    missing: list[str] = []
    changed = 0

    with SessionLocal() as db:
        for slug, years in wanted.items():
            series = db.scalar(select(CanonicalSeries).where(CanonicalSeries.slug == slug))
            if series is None:
                missing.append(slug)
                continue
            start, end = years.get("start_year"), years.get("end_year")
            if (series.start_year, series.end_year) == (start, end):
                continue
            label = f"{series.title}: {series.start_year}-{series.end_year or ''} -> {start}-{end or ''}"
            print(f"  {label}")
            changed += 1
            if not args.dry_run:
                series.start_year = start
                series.end_year = end
        if not args.dry_run:
            db.commit()

    print(f"\n{changed} series updated{' (dry run)' if args.dry_run else ''}.")
    if missing:
        print(f"{len(missing)} listed series are not in the database:")
        for slug in missing:
            print(f"  {slug}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
