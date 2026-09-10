from __future__ import annotations

import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import _reading_list_read, _reading_list_year_spans, _series_cover_urls
from backend.app.models import (
    Base,
    CanonicalIssue,
    CanonicalSeries,
    CatalogCollection,
    Publisher,
    ReadingList,
    ReadingListItem,
    ReadingPath,
    ReadingPathCoverAsset,
    ReadingPathEntry,
    UserIssueState,
)


class ReadingListTests(unittest.TestCase):
    """Lists showed no covers at all, and no way to keep reading progress.

    DC and Marvel canonical issues carry no cover URL of their own, so the only
    source a list row ever consulted was always empty.
    """

    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()
        publisher = Publisher(slug="dc", name="DC Comics")
        self.db.add(publisher)
        self.db.flush()
        self.series = CanonicalSeries(
            slug="absolute-batman-2024", title="Absolute Batman", publisher_id=publisher.id
        )
        self.db.add(self.series)
        self.db.flush()

        # Volume one has shipped and has a cached cover; volume two has not.
        self.shipped = self._volume("vol-1", 1, cover=True, published_on=date(2024, 10, 1))
        self.future = self._volume("vol-2", 2, cover=False, published_on=date(2026, 6, 1))

        self.reading_list = ReadingList(name="Absolute Batman")
        self.db.add(self.reading_list)
        self.db.flush()
        for order, (path, entry) in enumerate([self.shipped, self.future]):
            self.db.add(
                ReadingListItem(
                    reading_list_id=self.reading_list.id,
                    reading_path_id=path.id,
                    reading_path_entry_id=entry.id,
                    title=entry.canonical_issue.title,
                    sort_order=order,
                )
            )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _volume(self, slug: str, number: int, *, cover: bool, published_on: date):
        path = ReadingPath(slug=slug, title=f"Absolute Batman: Vol. {number}", status="published")
        self.db.add(path)
        self.db.flush()
        if cover:
            self.db.add(
                ReadingPathCoverAsset(
                    reading_path_id=path.id,
                    status="ready",
                    cached_path=f"/tmp/{slug}.jpg",
                    content_type="image/jpeg",
                )
            )
        self.db.add(
            CatalogCollection(
                slug=slug,
                title=path.title,
                sort_title=path.title.lower(),
                canonical_series_id=self.series.id,
                reading_path_id=path.id,
                line="absolute",
                collection_type="run",
                sequence_number=number,
                first_published_on=published_on,
            )
        )
        issue = CanonicalIssue(
            series_id=self.series.id,
            legacy_key=f"absolute-batman-2024#{number}",
            issue_number=str(number),
            issue_kind="issue",
            title=f"Absolute Batman #{number}",
            sort_order=number,
            published_on=published_on,
        )
        self.db.add(issue)
        self.db.flush()
        entry = ReadingPathEntry(
            reading_path_id=path.id,
            canonical_issue_id=issue.id,
            sort_order=number,
            entry_type="issue",
            importance="main",
        )
        self.db.add(entry)
        self.db.flush()
        return path, entry

    def test_a_row_takes_the_volume_cover_when_the_issue_has_none(self) -> None:
        items = _reading_list_read(self.db, self.reading_list).items
        self.assertEqual(items[0].cover_url, f"/reading-paths/{self.shipped[0].id}/cover-image")

    def test_a_volume_still_to_ship_borrows_its_series_cover(self) -> None:
        items = _reading_list_read(self.db, self.reading_list).items
        # Volume two has no cover asset, so it shows volume one's.
        self.assertEqual(items[1].cover_url, f"/reading-paths/{self.shipped[0].id}/cover-image")

    def test_read_state_is_shared_with_the_collection_pages(self) -> None:
        entry = self.shipped[1]
        self.db.add(
            UserIssueState(
                issue_key=f"canonical:{entry.canonical_issue_id}",
                canonical_issue_id=entry.canonical_issue_id,
                is_read=True,
            )
        )
        self.db.commit()
        items = _reading_list_read(self.db, self.reading_list).items
        self.assertTrue(items[0].is_read)
        self.assertFalse(items[1].is_read)

    def test_a_row_carries_the_key_read_state_is_stored_under(self) -> None:
        items = _reading_list_read(self.db, self.reading_list).items
        self.assertEqual(items[0].canonical_issue_id, self.shipped[1].canonical_issue_id)
        self.assertEqual(items[0].published_on, date(2024, 10, 1))

    def test_a_list_reports_the_years_its_contents_span(self) -> None:
        spans = _reading_list_year_spans(self.db, [self.reading_list])
        self.assertEqual(spans[self.reading_list.id], (2024, 2026))

    def test_year_spans_survive_more_entries_than_sqlite_allows_variables(self) -> None:
        """SQLite caps a statement at 999 variables on the host.

        Asking for every list's entries in one IN clause ran to thousands of ids
        and made /reading-lists fail outright once enough lists existed.
        """
        crowded = ReadingList(name="Everything")
        self.db.add(crowded)
        self.db.flush()
        path, _ = self.shipped
        for order in range(1200):
            issue = CanonicalIssue(
                series_id=self.series.id,
                legacy_key=f"filler#{order}",
                issue_number=f"filler-{order}",
                issue_kind="issue",
                sort_order=order,
                published_on=date(2020 + order % 5, 1, 1),
            )
            self.db.add(issue)
            self.db.flush()
            entry = ReadingPathEntry(
                reading_path_id=path.id,
                canonical_issue_id=issue.id,
                sort_order=1000 + order,
                entry_type="issue",
                importance="main",
            )
            self.db.add(entry)
            self.db.flush()
            self.db.add(
                ReadingListItem(
                    reading_list_id=crowded.id,
                    reading_path_id=path.id,
                    reading_path_entry_id=entry.id,
                    title=f"Filler #{order}",
                    sort_order=order,
                )
            )
        self.db.commit()

        spans = _reading_list_year_spans(self.db, [crowded])

        self.assertEqual(spans[crowded.id], (2020, 2024))

    def test_an_empty_list_has_no_years(self) -> None:
        empty = ReadingList(name="Nothing yet")
        self.db.add(empty)
        self.db.commit()
        self.assertEqual(_reading_list_year_spans(self.db, [empty]), {})

    def test_series_covers_prefer_the_earliest_volume(self) -> None:
        covers = _series_cover_urls(self.db, [self.series.id])
        self.assertEqual(covers[self.series.id], f"/reading-paths/{self.shipped[0].id}/cover-image")

    def test_a_series_with_no_cached_cover_anywhere_yields_nothing(self) -> None:
        self.assertEqual(_series_cover_urls(self.db, [self.series.id + 999]), {})


