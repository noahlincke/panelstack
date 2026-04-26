from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    CanonicalIssue,
    CanonicalSeries,
    CanonicalSeriesAlias,
    CatalogCollection,
    CatalogCollectionAlias,
    CatalogCollectionItem,
    CatalogCollectionTag,
    ContinuityGroup,
    CurationSource,
    Event,
    IssueMatch,
    Publisher,
    ReadingPath,
    ReadingPathEntry,
)
from .library import slugify


FAMILY_TAG_RULES: dict[str, tuple[str, ...]] = {
    "street-level-family": (
        "daredevil",
        "hell s kitchen",
        "hell's kitchen",
        "elektra",
        "punisher",
        "defenders",
        "street level",
        "echo",
    ),
    "hunter-x-hunter-family": (
        "hunter x hunter",
        "hunterxhunter",
        "gon freecss",
        "killua",
        "kurapika",
        "leorio",
        "hisoka",
        "chimera ant",
        "phantom troupe",
        "greed island",
    ),
    "jojo-family": (
        "jojo",
        "jojo s bizarre adventure",
        "jojo no kimyou na bouken",
        "phantom blood",
        "battle tendency",
        "stardust crusaders",
        "diamond is unbreakable",
        "golden wind",
        "ougon no kaze",
        "stone ocean",
        "steel ball run",
        "jojolion",
        "jojolands",
    ),
    "bat-family": (
        "batman",
        "detective comics",
        "nightwing",
        "robin",
        "batgirl",
        "batwoman",
        "red hood",
        "red robin",
        "catwoman",
        "gotham",
        "azrael",
        "harley quinn",
        "poison ivy",
        "birds of prey",
    ),
    "superman-family": (
        "superman",
        "action comics",
        "supergirl",
        "superboy",
        "power girl",
        "steel",
        "super sons",
        "krypto",
        "krypton",
    ),
    "wonder-woman-family": (
        "wonder woman",
        "amazon",
        "amazons",
        "nubia",
        "wonder girl",
        "yara flor",
        "trinity",
    ),
    "justice-league-family": (
        "justice league",
        "jla",
        "justice society",
        "titans",
        "teen titans",
        "green lantern",
        "flash",
        "aquaman",
        "green arrow",
        "suicide squad",
        "shazam",
        "hawkman",
        "hawkgirl",
    ),
    "lantern-family": (
        "green lantern",
        "green lantern corps",
        "lantern",
        "lanterns",
        "war journal",
        "hal jordan",
        "john stewart",
        "jo mullein",
        "guy gardner",
        "kyle rayner",
        "sinestro",
        "oa",
    ),
    "flash-family": (
        "flash",
        "speed force",
        "wally west",
        "barry allen",
        "jay garrick",
        "reverse flash",
        "rogues",
        "impulse",
        "kid flash",
        "max mercury",
    ),
    "spider-family": (
        "spider man",
        "spider-man",
        "spider verse",
        "spider-verse",
        "spiderverse",
        "ghost spider",
        "spider woman",
        "silk",
        "scarlet spider",
        "miles morales",
        "peter parker",
        "venom",
        "carnage",
    ),
    "avengers-family": (
        "avengers",
        "new avengers",
        "west coast avengers",
        "young avengers",
        "illuminati",
        "ultimates",
        "iron man",
        "captain america",
        "captain marvel",
        "thor",
        "black panther",
        "doctor strange",
        "hulk",
        "she hulk",
    ),
    "x-men-family": (
        "x men",
        "x-men",
        "uncanny",
        "nyx",
        "phoenix",
        "new mutants",
        "x factor",
        "x-force",
        "x force",
        "weapon x",
        "excalibur",
        "academy x",
        "kamala khan",
        "ms marvel",
        "hellion",
        "prodigy",
        "anole",
        "sophie cuckoo",
        "mutant",
        "cable",
        "storm",
        "rogue",
        "gambit",
        "nightcrawler",
        "magneto",
        "cyclops",
        "jean grey",
    ),
    "fantastic-four-family": (
        "fantastic four",
        "future foundation",
        "mr fantastic",
        "reed richards",
        "invisible woman",
        "sue storm",
        "human torch",
        "johnny storm",
        "thing",
        "ben grimm",
    ),
    "wolverine-family": (
        "wolverine",
        "logan",
        "x-23",
        "x 23",
        "laura kinney",
        "all-new wolverine",
        "daken",
        "sabretooth",
    ),
}


@dataclass
class CatalogSyncResult:
    sources_synced: int = 0
    continuity_groups_synced: int = 0
    collections_synced: int = 0
    items_synced: int = 0
    aliases_synced: int = 0
    tags_synced: int = 0


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _collection_dates(reading_path: ReadingPath) -> tuple[date | None, date | None]:
    dates = [
        entry.canonical_issue.published_on
        or (entry.issue.published_on if entry.issue is not None else None)
        for entry in reading_path.entries
        if entry.entry_type == "issue" and (entry.canonical_issue is not None or entry.issue is not None)
    ]
    valid = [value for value in dates if value is not None]
    if not valid:
        return None, None
    return min(valid), max(valid)


