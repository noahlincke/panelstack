#!/usr/bin/env python3
"""Find issues the catalogue invented, and show why it thinks so.

The catalogue was generated with a guessed issue count per series and monthly
dates running to the present, so a finished run keeps sprouting issues. Batman
(2016) ended at #163 and the catalogue carried it to #176, which produced two
phantom volumes and — because a cover search for a comic that does not exist
still returns something — put an unrelated book's cover on the shelf.

Whether an issue is invented depends entirely on whether the volume was matched
correctly, so nothing here is a verdict on its own. A series is only judged when
the catalogue's run sits *inside* the run Metron describes: every issue we hold
below Metron's last one is confirmed, and Metron's series is at least as long as
ours. When our run is longer than Metron's entire series we matched the wrong
volume — our Daredevil (2001) is the 58-issue Bendis run and matched an 8-issue
Metron series — and that gets reported for review rather than pruned.

No API calls; scripts/metron_import.py already recorded what it matched.

    python3 scripts/audit_catalog.py
    python3 scripts/audit_catalog.py --prune
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.app.db import SessionLocal  # noqa: E402
from backend.app.models import (  # noqa: E402
    CanonicalIssue,
    CanonicalIssueSource,
    CanonicalSeries,
    CanonicalSeriesSource,
    Publisher,
    ReadingListItem,
    ReadingPathEntry,
)
from backend.app.services.library import issue_sort_order  # noqa: E402


@dataclass
class SeriesAudit:
    series: CanonicalSeries
    publisher: str
    total: int = 0
    match: CanonicalSeriesSource | None = None
    phantoms: list[CanonicalIssue] = field(default_factory=list)
    verdict: str = "unmatched"
    reason: str = ""


def audit(db: Session) -> list[SeriesAudit]:
    sourced_ids = set(db.scalars(select(CanonicalIssueSource.canonical_issue_id)))
    matches = {
        row.canonical_series_id: row
        for row in db.scalars(
            select(CanonicalSeriesSource).where(CanonicalSeriesSource.source_name == "Metron")
        )
    }
    results: list[SeriesAudit] = []

    for series in db.scalars(select(CanonicalSeries).order_by(CanonicalSeries.title)):
        publisher = db.get(Publisher, series.publisher_id)
        issues = list(db.scalars(select(CanonicalIssue).where(CanonicalIssue.series_id == series.id)))
        if not issues:
            continue

        entry = SeriesAudit(series, publisher.slug if publisher else "?", total=len(issues))
        entry.match = matches.get(series.id)
        results.append(entry)

        if entry.match is None or not entry.match.source_last_issue_number:
            entry.reason = "no Metron match"
            continue

        cutoff = issue_sort_order(entry.match.source_last_issue_number)
        ours = [i for i in issues if "-" not in (i.issue_number or "")]
        within = [i for i in ours if issue_sort_order(i.issue_number) <= cutoff]
        beyond = [i for i in ours if issue_sort_order(i.issue_number) > cutoff]
        unconfirmed_within = [i for i in within if i.id not in sourced_ids]

        if unconfirmed_within:
            # Metron does not know issues we hold from inside its own run, so the
            # two disagree about what this series even is.
            entry.verdict = "review"
            entry.reason = (
                f"{len(unconfirmed_within)} of our issues at or below Metron's #"
                f"{entry.match.source_last_issue_number} are not in it"
            )
            continue

        entry.phantoms = sorted(beyond, key=lambda i: issue_sort_order(i.issue_number))
        entry.verdict = "clean" if not entry.phantoms else "invented"
        entry.reason = (
            f"Metron: {entry.match.source_series_title} ({entry.match.source_year_began}), "
            f"{entry.match.source_issue_count} issues through #{entry.match.source_last_issue_number}"
        )
    return results


def prune(db: Session, phantoms: list[CanonicalIssue]) -> tuple[int, int, int]:
    """Delete invented issues and everything hanging off them.

    A reading list points at a reading path *entry*, not at the issue, and SQLite
    does not enforce the cascade unless foreign keys are switched on — which they
    are not by default. So the list items go first, explicitly, or the Lists page
    is left pointing at rows that no longer exist.
    """
    ids = [issue.id for issue in phantoms]
    if not ids:
        return 0, 0, 0
    entry_ids = list(
        db.scalars(select(ReadingPathEntry.id).where(ReadingPathEntry.canonical_issue_id.in_(ids)))
    )
    items = db.execute(delete(ReadingListItem).where(ReadingListItem.reading_path_entry_id.in_(entry_ids))) if entry_ids else None
    entries = db.execute(delete(ReadingPathEntry).where(ReadingPathEntry.canonical_issue_id.in_(ids)))
    issues = db.execute(delete(CanonicalIssue).where(CanonicalIssue.id.in_(ids)))
    db.commit()
    return issues.rowcount, entries.rowcount, (items.rowcount if items is not None else 0)


def _numbers(issues: list[CanonicalIssue], limit: int = 8) -> str:
    shown = ", ".join(f"#{i.issue_number}" for i in issues[:limit])
    return shown + (f" (+{len(issues) - limit} more)" if len(issues) > limit else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prune", action="store_true", help="Delete the invented issues.")
    args = parser.parse_args()

    with SessionLocal() as db:
        results = audit(db)
        invented = [r for r in results if r.verdict == "invented"]
        review = [r for r in results if r.verdict == "review"]
        unmatched = [r for r in results if r.verdict == "unmatched"]
        clean = [r for r in results if r.verdict == "clean"]

        print(f"{len(results)} series with issues: {len(clean)} clean, {len(invented)} with invented "
              f"issues, {len(review)} to review, {len(unmatched)} with no Metron match.\n")

        if invented:
            print("INVENTED — our run runs past the end of the matched Metron series:")
            for entry in sorted(invented, key=lambda r: -len(r.phantoms)):
                print(f"  {entry.series.title} ({entry.series.start_year or '—'}): {_numbers(entry.phantoms)}")
                print(f"      {entry.reason}")

        if review:
            print("\nREVIEW — Metron disagrees about the shape of these, so nothing is pruned:")
            for entry in review:
                print(f"  {entry.series.title} ({entry.series.start_year or '—'}), {entry.total} issues"
                      f" — matched {entry.match.source_series_title} ({entry.match.source_year_began}),"
                      f" {entry.match.source_issue_count} issues; {entry.reason}")

        if unmatched:
            print(f"\nNO METRON MATCH ({len(unmatched)}) — mostly manga, which Metron does not carry:")
            for entry in unmatched:
                print(f"  {entry.series.title} ({entry.series.start_year or '—'}) — {entry.total} issues, {entry.publisher}")

        total = sum(len(r.phantoms) for r in invented)
        print(f"\n{total} invented issues across {len(invented)} series.")
        if args.prune and total:
            issues, entries, items = prune(db, [i for r in invented for i in r.phantoms])
            print(f"Deleted {issues} issues, {entries} reading-path entries, {items} reading-list items.")
        elif total:
            print("Re-run with --prune to delete them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
