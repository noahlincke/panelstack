from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    CanonicalIssue,
    CanonicalSeries,
    CatalogCollection,
    CatalogCollectionItem,
    CatalogCollectionTag,
    Publisher,
)
from .library import slugify

# Character and team tags are written by the catalog sync as "<slug>-family".
FAMILY_SUFFIX = "-family"
# The suffix also matches manga titles such as "Spy x Family", so family facets
# are scoped to the publishers that actually use family groupings.
FAMILY_PUBLISHER_SLUGS = ("dc", "marvel")
# Every collection also carries its publisher slug and its line as tags. Neither
# says anything about who is in the book, so they never become a facet.
NON_CHARACTER_TAGS = {"series", "event", "absolute", "ultimate", "run", "arc"}
# A manga line has no family groupings; its series slug is the useful grouping,
# but only once more than one volume shares it.
MIN_SERIES_TAG_COLLECTIONS = 2


@dataclass(frozen=True)
class Facet:
    value: str
    label: str
    count: int


@dataclass(frozen=True)
class CatalogFacets:
    publishers: list[Facet]
    lines: list[Facet]
    characters: list[Facet]
    min_year: int | None
    max_year: int | None


@dataclass(frozen=True)
class ChronologyRow:
    canonical_issue: CanonicalIssue
    collection: CatalogCollection
    published_on: date


def _titleize(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-"))


def _character_label(tag: str) -> str:
    return _titleize(tag).replace("X Men", "X-Men").replace("Jojo", "JoJo")


def _series_year(column) -> Select:  # noqa: ANN001 - a mapped column of CanonicalSeries
    return (
        select(column)
        .where(CanonicalSeries.id == CatalogCollection.canonical_series_id)
        .correlate(CatalogCollection)
        .scalar_subquery()
    )


def _series_ends_on_or_after(year: int):  # noqa: ANN201 - a SQLAlchemy boolean clause
    """Undated runs fall back to the series' own years.

    MangaPill publishes no chapter dates, so every manga collection has null
    publication dates. Dropping them from a date window made the chronology look
    empty for every publisher but DC and Marvel; an open-ended series is treated
    as still running.
    """
    end = _series_year(CanonicalSeries.end_year)
    return or_(end.is_(None), end >= year)


def _series_starts_on_or_before(year: int):  # noqa: ANN201 - a SQLAlchemy boolean clause
    start = _series_year(CanonicalSeries.start_year)
    return or_(start.is_(None), start <= year)


def _apply_filters(
    stmt: Select,
    *,
    publisher: Sequence[str] | None,
    line: str | None,
    character: str | None,
    start: date | None,
    end: date | None,
    search: str | None,
    owned: bool | None = None,
) -> Select:
    if publisher:
        stmt = stmt.join(Publisher, Publisher.id == CatalogCollection.publisher_id).where(Publisher.slug.in_(publisher))
    if line:
        stmt = stmt.where(CatalogCollection.line == line)
    if character:
        stmt = stmt.where(
            CatalogCollection.id.in_(
                select(CatalogCollectionTag.collection_id).where(CatalogCollectionTag.tag == character)
            )
        )
    if start:
        stmt = stmt.where(
            or_(
                and_(
                    CatalogCollection.latest_published_on.is_not(None),
                    CatalogCollection.latest_published_on >= start,
                ),
                and_(CatalogCollection.latest_published_on.is_(None), _series_ends_on_or_after(start.year)),
            )
        )
    if end:
        stmt = stmt.where(
            or_(
                and_(
                    CatalogCollection.first_published_on.is_not(None),
                    CatalogCollection.first_published_on <= end,
                ),
                and_(CatalogCollection.first_published_on.is_(None), _series_starts_on_or_before(end.year)),
            )
        )
    if owned is not None:
        owns = CatalogCollection.id.in_(
            select(CatalogCollectionItem.collection_id).where(CatalogCollectionItem.issue_id.is_not(None))
        )
        stmt = stmt.where(owns if owned else ~owns)
    if search:
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(CatalogCollection.title).like(needle),
                func.lower(CatalogCollection.sort_title).like(needle),
            )
        )
    return stmt


