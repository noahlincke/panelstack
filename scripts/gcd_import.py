#!/usr/bin/env python3
"""Run the GCD import.

Local backfill, resumable across runs — each invocation picks up at the cursor:

    python3 scripts/gcd_import.py backfill --max-requests 45

Weekly reconciliation, which is what CI runs:

    python3 scripts/gcd_import.py reconcile --weeks-back 2

GCD throttles anonymous clients to roughly an hour's window, so both modes stop
cleanly when told to back off and leave the cursor where it was.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.db import SessionLocal, engine  # noqa: E402
from backend.app.models import Base  # noqa: E402
from backend.app.services.gcd import (  # noqa: E402
    GcdThrottledError,
    build_fetcher,
    reconcile_recent_weeks,
    run_backfill,
)

DEFAULT_START = (2019, 1)
DEFAULT_END = (2026, 35)


def parse_week(value: str) -> tuple[int, int]:
    year, week = value.split("-W", 1)
    return int(year), int(week)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    backfill = subparsers.add_parser("backfill", help="Walk history forward from the saved cursor.")
    backfill.add_argument("--start", type=parse_week, default=DEFAULT_START, metavar="YYYY-Www")
    backfill.add_argument("--end", type=parse_week, default=DEFAULT_END, metavar="YYYY-Www")
    backfill.add_argument("--max-weeks", type=int, default=None)
    backfill.add_argument("--max-requests", type=int, default=45, help="Budget for one throttle window.")

    reconcile = subparsers.add_parser("reconcile", help="Re-scan the most recent on-sale weeks.")
    reconcile.add_argument("--weeks-back", type=int, default=2)

    args = parser.parse_args()
    Base.metadata.create_all(bind=engine)
    fetch = build_fetcher()

    with SessionLocal() as db:
        if args.mode == "backfill":
            result = run_backfill(
                db,
                start=args.start,
                end=args.end,
                fetch=fetch,
                max_weeks=args.max_weeks,
                max_requests=args.max_requests,
            )
            print(json.dumps(result.__dict__, default=str, indent=2))
            return 0

        try:
            results = reconcile_recent_weeks(db, weeks_back=args.weeks_back, fetch=fetch)
        except GcdThrottledError as throttle:
            print(json.dumps({"throttled_for_seconds": throttle.retry_after_seconds}, indent=2))
            # Throttling is expected capacity management, not a failure.
            return 0
        print(json.dumps([result.__dict__ for result in results], default=str, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
