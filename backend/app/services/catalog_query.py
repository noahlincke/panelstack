from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    CanonicalIssue,
    CatalogCollection,
    CatalogCollectionItem,
    CatalogCollectionTag,
    Publisher,
)

# Character and team tags are written by the catalog sync as "<slug>-family".
FAMILY_SUFFIX = "-family"
# The suffix also matches manga titles such as "Spy x Family", so character facets
# are scoped to the publishers that actually use family groupings.
FAMILY_PUBLISHER_SLUGS = ("dc", "marvel")


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
    return _titleize(tag).replace("X Men", "X-Men")


def _apply_filters(
    stmt: Select,
    *,
    publisher: Sequence[str] | None,
    line: str | None,
    character: str | None,
    start: date | None,
    end: date | None,
    search: str | None,
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
        stmt = stmt.where(CatalogCollection.latest_published_on.is_not(None), CatalogCollection.latest_published_on >= start)
    if end:
        stmt = stmt.where(CatalogCollection.first_published_on.is_not(None), CatalogCollection.first_published_on <= end)
    if search:
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(CatalogCollection.title).like(needle),
                func.lower(CatalogCollection.sort_title).like(needle),
            )
        )
    return stmt


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
    character_rows = db.execute(
        select(CatalogCollectionTag.tag, func.count(CatalogCollectionTag.collection_id))
        .join(CatalogCollection, CatalogCollection.id == CatalogCollectionTag.collection_id)
        .join(Publisher, Publisher.id == CatalogCollection.publisher_id)
        .where(CatalogCollectionTag.tag.like(f"%{FAMILY_SUFFIX}"), Publisher.slug.in_(FAMILY_PUBLISHER_SLUGS))
        .group_by(CatalogCollectionTag.tag)
        .order_by(func.count(CatalogCollectionTag.collection_id).desc(), CatalogCollectionTag.tag.asc())
    ).all()
    span = db.execute(
        select(func.min(CatalogCollection.first_published_on), func.max(CatalogCollection.latest_published_on))
    ).one()
    return CatalogFacets(
        publishers=[Facet(value=slug, label=name, count=count) for slug, name, count in publisher_rows],
        lines=[Facet(value=line, label=_titleize(line), count=count) for line, count in line_rows],
        characters=[Facet(value=tag, label=_character_label(tag), count=count) for tag, count in character_rows],
        min_year=span[0].year if span[0] else None,
        max_year=span[1].year if span[1] else None,
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
    limit: int = 60,
    offset: int = 0,
) -> tuple[list[CatalogCollection], int]:
    filters = dict(publisher=publisher, line=line, character=character, start=start, end=end, search=search)
    total = db.scalar(_apply_filters(select(func.count(CatalogCollection.id)), **filters))
    stmt = _apply_filters(select(CatalogCollection), **filters).options(
        selectinload(CatalogCollection.publisher),
        selectinload(CatalogCollection.reading_path),
    )
    stmt = stmt.order_by(
        CatalogCollection.first_published_on.desc().nullslast(),
        CatalogCollection.sort_title.asc(),
        CatalogCollection.id.asc(),
    )
    return list(db.scalars(stmt.offset(offset).limit(limit))), int(total or 0)


def catalog_chronology(
    db: Session,
    *,
    publisher: Sequence[str] | None = None,
    line: str | None = None,
    character: str | None = None,
    start: date | None = None,
    end: date | None = None,
    search: str | None = None,
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
    filters = dict(publisher=publisher, line=line, character=character, start=None, end=None, search=search)
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
