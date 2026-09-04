from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app.models import (
    Base,
    ReadingPath,
    CanonicalIssue,
    CanonicalSeries,
    CatalogCollection,
    CatalogCollectionItem,
    CatalogCollectionTag,
    Publisher,
)
from backend.app.services.catalog_query import (
    catalog_chronology,
    catalog_collections,
    catalog_facets,
    catalog_publishers,
    catalog_series,
)


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


class SeriesGroupingTests(unittest.TestCase):
    """Collections browse as publisher -> series -> issues, not a flat volume list."""

    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()
        dc = Publisher(slug="dc", name="DC Comics")
        manga = Publisher(slug="shueisha", name="Shueisha")
        self.db.add_all([dc, manga])
        self.db.flush()
        self._series(dc, "Absolute Batman", 2024, None, volumes=3, latest=date.today())
        self._series(dc, "Old Run", 1990, 1992, volumes=2, latest=date(1992, 5, 1))
        self._series(manga, "Jujutsu Kaisen", 2018, None, volumes=4, latest=date.today())
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _series(self, publisher, title, start, end, *, volumes, latest) -> None:
        series = CanonicalSeries(
            slug=title.lower().replace(" ", "-"),
            title=title,
            publisher_id=publisher.id,
            start_year=start,
            end_year=end,
        )
        self.db.add(series)
        self.db.flush()
        for index in range(volumes):
            path = ReadingPath(slug=f"{series.slug}-vol-{index + 1}", title=f"{title}: Vol. {index + 1}", status="published")
            self.db.add(path)
            self.db.flush()
            self.db.add(
                CatalogCollection(
                    slug=f"{series.slug}-{index}",
                    title=f"{title}: Vol. {index + 1}",
                    sort_title=title.lower(),
                    publisher_id=publisher.id,
                    canonical_series_id=series.id,
                    reading_path_id=path.id,
                    line="series",
                    collection_type="run",
                    sequence_number=index,
                    first_published_on=date(start, 1, 1),
                    latest_published_on=latest,
                )
            )

    def test_volumes_group_into_one_series_entry(self) -> None:
        groups = {g.title: g for g in catalog_series(self.db, "dc")}
        self.assertEqual(groups["Absolute Batman"].volume_count, 3)
        self.assertEqual(len(groups["Absolute Batman"].reading_path_ids), 3)

    def test_an_ongoing_run_leaves_the_end_year_open(self) -> None:
        group = next(g for g in catalog_series(self.db, "dc") if g.title == "Absolute Batman")
        self.assertTrue(group.is_ongoing)
        self.assertEqual(group.display_title(with_years=True), "Absolute Batman (2024–)")

    def test_a_finished_run_shows_both_years(self) -> None:
        group = next(g for g in catalog_series(self.db, "dc") if g.title == "Old Run")
        self.assertFalse(group.is_ongoing)
        self.assertEqual(group.display_title(with_years=True), "Old Run (1990–1992)")

    def test_manga_titles_carry_no_run_years(self) -> None:
        group = catalog_series(self.db, "shueisha")[0]
        self.assertEqual(group.display_title(with_years=False), "Jujutsu Kaisen")

    def test_publishers_are_ranked_by_volume_count(self) -> None:
        publishers = [p.value for p in catalog_publishers(self.db)]
        self.assertEqual(publishers[0], "dc")
        self.assertIn("shueisha", publishers)


if __name__ == "__main__":
    unittest.main()
