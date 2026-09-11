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
import json
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
    CatalogCollection,
    Publisher,
    ReadingListItem,
    ReadingPath,
    ReadingPathEntry,
)
from backend.app.services.curation import CURATION_DATA_PATH  # noqa: E402
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

        same_title = (entry.match.source_series_title or "").strip().lower() == series.title.strip().lower()
        same_year = entry.match.source_year_began == series.start_year
        if not (same_title and same_year):
            # We matched a different book. Our Daredevil (2001) matched Metron's
            # Daredevil (2026); our Black Mirror (2010) matched their 2013 one.
            entry.verdict = "review"
            entry.reason = (
                f"matched {entry.match.source_series_title} ({entry.match.source_year_began}), "
                f"which is not {series.title} ({series.start_year})"
            )
            continue

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


def reject_wrong_volume(entry: SeriesAudit, volumes: list[dict]) -> str | None:
    """A volume of this title exactly as long as our run means we are that one.

    Our "X-Force (2019)" holds 50 issues and matched a 10-issue Metron series of
    the same name and year -- but Metron also has X-Force (2020) with exactly 50.
    Ours is that run, mislabelled, and its issues are real. Same for our
    "Fantastic Four (2022)", which is really their 48-issue 2018 volume.
    """
    if not entry.phantoms:
        return None
    ours_max = _as_int(entry.phantoms[-1].issue_number)
    if ours_max is None:
        return None
    for volume in volumes:
        if (volume.get("issue_count") or 0) == ours_max:
            return f"{volume.get('series')} has exactly {ours_max} issues, so ours is probably that run"
    return None


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
    touched_paths = set(
        db.scalars(
            select(ReadingPathEntry.reading_path_id).where(ReadingPathEntry.canonical_issue_id.in_(ids))
        )
    )
    items = db.execute(delete(ReadingListItem).where(ReadingListItem.reading_path_entry_id.in_(entry_ids))) if entry_ids else None
    entries = db.execute(delete(ReadingPathEntry).where(ReadingPathEntry.canonical_issue_id.in_(ids)))
    issues = db.execute(delete(CanonicalIssue).where(CanonicalIssue.id.in_(ids)))
    db.flush()

    # A volume emptied by the prune has to go as well, or the catalogue rebuilds
    # it as a collection holding nothing — which is what Batman: Vol. 4 was.
    emptied = [
        path_id for path_id in touched_paths
        if not db.scalar(
            select(ReadingPathEntry.id).where(ReadingPathEntry.reading_path_id == path_id).limit(1)
        )
    ]
    if emptied:
        # The shelf entry in the catalogue outlives its reading path — the
        # foreign key says SET NULL and SQLite does not enforce it anyway — so
        # the collection has to be removed by hand or it lingers as a blank tile.
        db.execute(delete(CatalogCollection).where(CatalogCollection.reading_path_id.in_(emptied)))
        db.execute(delete(ReadingPath).where(ReadingPath.id.in_(emptied)))
    db.commit()
    return issues.rowcount, entries.rowcount, (items.rowcount if items is not None else 0)


def prune_curation_seed(phantoms: list[tuple[str, str]], path: Path | None = None) -> tuple[int, int, int]:
    """Take the invented issues out of the curation file too.

    Deleting them from the database alone achieves nothing: the curation file is
    the seed, its sync runs on every app start, and it put all thirteen phantom
    Batman issues straight back. Reading paths left with no entries go as well —
    that is what "Batman: Vol. 4", a volume holding one made-up issue, was.
    """
    data_path = path or CURATION_DATA_PATH
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    doomed = {f"{series_slug}#{number}" for series_slug, number in phantoms}
    by_series: dict[str, set[str]] = {}
    for series_slug, number in phantoms:
        by_series.setdefault(series_slug, set()).add(number)

    removed_issues = 0
    for series in payload.get("series", []):
        wanted = by_series.get(series.get("slug"))
        if not wanted:
            continue
        before = len(series.get("issues", []))
        series["issues"] = [i for i in series.get("issues", []) if i.get("issue_number") not in wanted]
        removed_issues += before - len(series["issues"])

    removed_entries = 0
    kept_paths = []
    removed_paths = 0
    for reading_path in payload.get("reading_paths", []):
        entries = reading_path.get("entries", [])
        before = len(entries)
        reading_path["entries"] = [e for e in entries if e.get("canonical_issue_key") not in doomed]
        removed_entries += before - len(reading_path["entries"])
        if reading_path["entries"] or not before:
            kept_paths.append(reading_path)
        else:
            removed_paths += 1
    payload["reading_paths"] = kept_paths

    data_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return removed_issues, removed_entries, removed_paths


def _as_int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _numbers(issues: list[CanonicalIssue], limit: int = 8) -> str:
    shown = ", ".join(f"#{i.issue_number}" for i in issues[:limit])
    return shown + (f" (+{len(issues) - limit} more)" if len(issues) > limit else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prune", action="store_true", help="Delete the invented issues.")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Ask Metron whether another volume of the same title is exactly as long as our run, "
        "which would mean ours is that volume and its issues are real. Required before --prune.",
    )
    args = parser.parse_args()
    if args.prune and not args.verify:
        print("error: --prune needs --verify; the offline signals alone cannot tell an invented "
              "issue from a mislabelled volume.", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        results = audit(db)
        if args.verify:
            import requests  # noqa: PLC0415
            from backend.app.services.metron import (  # noqa: PLC0415
                METRON_API_ROOT,
                _series_title_of,
                build_fetcher,
            )

            fetch = build_fetcher()
            for entry in [r for r in results if r.verdict == "invented"]:
                payload = fetch(f"{METRON_API_ROOT}/series/?name={requests.utils.quote(entry.series.title)}")
                volumes = [
                    row for row in payload.get("results", [])
                    if _series_title_of(row).lower() == entry.series.title.strip().lower()
                ]
                rejection = reject_wrong_volume(entry, volumes)
                if rejection:
                    entry.verdict = "review"
                    entry.reason = rejection
                    entry.phantoms = []

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
            phantoms = [i for r in invented for i in r.phantoms]
            issues, entries, items = prune(db, phantoms)
            print(f"Deleted {issues} issues, {entries} reading-path entries, {items} reading-list items.")
            seed_issues, seed_entries, seed_paths = prune_curation_seed(
                [(r.series.slug, i.issue_number) for r in invented for i in r.phantoms]
            )
            print(
                f"Curation seed: removed {seed_issues} issues, {seed_entries} entries and "
                f"{seed_paths} now-empty reading paths, so the sync cannot put them back."
            )
        elif total:
            print("Re-run with --prune to delete them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
