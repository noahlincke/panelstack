#!/usr/bin/env python3
"""Correct the catalogue's issue data against Metron.

The catalogue was generated with guessed issue counts and monthly publication
dates. Metron has the real ones, plus cover art, and unlike the GCD API it
supports filtering, so the whole curated catalogue costs a few hundred requests
instead of hundreds of hours.

Needs METRON_USERNAME and METRON_PASSWORD. Metron authenticates on the account
username, not the email address; an email is normalised to the part before "@".

    python3 scripts/metron_import.py --dry-run
    python3 scripts/metron_import.py --max-requests 200
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.db import SessionLocal  # noqa: E402
from backend.app.services.metron import (  # noqa: E402
    MetronAuthError,
    SeriesResult,
    build_fetcher,
    curated_series,
    run_backfill,
)


def describe(result: SeriesResult) -> str:
    if result.metron_series_id is None:
        return f"  ? {result.title} ({result.start_year or '—'}): not found on Metron"
    parts = []
    if result.issues_created:
        parts.append(f"+{result.issues_created} issues")
    if result.dates_corrected:
        parts.append(f"{result.dates_corrected} dates fixed")
    if result.covers_added:
        parts.append(f"{result.covers_added} covers")
    if result.unmatched_issue_numbers:
        numbers = ", ".join(f"#{n}" for n in result.unmatched_issue_numbers[:8])
        extra = "…" if len(result.unmatched_issue_numbers) > 8 else ""
        parts.append(f"{len(result.unmatched_issue_numbers)} not in Metron ({numbers}{extra})")
    return f"  · {result.title} ({result.start_year or '—'}): {', '.join(parts) or 'already correct'}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List the series that would be imported.")
    parser.add_argument(
        "--max-requests",
        type=int,
        default=None,
        help="Stop cleanly after this many API requests. Roughly 3s each.",
    )
    parser.add_argument("--publisher", action="append", dest="publishers", help="Repeatable, defaults to dc and marvel.")
    args = parser.parse_args()
    publishers = tuple(args.publishers or ("dc", "marvel"))

    with SessionLocal() as db:
        if args.dry_run:
            series = curated_series(db, publishers)
            print(f"{len(series)} curated series would be looked up:")
            for entry in series:
                print(f"  {entry.title} ({entry.start_year or '—'})")
            print(f"\nRoughly {len(series) * 2} requests, about {len(series) * 2 * 3.2 / 60:.0f} minutes.")
            return 0

        try:
            fetch = build_fetcher()
        except MetronAuthError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        try:
            outcome = run_backfill(
                db,
                fetch=fetch,
                publisher_slugs=publishers,
                max_requests=args.max_requests,
                on_series=lambda result: print(describe(result), flush=True),
            )
        except MetronAuthError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    print(
        f"\n{len(outcome.series)} series | +{outcome.issues_created} issues"
        f" | {outcome.dates_corrected} dates corrected | {outcome.covers_added} covers"
        f" | {outcome.requests_made} requests"
    )
    unmatched = sum(len(result.unmatched_issue_numbers) for result in outcome.series)
    if unmatched:
        print(f"{unmatched} issues in the catalogue have no Metron record; these are the extrapolated ones.")
    if outcome.stopped_early:
        print("Stopped early on the request budget. Re-run to continue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