if __name__ == "__main__":
    unittest.main()


class SeriesCoverFallbackTests(unittest.TestCase):
    """A volume borrows its series' cover however that cover is stored.

    Batman and Immortal Thor kept their covers as publisher CDN URLs rather than
    cached files, so a fallback that only looked for a cached path left all four
    of their later volumes blank.
    """

    def setUp(self) -> None:
        engine = create_engine("sqlite://", future=True)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, future=True)()
        publisher = Publisher(slug="dc", name="DC Comics")
        self.db.add(publisher)
        self.db.flush()
        self.series = CanonicalSeries(slug="batman-2016", title="Batman", publisher_id=publisher.id)
        self.db.add(self.series)
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()

    def _volume(self, number: int, *, source_image_url: str | None, cached_path: str | None) -> ReadingPath:
        path = ReadingPath(slug=f"batman-2016-vol-{number}", title=f"Batman: Vol. {number}", status="published")
        self.db.add(path)
        self.db.flush()
        if source_image_url or cached_path:
            self.db.add(
                ReadingPathCoverAsset(
                    reading_path_id=path.id,
                    status="ready",
                    source_image_url=source_image_url,
                    cached_path=cached_path,
                )
            )
        self.db.add(
            CatalogCollection(
                slug=path.slug,
                title=path.title,
                sort_title=path.title.lower(),
                canonical_series_id=self.series.id,
                reading_path_id=path.id,
                line="series",
                collection_type="run",
                sequence_number=number,
            )
        )
        self.db.flush()
        return path

    def test_a_cdn_cover_on_an_earlier_volume_is_reused(self) -> None:
        self._volume(1, source_image_url="https://static.dc.com/BM_Cv158.jpg", cached_path=None)
        self._volume(3, source_image_url=None, cached_path=None)
        self.db.commit()
        covers = _series_cover_urls(self.db, [self.series.id])
        self.assertEqual(covers[self.series.id], "https://static.dc.com/BM_Cv158.jpg")

    def test_a_volume_whose_cover_never_resolved_is_not_offered(self) -> None:
        self._volume(1, source_image_url=None, cached_path=None)
        self.db.commit()
        self.assertEqual(_series_cover_urls(self.db, [self.series.id]), {})

    def test_the_earliest_volume_with_a_cover_wins(self) -> None:
        self._volume(1, source_image_url="https://static.dc.com/vol1.jpg", cached_path=None)
        self._volume(2, source_image_url="https://static.dc.com/vol2.jpg", cached_path=None)
        self.db.commit()
        covers = _series_cover_urls(self.db, [self.series.id])
        self.assertEqual(covers[self.series.id], "https://static.dc.com/vol1.jpg")
