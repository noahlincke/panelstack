#!/usr/bin/env python3
"""Extend the curated catalogue so it genuinely covers 2019 onwards.

The catalogue claimed a 2019-2026 window while holding almost nothing before
2023. This adds the runs from the recommendation brief, generated from a compact
spec rather than hand-written JSON: monthly cadence from a start month, split
into collected volumes.

Issue counts and street dates are curated approximations at volume granularity.
The GCD import is the authority for exact per-issue dates and will correct these
in place, matching on series and issue number.

    python3 scripts/expand_curation.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

CURATION_PATH = Path(__file__).resolve().parent.parent / "backend" / "data" / "curation" / "reading_paths.json"


@dataclass(frozen=True)
class Run:
    slug: str
    publisher: str
    title: str
    start: str  # YYYY-MM
    issues: int
    description: str
    vol_size: int = 6
    first_issue: int = 1
    collection_word: str = "Vol."
    aliases: list[str] = field(default_factory=list)


DC_RUNS = [
    Run("far-sector-2019", "dc", "Far Sector", "2019-11", 12,
        "N.K. Jemisin's self-contained Green Lantern murder mystery with Jo Mullein.", vol_size=12),
    Run("dceased-2019", "dc", "DCeased", "2019-05", 6,
        "Tom Taylor's anti-life plague apocalypse.", vol_size=6),
    Run("jimmy-olsen-2019", "dc", "Superman's Pal Jimmy Olsen", "2019-07", 12,
        "Matt Fraction and Steve Lieber's twelve-issue comedy.", vol_size=12),
    Run("strange-adventures-2020", "dc", "Strange Adventures", "2020-03", 12,
        "Tom King and Mitch Gerads on Adam Strange as an unreliable war hero.", vol_size=12),
    Run("dark-nights-death-metal-2020", "dc", "Dark Nights: Death Metal", "2020-06", 7,
        "The event that closes out the Snyder cosmic-DC period.", vol_size=7),
    Run("nightwing-2021", "dc", "Nightwing", "2021-04", 30,
        "Tom Taylor's run, beginning at Nightwing #78.", first_issue=78),
    Run("supergirl-woman-of-tomorrow-2021", "dc", "Supergirl: Woman of Tomorrow", "2021-06", 8,
        "Tom King and Bilquis Evely's cosmic western.", vol_size=8),
    Run("human-target-2021", "dc", "The Human Target", "2021-12", 12,
        "Tom King and Greg Smallwood's noir with the Justice League International cast.", vol_size=12),
    Run("batman-the-knight-2022", "dc", "Batman: The Knight", "2022-02", 10,
        "Chip Zdarsky on young Bruce learning the job.", vol_size=10),
    Run("worlds-finest-2022", "dc", "Batman/Superman: World's Finest", "2022-04", 36,
        "Mark Waid and Dan Mora's deliberately timeless DC adventure book."),
    Run("dark-crisis-2022", "dc", "Dark Crisis on Infinite Earths", "2022-06", 7,
        "The era-ending crossover. Optional continuity candy.", vol_size=7),
    Run("titans-2023", "dc", "Titans", "2023-06", 18,
        "The Titans step up as the DC Universe's front line."),
    Run("shazam-2023", "dc", "Shazam!", "2023-06", 12,
        "Mark Waid's Dawn of DC Shazam."),
    Run("absolute-power-2024", "dc", "Absolute Power", "2024-07", 4,
        "The event that closes Dawn of DC and opens All In.", vol_size=4),
    Run("absolute-green-arrow-2026", "dc", "Absolute Green Arrow", "2026-01", 6,
        "The Absolute line's take on Oliver Queen.", vol_size=6),
    Run("absolute-catwoman-2026", "dc", "Absolute Catwoman", "2026-02", 6,
        "The Absolute line's take on Selina Kyle.", vol_size=6),
]

MARVEL_RUNS = [
    Run("house-of-x-powers-of-x-2019", "marvel", "House of X / Powers of X", "2019-07", 12,
        "Hickman's twin miniseries, read in Marvel's alternating order.", vol_size=12),
    Run("x-men-2019", "marvel", "X-Men", "2019-10", 21,
        "Hickman's flagship Krakoa-era X-Men."),
    Run("marauders-2019", "marvel", "Marauders", "2019-10", 27,
        "Kate Pryde and the Hellfire Trading Company."),
    Run("excalibur-2019", "marvel", "Excalibur", "2019-10", 26,
        "Mutant magic and Otherworld."),
    Run("x-force-2019", "marvel", "X-Force", "2019-11", 50,
        "Krakoa's intelligence agency."),
    Run("new-mutants-2019", "marvel", "New Mutants", "2019-11", 33,
        "The younger cast, on Krakoa and in space."),
    Run("hellions-2020", "marvel", "Hellions", "2020-03", 18,
        "Zeb Wells on Krakoa's least manageable mutants.", vol_size=6),
    Run("x-factor-2020", "marvel", "X-Factor", "2020-08", 10,
        "Mutant investigations into Krakoan resurrection.", vol_size=5),
    Run("x-of-swords-2020", "marvel", "X of Swords", "2020-09", 22,
        "The first major Krakoa crossover.", vol_size=22),
    Run("devils-reign-2021", "marvel", "Devil's Reign", "2021-12", 6,
        "Mayor Fisk versus the heroes of New York.", vol_size=6),
    Run("moon-knight-2021", "marvel", "Moon Knight", "2021-07", 30,
        "Jed MacKay's stylish, supernatural Moon Knight."),
    Run("immortal-x-men-2022", "marvel", "Immortal X-Men", "2022-03", 18,
        "Kieron Gillen on the politics of Krakoa's Quiet Council."),
    Run("x-men-red-2022", "marvel", "X-Men Red", "2022-05", 18,
        "Al Ewing on Arakko and the war for Mars."),
    Run("axe-judgment-day-2022", "marvel", "A.X.E.: Judgment Day", "2022-07", 6,
        "Avengers, X-Men and Eternals, written by Gillen.", vol_size=6),
    Run("daredevil-2019", "marvel", "Daredevil", "2019-02", 36,
        "Chip Zdarsky's crime drama, beginning with Know Fear."),
    Run("daredevil-2022", "marvel", "Daredevil", "2022-07", 14,
        "Zdarsky's second Daredevil series.", vol_size=7),
    Run("immortal-hulk-2018", "marvel", "Immortal Hulk", "2018-06", 50,
        "Al Ewing's complete fifty-issue body-horror Hulk."),
    Run("doctor-strange-2023", "marvel", "Doctor Strange", "2023-01", 15,
        "Jed MacKay's clean modern Strange book.", vol_size=5),
    Run("scarlet-witch-2023", "marvel", "Scarlet Witch", "2023-01", 10,
        "Steve Orlando's Wanda Maximoff.", vol_size=5),
    Run("ultimate-invasion-2023", "marvel", "Ultimate Invasion", "2023-06", 4,
        "Hickman's launch of the new Ultimate Universe.", vol_size=4),
    Run("ultimate-x-men-2024", "marvel", "Ultimate X-Men", "2024-03", 18,
        "Peach Momoko's corner of the new Ultimate Universe."),
    Run("fall-of-the-house-of-x-2024", "marvel", "Fall of the House of X", "2024-01", 5,
        "Half of the Krakoa era's conclusion.", vol_size=5),
    Run("rise-of-the-powers-of-x-2024", "marvel", "Rise of the Powers of X", "2024-01", 5,
        "The other half of the Krakoa era's conclusion.", vol_size=5),
    Run("ultimate-endgame-2026", "marvel", "Ultimate Endgame", "2026-01", 6,
        "The Ultimate Universe's closing event.", vol_size=6),
]

# Pre-2019 books the brief calls "just read the damn book". They sit outside the
# 2019 window on purpose and surface under All years.
CLASSIC_RUNS = [
    Run("batman-year-one-1987", "dc", "Batman: Year One", "1987-02", 4, "Miller and Mazzucchelli's origin.", vol_size=4),
    Run("batman-the-long-halloween-1996", "dc", "Batman: The Long Halloween", "1996-12", 13, "The definitive Batman murder mystery.", vol_size=13),
    Run("kingdom-come-1996", "dc", "Kingdom Come", "1996-05", 4, "Waid and Ross on a future DC.", vol_size=4),
    Run("gotham-central-2003", "dc", "Gotham Central", "2003-02", 40, "GCPD police procedural in Batman's city."),
    Run("all-star-superman-2005", "dc", "All-Star Superman", "2005-11", 12, "Morrison and Quitely's standalone Superman.", vol_size=12),
    Run("green-lantern-rebirth-2004", "dc", "Green Lantern: Rebirth", "2004-10", 6, "The start of Geoff Johns' GL mythology.", vol_size=6),
    Run("batman-the-black-mirror-2010", "dc", "Batman: The Black Mirror", "2010-11", 11, "Snyder's horror-crime Batman with Dick Grayson.", vol_size=11),
    Run("batman-snyder-capullo-2011", "dc", "Batman", "2011-11", 52, "Snyder and Capullo, starting with Court of Owls."),
    Run("ultimate-spider-man-2000", "marvel", "Ultimate Spider-Man", "2000-10", 133, "Bendis and Bagley's original Ultimate run.", vol_size=12),
    Run("new-x-men-2001", "marvel", "New X-Men", "2001-05", 41, "Grant Morrison's radical X-Men."),
    Run("astonishing-x-men-2004", "marvel", "Astonishing X-Men", "2004-05", 24, "Whedon and Cassaday, starting with Gifted."),
    Run("daredevil-bendis-2001", "marvel", "Daredevil", "2001-08", 55, "Bendis and Maleev's crime run."),
    Run("uncanny-x-force-2010", "marvel", "Uncanny X-Force", "2010-10", 35, "Rick Remender's black-ops X-book."),
    Run("hawkeye-2012", "marvel", "Hawkeye", "2012-08", 22, "Fraction and Aja's small-scale Hawkeye."),
    Run("vision-2015", "marvel", "The Vision", "2015-11", 12, "Tom King's suburban tragedy.", vol_size=12),
]

ALL_RUNS = DC_RUNS + MARVEL_RUNS + CLASSIC_RUNS


def month_sequence(start: str, count: int) -> list[date]:
    year, month = (int(part) for part in start.split("-"))
    months = []
    for _ in range(count):
        months.append(date(year, month, 1))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return months


def build(run: Run) -> tuple[dict, list[dict], list[dict]]:
    months = month_sequence(run.start, run.issues)
    numbers = [run.first_issue + offset for offset in range(run.issues)]

    series = {
        "slug": run.slug,
        "publisher_slug": run.publisher,
        "title": run.title,
        "volume": 1,
        "start_year": months[0].year,
        "end_year": months[-1].year,
        "description": run.description,
        "issues": [
            {
                "issue_number": str(number),
                "title": f"{run.title} #{number}",
                "published_on": month.isoformat(),
            }
            for number, month in zip(numbers, months)
        ],
    }
    if run.aliases:
        series["aliases"] = run.aliases

    arcs: list[dict] = []
    paths: list[dict] = []
    for index in range(0, run.issues, run.vol_size):
        volume = index // run.vol_size + 1
        chunk = list(zip(numbers[index : index + run.vol_size], months[index : index + run.vol_size]))
        if not chunk:
            continue
        arc_slug = f"{run.slug}-vol-{volume}"
        single_volume = run.vol_size >= run.issues
        title = run.title if single_volume else f"{run.title}: {run.collection_word} {volume}"
        arcs.append(
            {
                "slug": arc_slug,
                "title": run.title if single_volume else f"Volume {volume}",
                "phase": "series",
                "status": "published",
                "description": run.description,
            }
        )
        paths.append(
            {
                "slug": arc_slug,
                "title": title,
                "description": run.description,
                "status": "published",
                "entries": [
                    {
                        "sort_order": (position + 1) * 10,
                        "canonical_issue_key": f"{run.slug}#{number}",
                        "story_arc_slug": arc_slug,
                        "entry_type": "issue",
                        "importance": "main",
                    }
                    for position, (number, _) in enumerate(chunk)
                ],
            }
        )
    return series, arcs, paths


def main() -> None:
    payload = json.loads(CURATION_PATH.read_text(encoding="utf-8"))
    existing_series = {series["slug"] for series in payload["series"]}
    existing_arcs = {arc["slug"] for arc in payload["story_arcs"]}
    existing_paths = {path["slug"] for path in payload["reading_paths"]}

    added_series = added_paths = 0
    for run in ALL_RUNS:
        if run.slug in existing_series:
            continue
        series, arcs, paths = build(run)
        payload["series"].append(series)
        added_series += 1
        for arc in arcs:
            if arc["slug"] not in existing_arcs:
                payload["story_arcs"].append(arc)
                existing_arcs.add(arc["slug"])
        for path in paths:
            if path["slug"] not in existing_paths:
                payload["reading_paths"].append(path)
                existing_paths.add(path["slug"])
                added_paths += 1

    CURATION_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"added {added_series} series and {added_paths} collections")
    print(f"totals: {len(payload['series'])} series, {len(payload['reading_paths'])} collections")


if __name__ == "__main__":
    main()