def _character_facet_rows(db: Session) -> list[tuple[str, int, str]]:
    """The groupings worth putting a filter chip or a chronology lane on.

    DC and Marvel are curated into "<who>-family" tags, which are exactly right.
    Manga lines have no such curation, so the series slug shared by a title's
    volumes stands in for it — without that, every non-DC/Marvel publisher had
    nothing to filter or lane by and the chronology looked empty for all of them.
    """
    counts = select(
        CatalogCollectionTag.tag,
        func.count(CatalogCollectionTag.collection_id).label("collections"),
    ).join(CatalogCollection, CatalogCollection.id == CatalogCollectionTag.collection_id)

    family = db.execute(
        counts.join(Publisher, Publisher.id == CatalogCollection.publisher_id)
        .where(CatalogCollectionTag.tag.like(f"%{FAMILY_SUFFIX}"), Publisher.slug.in_(FAMILY_PUBLISHER_SLUGS))
        .group_by(CatalogCollectionTag.tag)
    ).all()

    publisher_slugs = set(db.scalars(select(Publisher.slug)))
    other = db.execute(
        counts.join(Publisher, Publisher.id == CatalogCollection.publisher_id)
        .where(Publisher.slug.not_in(FAMILY_PUBLISHER_SLUGS))
        .group_by(CatalogCollectionTag.tag)
        .having(func.count(CatalogCollectionTag.collection_id) >= MIN_SERIES_TAG_COLLECTIONS)
    ).all()

    # Slugs like "frieren-beyond-journey-s-end" do not titleize into anything a
    # human would recognise, so a series tag borrows its series' real title. The
    # tag is slugified from either the series slug (minus its trailing start
    # year) or the title, depending on which sync wrote it, so both are indexed.
    titles: dict[str, str] = {}
    for slug, title in db.execute(select(CanonicalSeries.slug, CanonicalSeries.title)).all():
        titles.setdefault(re.sub(r"-\d{4}$", "", slug), title)
        titles.setdefault(slugify(title), title)

    rows = [(tag, count, _character_label(tag)) for tag, count in family]
    rows += [
        (tag, count, titles.get(tag) or _character_label(tag))
        for tag, count in other
        if tag not in publisher_slugs and tag not in NON_CHARACTER_TAGS
    ]
    return sorted(rows, key=lambda row: (-row[1], row[2]))


def catalog_facets(db: Session) -> CatalogFacets:
    publisher_rows = db.execute(
        select(Publisher.slug, Publisher.name, func.count(CatalogCollection.id))
        .join(CatalogCollection, CatalogCollection.publisher_id == Publisher.id)
        .group_by(Publisher.slug, Publisher.name)
        .order_by(func.count(CatalogCollection.id).desc(), Publisher.name.asc())
    ).all()
    line_rows = db.execute(
        select(CatalogCollection.line, func.count(CatalogCollection.id))
        .group_by(CatalogCollection.line)
        .order_by(func.count(CatalogCollection.id).desc())
    ).all()
    character_rows = _character_facet_rows(db)
    span = db.execute(
        select(func.min(CatalogCollection.first_published_on), func.max(CatalogCollection.latest_published_on))
    ).one()
    # Undated runs would otherwise fall outside the range offered to the user.
    series_span = db.execute(
        select(func.min(CanonicalSeries.start_year), func.max(CanonicalSeries.end_year)).join(
            CatalogCollection, CatalogCollection.canonical_series_id == CanonicalSeries.id
        )
    ).one()
    min_year = min(year for year in (span[0].year if span[0] else None, series_span[0]) if year) if (
        span[0] or series_span[0]
    ) else None
    max_year = max(year for year in (span[1].year if span[1] else None, series_span[1]) if year) if (
        span[1] or series_span[1]
    ) else None
    return CatalogFacets(
        publishers=[Facet(value=slug, label=name, count=count) for slug, name, count in publisher_rows],
        lines=[Facet(value=line, label=_titleize(line), count=count) for line, count in line_rows],
        characters=[Facet(value=tag, label=label, count=count) for tag, count, label in character_rows],
        min_year=min_year,
        max_year=max_year,
    )


