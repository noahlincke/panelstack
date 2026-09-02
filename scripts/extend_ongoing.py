#!/usr/bin/env python3
"""Continue ongoing runs up to the present month.

The catalogue was generated with fixed issue counts, so most runs stopped in 2025
or early 2026 even though the books kept shipping. This walks every series whose
last issue lands in the recent past, appends monthly issues through the target
month, and adds the volumes to hold them.

Finite series — minis, events, and lines that have concluded — are left alone.

    python3 scripts/extend_ongoing.py --through 2026-09
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

CURATION_PATH = Path(__file__).resolve().parent.parent / "backend" / "data" / "curation" / "reading_paths.json"

# Runs that ended on purpose. Extending these would invent issues that do not exist.
FINITE_SLUGS = {
    "house-of-x-powers-of-x-2019",
    "far-sector-2019",
    "dceased-2019",
    "jimmy-olsen-2019",
    "strange-adventures-2020",
    "dark-nights-death-metal-2020",
    "supergirl-woman-of-tomorrow-2021",
    "human-target-2021",
    "batman-the-knight-2022",
    "dark-crisis-2022",
    "absolute-power-2024",
    "x-of-swords-2020",
    "axe-judgment-day-2022",
    "devils-reign-2021",
    "fall-of-the-house-of-x-2024",
    "rise-of-the-powers-of-x-2024",
    "immortal-hulk-2018",
    "x-men-2019",
    "marauders-2019",
    "excalibur-2019",
    "x-force-2019",
    "new-mutants-2019",
    "hellions-2020",
    "x-factor-2020",
    "immortal-x-men-2022",
    "x-men-red-2022",
    "daredevil-2019",
    "daredevil-2022",
    "moon-knight-2021",
    "scarlet-witch-2023",
    "ultimate-invasion-2023",
    "ultimate-endgame-2026",
    "worlds-finest-2022",
    "nightwing-2021",
    "titans-2023",
    "shazam-2023",
}

# A run is a candidate only if it was still shipping recently.
STILL_RUNNING_AFTER = date(2025, 6, 1)
VOLUME_SIZE = 6


def month_after(day: date) -> date:
    return date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", default="2026-09", metavar="YYYY-MM")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    year, month = (int(part) for part in args.through.split("-"))
    target = date(year, month, 1)

    payload = json.loads(CURATION_PATH.read_text(encoding="utf-8"))
    arcs_by_slug = {arc["slug"] for arc in payload["story_arcs"]}
    paths_by_slug = {path["slug"] for path in payload["reading_paths"]}

    extended = added_issues = added_volumes = 0
    for series in payload["series"]:
        slug = series["slug"]
        if slug in FINITE_SLUGS or slug.startswith(("ultimate-", "gcd-")):
            continue

        numbered = [i for i in series["issues"] if i["issue_number"].isdigit() and i.get("published_on")]
        if not numbered:
            continue
        last = max(numbered, key=lambda i: i["published_on"])
        last_date = date.fromisoformat(last["published_on"])
        if not (STILL_RUNNING_AFTER <= last_date < target):
            continue

        number = int(last["issue_number"])
        cursor = month_after(last_date)
        new_issues = []
        while cursor <= target:
            number += 1
            new_issues.append(
                {
                    "issue_number": str(number),
                    "title": f"{series['title']} #{number}",
                    "published_on": cursor.isoformat(),
                }
            )
            cursor = month_after(cursor)
        if not new_issues:
            continue

        extended += 1
        added_issues += len(new_issues)
        if args.dry_run:
            print(f"  {slug}: +{len(new_issues)} issues through {target}")
            continue

        series["issues"].extend(new_issues)
        series["end_year"] = target.year

        # Existing volumes for this series tell us where numbering continues.
        existing_volumes = [p for p in payload["reading_paths"] if p["slug"].startswith(f"{slug}-vol-")]
        next_volume = len(existing_volumes) + 1
        for index in range(0, len(new_issues), VOLUME_SIZE):
            chunk = new_issues[index : index + VOLUME_SIZE]
            arc_slug = f"{slug}-vol-{next_volume}"
            if arc_slug not in arcs_by_slug:
                payload["story_arcs"].append(
                    {
                        "slug": arc_slug,
                        "title": f"Volume {next_volume}",
                        "phase": "series",
                        "status": "published",
                        "description": series.get("description", ""),
                    }
                )
                arcs_by_slug.add(arc_slug)
            if arc_slug not in paths_by_slug:
                payload["reading_paths"].append(
                    {
                        "slug": arc_slug,
                        "title": f"{series['title']}: Vol. {next_volume}",
                        "description": series.get("description", ""),
                        "status": "published",
                        "entries": [
                            {
                                "sort_order": (position + 1) * 10,
                                "canonical_issue_key": f"{slug}#{issue['issue_number']}",
                                "story_arc_slug": arc_slug,
                                "entry_type": "issue",
                                "importance": "main",
                            }
                            for position, issue in enumerate(chunk)
                        ],
                    }
                )
                paths_by_slug.add(arc_slug)
                added_volumes += 1
            next_volume += 1

    if not args.dry_run:
        CURATION_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\nextended {extended} series, +{added_issues} issues, +{added_volumes} volumes through {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
