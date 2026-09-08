#!/usr/bin/env python3
"""Write verified collected editions into the curation data.

The generated catalogue assumed each volume's trade collects exactly the issues
in that volume. Publishers do not work that way — Absolute Batman Vol. 2 collects
#7-14 even though the catalogue split the run at #12 — so reading lists offered
issues as loose downloads that a trade already covered.

This reads backend/data/curation/collected_editions.json, and for each edition:

  * upserts the collection issue on its series, keyed by the issue range,
  * removes any other collection issue on that series whose range overlaps it,
  * attaches it as a "collection" entry to the reading path that holds the
    edition's first issue.

    python3 scripts/apply_collected_editions.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CURATION_PATH = REPO_ROOT / "backend" / "data" / "curation" / "reading_paths.json"
EDITIONS_PATH = REPO_ROOT / "backend" / "data" / "curation" / "collected_editions.json"

# Trades sort after the issues they collect so they read as the volume's summary.
COLLECTION_SORT_ORDER = 14500
COLLECTION_ENTRY_SORT_ORDER = 1000


def issue_range(issue_number: str) -> tuple[int, int] | None:
    first, separator, last = issue_number.partition("-")
    if not separator or not first.strip().isdigit() or not last.strip().isdigit():
        return None
    return int(first), int(last)


def apply_edition(payload: dict, edition: dict) -> list[str]:
    """Returns a line per change made, for the run log."""
    changes: list[str] = []
    series_slug = edition["series_slug"]
    series = next((s for s in payload["series"] if s.get("slug") == series_slug), None)
    if series is None:
        return [f"! {series_slug}: no such series"]

    first, last = edition["first_issue"], edition["last_issue"]
    number = f"{first}-{last}"
    key = f"{series_slug}#{number}"

    issues = series.setdefault("issues", [])
    # Any existing trade whose range overlaps this one is the same book recorded
    # wrongly, so it goes rather than sitting alongside the corrected entry.
    stale = [
        issue
        for issue in issues
        if issue.get("issue_kind") == "collection"
        and (span := issue_range(str(issue.get("issue_number", "")))) is not None
        and span != (first, last)
        and span[0] <= last
        and span[1] >= first
    ]
    for issue in stale:
        issues.remove(issue)
        changes.append(f"- {series_slug}: dropped stale trade #{issue['issue_number']}")

    record = {
        "issue_number": number,
        "issue_kind": "collection",
        "title": edition["title"],
        "published_on": edition["published_on"],
        "sort_order": COLLECTION_SORT_ORDER,
        "summary": f"Collects {series.get('title', series_slug)} #{first}-{last}.",
    }
    existing = next((i for i in issues if str(i.get("issue_number")) == number), None)
    if existing is None:
        issues.append(record)
        changes.append(f"+ {series_slug}: added {edition['title']}")
    elif existing != record:
        existing.update(record)
        changes.append(f"~ {series_slug}: updated {edition['title']}")

    # The trade belongs on whichever volume holds its first issue, so that the
    # download list for that volume offers the book instead of the floppies.
    first_key = f"{series_slug}#{first}"
    host = next(
        (
            path
            for path in payload["reading_paths"]
            if any(entry.get("canonical_issue_key") == first_key for entry in path.get("entries", []))
        ),
        None,
    )
    if host is None:
        return changes + [f"! {series_slug}: no reading path holds #{first}"]

    entries = host.setdefault("entries", [])
    # A volume can host more than one trade when the split does not line up, so
    # only an entry covering the same issues is the one being corrected.
    for entry in list(entries):
        if entry.get("entry_type") != "collection" or entry.get("canonical_issue_key") == key:
            continue
        span = issue_range(str(entry.get("canonical_issue_key", "")).rpartition("#")[2])
        if span is None or span[0] > last or span[1] < first:
            continue
        entries.remove(entry)
        changes.append(f"- {host['slug']}: dropped stale collection entry {entry['canonical_issue_key']}")
    entry = {
        # Trades sort after the issues, and among themselves by what they collect,
        # so a volume hosting two trades still reads in publication order.
        "sort_order": COLLECTION_ENTRY_SORT_ORDER + first,
        "canonical_issue_key": key,
        "story_arc_slug": host["slug"],
        "entry_type": "collection",
        "importance": "collected-edition",
        "note": record["summary"],
    }
    existing_entry = next((e for e in entries if e.get("canonical_issue_key") == key), None)
    if existing_entry is None:
        entries.append(entry)
        changes.append(f"+ {host['slug']}: collection entry {number}")
    elif existing_entry != entry:
        existing_entry.update(entry)
        changes.append(f"~ {host['slug']}: collection entry {number}")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(CURATION_PATH.read_text())
    editions = json.loads(EDITIONS_PATH.read_text())["editions"]

    changes: list[str] = []
    for edition in editions:
        changes.extend(apply_edition(payload, edition))

    for line in changes:
        print(line)
    if not changes:
        print("Nothing to change.")
    if args.dry_run:
        return 0

    CURATION_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nWrote {CURATION_PATH.relative_to(REPO_ROOT)}")
    return 1 if any(line.startswith("!") for line in changes) else 0


if __name__ == "__main__":
    sys.exit(main())
