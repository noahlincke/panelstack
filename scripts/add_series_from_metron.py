#!/usr/bin/env python3
"""Add a series to the curation file from Metron's record of it.

The catalogue missed relaunches of runs it already follows: Batman restarted at
#1 in 2025 and the catalogue still only knew the 2016 volume, so the current run
— the one actually on the shelf — was nowhere.

Everything written here comes from Metron: real issue numbers, cover dates and
cover art, never a guessed count extrapolated to the present. That is the whole
point, so this refuses to invent anything the source does not have.

Issues are grouped into volumes of six, which is what the DC and Marvel entries
already in the file use.

    python3 scripts/add_series_from_metron.py --metron-id 12829 \
        --slug batman-2025 --publisher dc --description "..." [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.services.curation import CURATION_DATA_PATH  # noqa: E402
from backend.app.services.metron import (  # noqa: E402
    METRON_API_ROOT,
    build_fetcher,
    iter_series_issues,
)

ISSUES_PER_VOLUME = 6
ORDINALS = ["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]


def volume_label(index: int) -> str:
    return ORDINALS[index] if index < len(ORDINALS) else str(index + 1)


def build_entries(slug: str, title: str, issues: list[dict], source_url: str) -> tuple[dict, list, list]:
    """Turn Metron's issue list into a series, its volumes and their story arcs."""
    series_issues = [
        {
            "issue_number": issue["number"],
            "title": issue.get("issue") or f"{title} #{issue['number']}",
            "published_on": (issue.get("cover_date") or issue.get("store_date") or "")[:10] or None,
        }
        for issue in issues
    ]
    for entry in series_issues:
        if entry["published_on"] is None:
            del entry["published_on"]

    reading_paths = []
    story_arcs = []
    for index in range(0, len(issues), ISSUES_PER_VOLUME):
        chunk = issues[index : index + ISSUES_PER_VOLUME]
        number = index // ISSUES_PER_VOLUME
        path_slug = f"{slug}-vol-{number + 1}"
        first, last = chunk[0]["number"], chunk[-1]["number"]
        blurb = (
            f"Issues #{first}-{last} of the run."
            if first != last
            else f"Issue #{first} of the run."
        )
        story_arcs.append(
            {
                "slug": path_slug,
                "title": f"Volume {volume_label(number)}",
                "phase": "series",
                "status": "published",
                "description": blurb,
            }
        )
        reading_paths.append(
            {
                "slug": path_slug,
                "title": f"{title}: Vol. {number + 1}",
                "description": blurb,
                "status": "published",
                "source_name": "Metron",
                "source_url": source_url,
                "entries": [
                    {
                        "sort_order": (position + 1) * 10,
                        "canonical_issue_key": f"{slug}#{issue['number']}",
                        "story_arc_slug": path_slug,
                        "entry_type": "issue",
                        "importance": "main",
                    }
                    for position, issue in enumerate(chunk)
                ],
            }
        )
    return series_issues, reading_paths, story_arcs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metron-id", type=int, required=True)
    parser.add_argument("--slug", required=True, help="Our series slug, e.g. batman-2025.")
    parser.add_argument("--title", help="Defaults to the title Metron uses.")
    parser.add_argument("--publisher", required=True, choices=["dc", "marvel"])
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    fetch = build_fetcher()
    issues = sorted(
        iter_series_issues(args.metron_id, fetch),
        key=lambda row: int(row["number"]) if str(row["number"]).isdigit() else 0,
    )
    if not issues:
        print(f"error: Metron series {args.metron_id} has no issues.", file=sys.stderr)
        return 1

    title = args.title or (issues[0].get("series") or {}).get("name") or args.slug
    source_url = f"https://metron.cloud/series/{args.metron_id}/"
    series_issues, reading_paths, story_arcs = build_entries(args.slug, title, issues, source_url)

    payload = json.loads(CURATION_DATA_PATH.read_text(encoding="utf-8"))
    if any(s["slug"] == args.slug for s in payload["series"]):
        print(f"error: {args.slug} is already in the curation file.", file=sys.stderr)
        return 1

    print(f"{title} ({args.start_year}): {len(series_issues)} issues -> {len(reading_paths)} volumes")
    for path in reading_paths:
        print(f"  {path['title']}: {len(path['entries'])} issues")
    if args.dry_run:
        return 0

    payload["series"].append(
        {
            "slug": args.slug,
            "publisher_slug": args.publisher,
            "title": title,
            "volume": 1,
            "start_year": args.start_year,
            "description": args.description,
            "issues": series_issues,
        }
    )
    payload["story_arcs"].extend(story_arcs)
    payload["reading_paths"].extend(reading_paths)
    CURATION_DATA_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Added to {CURATION_DATA_PATH.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