def catalog_collections(
    db: Session,
    *,
    publisher: Sequence[str] | None = None,
    line: str | None = None,
    character: str | None = None,
    start: date | None = None,
    end: date | None = None,
    search: str | None = None,
    owned: bool | None = None,
    limit: int = 60,
    offset: int = 0,
) -> tuple[list[CatalogCollection], int]:
    filters = dict(publisher=publisher, line=line, character=character, start=start, end=end, search=search, owned=owned)
    total = db.scalar(_apply_filters(select(func.count(CatalogCollection.id)), **filters))
    stmt = _apply_filters(select(CatalogCollection), **filters).options(
        selectinload(CatalogCollection.publisher),
        selectinload(CatalogCollection.reading_path),
        selectinload(CatalogCollection.tags),
    )
    # NULLS LAST needs SQLite 3.30; the host still ships 3.26, so sort the nulls
    # explicitly instead.
    stmt = stmt.order_by(
        CatalogCollection.first_published_on.is_(None).asc(),
        CatalogCollection.first_published_on.desc(),
        CatalogCollection.sort_title.asc(),
        CatalogCollection.id.asc(),
    )
    return list(db.scalars(stmt.offset(offset).limit(limit))), int(total or 0)


def collection_year_spans(
    db: Session, collections: Sequence[CatalogCollection]
) -> dict[int, tuple[int | None, int | None]]:
    """The years each collection covers, for placing it on the chronology board.

    Publication dates are used where they exist. Manga has none, so its series'
    run years stand in — the only date signal MangaPill gives us.
    """
    undated = {
        collection.canonical_series_id
        for collection in collections
        if collection.first_published_on is None and collection.canonical_series_id is not None
    }
    series_years: dict[int, tuple[int | None, int | None]] = {}
    if undated:
        series_years = {
            series_id: (start, end)
            for series_id, start, end in db.execute(
                select(CanonicalSeries.id, CanonicalSeries.start_year, CanonicalSeries.end_year).where(
                    CanonicalSeries.id.in_(undated)
                )
            ).all()
        }

    spans: dict[int, tuple[int | None, int | None]] = {}
    for collection in collections:
        if collection.first_published_on is not None:
            latest = collection.latest_published_on or collection.first_published_on
            spans[collection.id] = (collection.first_published_on.year, latest.year)
            continue
        start, end = series_years.get(collection.canonical_series_id or -1, (None, None))
        # An open-ended run is still going, so it reaches the present year.
        spans[collection.id] = (start, end or (date.today().year if start else None))
    return spans


def owned_counts(db: Session, collection_ids: Sequence[int]) -> dict[int, int]:
    """How many issues of each collection already exist as local files."""
    if not collection_ids:
        return {}
    rows = db.execute(
        select(CatalogCollectionItem.collection_id, func.count())
        .where(
            CatalogCollectionItem.collection_id.in_(collection_ids),
            CatalogCollectionItem.issue_id.is_not(None),
        )
        .group_by(CatalogCollectionItem.collection_id)
    ).all()
    return {collection_id: count for collection_id, count in rows}


