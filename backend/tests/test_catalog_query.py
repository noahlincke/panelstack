from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app.models import (
    Base,
    CanonicalIssue,
    CanonicalSeries,
    CatalogCollection,
    CatalogCollectionItem,
    CatalogCollectionTag,
    Publisher,
)
from backend.app.services.catalog_query import catalog_chronology, catalog_collections, catalog_facets


class CatalogQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()
        self._seed()

    def tearDown(self) -> None:
        self.db.close()

    def _seed(self) -> None:
        dc = Publisher(slug="dc", name="DC Comics")
        marvel = Publisher(slug="marvel", name="Marvel")
        shueisha = Publisher(slug="shueisha", name="Shueisha")
        self.db.add_all([dc, marvel, shueisha])
        self.db.flush()

        self.collections: dict[str, CatalogCollection] = {}
        plan = [
            ("detective", "Detective Comics: Vol. 1", dc, "series", date(2024, 10, 1), date(2025, 4, 1), ["bat-family"]),
            ("absolute", "Absolute Batman: Vol. 1", dc, "absolute", date(2024, 10, 1), date(2025, 6, 1), ["bat-family"]),
            ("avengers", "Avengers: Vol. 3", marvel, "series", date(2025, 1, 1), date(2026, 3, 1), ["avengers-family"]),
            ("legacy", "Legacy Run", dc, "series", date(1990, 1, 1), date(1991, 1, 1), []),
            ("spy", "Spy x Family: Vol. 1", shueisha, "series", date(2024, 1, 1), date(2025, 1, 1), ["spy-x-family"]),
        ]
        for index, (key, title, publisher, line, first, latest, tags) in enumerate(plan):
            series = CanonicalSeries(slug=f"{key}-series", title=title, publisher_id=publisher.id)
            self.db.add(series)
            self.db.flush()
            collection = CatalogCollection(
                slug=key,
                title=title,
                sort_title=title.lower(),
                publisher_id=publisher.id,
                canonical_series_id=series.id,
                line=line,
                collection_type="run",
                sequence_number=index,
                first_published_on=first,
                latest_published_on=latest,
            )
            self.db.add(collection)
            self.db.flush()
            for tag in tags:
                self.db.add(CatalogCollectionTag(collection_id=collection.id, tag=tag))
            for offset, published_on in enumerate((first, latest)):
                issue = CanonicalIssue(
                    series_id=series.id,
                    legacy_key=f"{key}#{offset}",
                    issue_number=str(offset + 1),
                    issue_kind="issue",
                    title=f"{title} #{offset + 1}",
                    sort_order=offset,
                    published_on=published_on,
                )
                self.db.add(issue)
                self.db.flush()
                self.db.add(
                    CatalogCollectionItem(
                        collection_id=collection.id,
                        canonical_issue_id=issue.id,
                        sort_order=offset,
                        item_type="issue",
                    )
                )
            self.collections[key] = collection
        self.db.commit()

    def test_facets_exclude_manga_titles_that_end_in_family(self) -> None:
        facets = catalog_facets(self.db)
        characters = {facet.value for facet in facets.characters}
        self.assertIn("bat-family", characters)
        self.assertNotIn("spy-x-family", characters)

    def test_character_labels_are_readable(self) -> None:
        labels = {facet.value: facet.label for facet in catalog_facets(self.db).characters}
        self.assertEqual(labels["bat-family"], "Bat Family")

    def test_collections_filter_by_publisher_set(self) -> None:
        collections, total = catalog_collections(self.db, publisher=["dc", "marvel"])
        self.assertEqual(total, 4)
        self.assertNotIn("Spy x Family: Vol. 1", {collection.title for collection in collections})

    def test_collections_filter_by_window_keeps_overlapping_runs(self) -> None:
        collections, total = catalog_collections(self.db, start=date(2019, 1, 1), end=date(2026, 8, 31))
        titles = {collection.title for collection in collections}
        self.assertEqual(total, 4)
        self.assertNotIn("Legacy Run", titles)

    def test_collections_filter_by_line_and_character(self) -> None:
        _, absolute_total = catalog_collections(self.db, line="absolute")
        self.assertEqual(absolute_total, 1)
        collections, bat_total = catalog_collections(self.db, character="bat-family")
        self.assertEqual(bat_total, 2)
        self.assertEqual({c.line for c in collections}, {"series", "absolute"})

    def test_collections_sort_undated_runs_last(self) -> None:
        self.db.add(
            CatalogCollection(
                slug="undated",
                title="Undated Run",
                sort_title="undated run",
                line="series",
                collection_type="run",
                sequence_number=99,
            )
        )
        self.db.commit()
        collections, _ = catalog_collections(self.db)
        self.assertEqual(collections[-1].title, "Undated Run")

    def test_collection_ordering_never_emits_nulls_last(self) -> None:
        """The host runs SQLite 3.26, which cannot parse NULLS LAST."""
        statements: list[str] = []

        @event.listens_for(self.db.get_bind(), "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            statements.append(statement)

        catalog_collections(self.db)
        self.assertTrue(statements)
        self.assertNotIn("NULLS", " ".join(statements).upper())

    def test_chronology_is_newest_first_and_window_bounded(self) -> None:
        rows, total = catalog_chronology(self.db, publisher=["dc", "marvel"], start=date(2019, 1, 1), end=date(2026, 8, 31))
        self.assertEqual(total, 6)
        published = [row.published_on for row in rows]
        self.assertEqual(published, sorted(published, reverse=True))

    def test_chronology_window_uses_issue_dates_not_collection_dates(self) -> None:
        _, total = catalog_chronology(self.db, publisher=["dc"], start=date(2025, 1, 1), end=date(2025, 12, 31))
        self.assertEqual(total, 2)


if __name__ == "__main__":
    unittest.main()