def _infer_line(reading_path: ReadingPath) -> str:
    if reading_path.event_id is not None:
        return "event"
    slug = reading_path.slug.lower()
    title = reading_path.title.lower()
    if "absolute" in slug or "absolute" in title:
        return "absolute"
    if "ultimate" in slug or "ultimate" in title:
        return "ultimate"
    return "series"


def _infer_collection_type(reading_path: ReadingPath) -> str:
    return "event" if reading_path.event_id is not None else "run"


def _primary_canonical_series(reading_path: ReadingPath) -> CanonicalSeries | None:
    for entry in reading_path.entries:
        if entry.canonical_series is not None:
            return entry.canonical_series
        if entry.canonical_issue is not None:
            return entry.canonical_issue.series
    return None


def _primary_publisher(reading_path: ReadingPath, series: CanonicalSeries | None) -> Publisher | None:
    if series is not None and series.publisher is not None:
        return series.publisher
    if reading_path.event is not None and reading_path.event.publisher is not None:
        return reading_path.event.publisher
    return None


def _infer_volume_number(reading_path: ReadingPath, series: CanonicalSeries | None) -> int | None:
    match = re.search(r"\bvol(?:ume)?\.?\s*(\d+)\b", reading_path.title, flags=re.I)
    if match:
        return int(match.group(1))
    if "first year" in reading_path.title.lower():
        return 1
    if series is not None and series.volume is not None:
        return series.volume
    return None


def _infer_sequence_number(reading_path: ReadingPath, series: CanonicalSeries | None, first_published_on: date | None) -> int:
    volume_number = _infer_volume_number(reading_path, series)
    if volume_number is not None:
        return volume_number
    if series is not None and series.start_year is not None:
        return series.start_year
    if first_published_on is not None:
        return first_published_on.year
    return 0


def _continuity_identity(reading_path: ReadingPath, series: CanonicalSeries | None, publisher: Publisher | None) -> tuple[str, str]:
    if series is not None:
        base_title = series.title
    else:
        base_title = re.sub(r":\s*vol(?:ume)?\.?\s*\d+.*$", "", reading_path.title, flags=re.I).strip()
        base_title = re.sub(r":\s*first year$", "", base_title, flags=re.I).strip()
    publisher_slug = publisher.slug if publisher is not None else "catalog"
    slug = slugify(f"{publisher_slug}-{base_title}") or slugify(reading_path.slug)
    return slug, base_title


def _upsert_source(
    db: Session,
    *,
    name: str | None,
    source_url: str | None,
    source_type: str = "editorial",
) -> tuple[CurationSource | None, bool]:
    if not name and not source_url:
        return None, False
    slug = slugify(source_url or name or source_type)
    source = db.scalar(select(CurationSource).where(CurationSource.slug == slug))
    created = False
    if source is None:
        source = CurationSource(slug=slug, name=name or source_url or "Unknown source", source_type=source_type)
        db.add(source)
        created = True
    source.name = name or source.name
    source.source_type = source_type
    source.source_url = source_url or source.source_url
    return source, created


def _collection_alias_values(reading_path: ReadingPath, series: CanonicalSeries | None) -> set[str]:
    aliases = {reading_path.title}
    cleaned_title = re.sub(r":\s*vol(?:ume)?\.?\s*\d+.*$", "", reading_path.title, flags=re.I).strip()
    if cleaned_title and cleaned_title != reading_path.title:
        aliases.add(cleaned_title)
    if series is not None:
        aliases.add(series.title)
    return {alias for alias in aliases if alias}


def _series_alias_values(series: CanonicalSeries) -> set[str]:
    aliases = {series.title}
    without_article = re.sub(r"^the\s+", "", series.title, flags=re.I).strip()
    if without_article and without_article != series.title:
        aliases.add(without_article)
    return {alias for alias in aliases if alias}


def _collection_tags(reading_path: ReadingPath, publisher: Publisher | None, line: str, series: CanonicalSeries | None) -> set[str]:
    tags = {line}
    if publisher is not None:
        tags.add(publisher.slug)
    if series is not None:
        tags.add(slugify(series.title))
    haystack = _normalize_text(f"{reading_path.title} {reading_path.slug} {series.title if series is not None else ''}")
    for tag, terms in FAMILY_TAG_RULES.items():
        if any(_normalize_text(term) in haystack for term in terms):
            tags.add(tag)
    return {tag for tag in tags if tag}