def catalog_chronology(
    db: Session,
    *,
    publisher: Sequence[str] | None = None,
    line: str | None = None,
    character: str | None = None,
    start: date | None = None,
    end: date | None = None,
    search: str | None = None,
    owned: bool | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[ChronologyRow], int]:
    """Publication-order issues across the filtered catalogue, newest first."""
    base = (
        select(CanonicalIssue, CatalogCollection)
        .join(CatalogCollectionItem, CatalogCollectionItem.canonical_issue_id == CanonicalIssue.id)
        .join(CatalogCollection, CatalogCollection.id == CatalogCollectionItem.collection_id)
        .where(CanonicalIssue.published_on.is_not(None))
    )
    filters = dict(publisher=publisher, line=line, character=character, start=None, end=None, search=search, owned=owned)
    base = _apply_filters(base, **filters)
    if start:
        base = base.where(CanonicalIssue.published_on >= start)
    if end:
        base = base.where(CanonicalIssue.published_on <= end)

    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.execute(
        base.order_by(
            CanonicalIssue.published_on.desc(),
            CatalogCollection.sort_title.asc(),
            CanonicalIssue.sort_order.asc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        ChronologyRow(canonical_issue=issue, collection=collection, published_on=issue.published_on)
        for issue, collection in rows
    ], int(total or 0)


@dataclass(frozen=True)
class SeriesGroup:
    """One series, with the volumes that make it up."""

    canonical_series_id: int
    slug: str
    title: str
    publisher_slug: str
    publisher_name: str
    start_year: int | None
    end_year: int | None
    volume_count: int
    reading_path_ids: list[int]
    latest_published_on: date | None

    @property
    def is_ongoing(self) -> bool:
        """Still shipping if the newest issue is recent."""
        if self.latest_published_on is None:
            return False
        return self.latest_published_on >= date.today() - timedelta(days=120)

    def display_title(self, *, with_years: bool) -> str:
        """Marvel and DC series carry their run years; manga lines do not."""
        if not with_years or not self.start_year:
            return self.title
        if self.is_ongoing:
            return f"{self.title} ({self.start_year}–)"
        end = self.end_year or (self.latest_published_on.year if self.latest_published_on else None)
        if end and end != self.start_year:
            return f"{self.title} ({self.start_year}–{end})"
        return f"{self.title} ({self.start_year})"


def catalog_publishers(db: Session) -> list[Facet]:
    rows = db.execute(
        select(Publisher.slug, Publisher.name, func.count(CatalogCollection.id))
        .join(CatalogCollection, CatalogCollection.publisher_id == Publisher.id)
        .group_by(Publisher.slug, Publisher.name)
        .order_by(func.count(CatalogCollection.id).desc(), Publisher.name.asc())
    ).all()
    return [Facet(value=slug, label=name, count=count) for slug, name, count in rows]


def catalog_series(db: Session, publisher_slug: str) -> list[SeriesGroup]:
    """Group a publisher's volumes into the series they belong to."""
    rows = db.execute(
        select(
            CanonicalSeries.id,
            CanonicalSeries.slug,
            CanonicalSeries.title,
            Publisher.slug,
            Publisher.name,
            CanonicalSeries.start_year,
            CanonicalSeries.end_year,
            func.count(CatalogCollection.id),
            func.max(CatalogCollection.latest_published_on),
        )
        .join(CatalogCollection, CatalogCollection.canonical_series_id == CanonicalSeries.id)
        .join(Publisher, Publisher.id == CatalogCollection.publisher_id)
        .where(Publisher.slug == publisher_slug)
        .group_by(CanonicalSeries.id)
        .order_by(CanonicalSeries.title.asc())
    ).all()

    groups = []
    for series_id, slug, title, pub_slug, pub_name, start, end, volumes, latest in rows:
        paths = list(
            db.scalars(
                select(CatalogCollection.reading_path_id)
                .where(
                    CatalogCollection.canonical_series_id == series_id,
                    CatalogCollection.reading_path_id.is_not(None),
                )
                .order_by(CatalogCollection.sequence_number.asc(), CatalogCollection.first_published_on.asc())
            )
        )
        groups.append(
            SeriesGroup(
                canonical_series_id=series_id,
                slug=slug,
                title=title,
                publisher_slug=pub_slug,
                publisher_name=pub_name,
                start_year=start,
                end_year=end,
                volume_count=volumes,
                reading_path_ids=paths,
                latest_published_on=date.fromisoformat(latest) if isinstance(latest, str) else latest,
            )
        )
    return groups