def _primary_local_issue_id(canonical_issue_id: int | None, db: Session) -> int | None:
    if canonical_issue_id is None:
        return None
    match = db.scalar(
        select(IssueMatch.local_issue_id)
        .where(IssueMatch.canonical_issue_id == canonical_issue_id, IssueMatch.is_primary.is_(True))
        .order_by(IssueMatch.confidence_score.desc(), IssueMatch.id.asc())
        .limit(1)
    )
    return match


def sync_catalog_data(db: Session) -> CatalogSyncResult:
    reading_paths = db.scalars(
        select(ReadingPath).options(
            selectinload(ReadingPath.event).selectinload(Event.publisher),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.canonical_issue).selectinload(CanonicalIssue.series).selectinload(CanonicalSeries.publisher),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.canonical_series).selectinload(CanonicalSeries.publisher),
            selectinload(ReadingPath.entries).selectinload(ReadingPathEntry.issue),
        )
    ).all()

    result = CatalogSyncResult()

    for reading_path in reading_paths:
        series = _primary_canonical_series(reading_path)
        publisher = _primary_publisher(reading_path, series)
        first_published_on, latest_published_on = _collection_dates(reading_path)
        source, source_created = _upsert_source(
            db,
            name=reading_path.source_name or (reading_path.event.source_name if reading_path.event is not None else None),
            source_url=reading_path.source_url or (reading_path.event.source_url if reading_path.event is not None else None),
        )
        if source_created:
            result.sources_synced += 1

        continuity_slug, continuity_title = _continuity_identity(reading_path, series, publisher)
        continuity_group = db.scalar(select(ContinuityGroup).where(ContinuityGroup.slug == continuity_slug))
        continuity_created = False
        if continuity_group is None:
            continuity_group = ContinuityGroup(slug=continuity_slug, title=continuity_title)
            db.add(continuity_group)
            continuity_created = True
        continuity_group.title = continuity_title
        continuity_group.publisher_id = publisher.id if publisher is not None else None
        if continuity_created:
            result.continuity_groups_synced += 1

        collection = db.get(CatalogCollection, reading_path.id)
        if collection is None:
            collection = db.scalar(select(CatalogCollection).where(CatalogCollection.reading_path_id == reading_path.id))
        created = False
        if collection is None:
            collection = CatalogCollection(id=reading_path.id)
            db.add(collection)
            created = True

        line = _infer_line(reading_path)
        collection.reading_path_id = reading_path.id
        collection.canonical_series_id = series.id if series is not None else None
        collection.continuity_group = continuity_group
        collection.publisher_id = publisher.id if publisher is not None else None
        collection.source = source
        collection.slug = reading_path.slug
        collection.title = reading_path.title
        collection.sort_title = _normalize_text(reading_path.title)
        collection.description = reading_path.description
        collection.status = reading_path.status
        collection.line = line
        collection.collection_type = _infer_collection_type(reading_path)
        collection.volume_number = _infer_volume_number(reading_path, series)
        collection.sequence_number = _infer_sequence_number(reading_path, series, first_published_on)
        collection.start_year = series.start_year if series is not None else (first_published_on.year if first_published_on is not None else None)
        collection.end_year = series.end_year if series is not None else (latest_published_on.year if latest_published_on is not None else None)
        collection.first_published_on = first_published_on
        collection.latest_published_on = latest_published_on
        if created:
            result.collections_synced += 1

        for existing in list(collection.items):
            db.delete(existing)
        for existing in list(collection.aliases):
            db.delete(existing)
        for existing in list(collection.tags):
            db.delete(existing)
        db.flush()

        for entry in reading_path.entries:
            local_issue_id = entry.issue_id or _primary_local_issue_id(entry.canonical_issue_id, db)
            item = CatalogCollectionItem(
                id=entry.id,
                collection_id=collection.id,
                reading_path_entry_id=entry.id,
                issue_id=local_issue_id,
                canonical_issue_id=entry.canonical_issue_id,
                source_id=source.id if source is not None else None,
                sort_order=entry.sort_order,
                item_type=entry.entry_type,
                importance=entry.importance,
                label=entry.label,
                note=entry.note,
                is_optional=entry.is_optional,
            )
            db.add(item)
            result.items_synced += 1

        for alias in sorted(_collection_alias_values(reading_path, series)):
            db.add(CatalogCollectionAlias(collection_id=collection.id, alias=alias))
            result.aliases_synced += 1

        for tag in sorted(_collection_tags(reading_path, publisher, line, series)):
            db.add(CatalogCollectionTag(collection_id=collection.id, tag=tag))
            result.tags_synced += 1

        if series is not None:
            existing_aliases = {
                alias
                for alias in db.scalars(
                    select(CanonicalSeriesAlias.alias).where(CanonicalSeriesAlias.canonical_series_id == series.id)
                )
            }
            for alias in sorted(_series_alias_values(series)):
                if alias in existing_aliases:
                    continue
                db.add(CanonicalSeriesAlias(canonical_series_id=series.id, alias=alias))
                result.aliases_synced += 1

    db.commit()
    return result
