from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.main import get_reading_path_covers
from backend.app.models import Base, ReadingPath, ReadingPathCoverAsset
from backend.app.services.covers import (
    ensure_query_cover_image,
    ensure_reading_path_cover_asset,
    fetch_getcomics_cover,
    parse_getcomics_search_results,
)


class CoverParsingTests(unittest.TestCase):
    def test_parse_getcomics_search_results_extracts_first_article_cover(self) -> None:
        html = """
        <html>
          <body>
            <article>
              <div class="post-header-image">
                <a href="https://getcomics.org/dc/absolute-batman-19-2026/">
                  <img src="https://i0.wp.com/getcomics.org/share/uploads/2026/04/Absolute-Batman-19-2026.jpg?fit=400%2C615&amp;ssl=1" alt="Absolute Batman #19 (2026)">
                </a>
              </div>
              <div class="post-info">
                <h1 class="post-title">
                  <a href="https://getcomics.org/dc/absolute-batman-19-2026/">Absolute Batman #19 (2026)</a>
                </h1>
              </div>
            </article>
          </body>
        </html>
        """

        result = parse_getcomics_search_results(html, "Absolute Batman #19")

        self.assertEqual(result.query, "Absolute Batman #19")
        self.assertEqual(result.post_title, "Absolute Batman #19 (2026)")
        self.assertEqual(result.post_url, "https://getcomics.org/dc/absolute-batman-19-2026/")
        self.assertIn("Absolute-Batman-19-2026.jpg", result.image_url or "")

    def test_parse_getcomics_search_results_falls_back_to_og_image(self) -> None:
        html = """
        <html>
          <head>
            <meta property="og:image" content="https://i0.wp.com/getcomics.org/share/uploads/2026/04/fallback.jpg" />
          </head>
          <body></body>
        </html>
        """

        result = parse_getcomics_search_results(html, "Fallback Query")

        self.assertEqual(result.query, "Fallback Query")
        self.assertEqual(result.image_url, "https://i0.wp.com/getcomics.org/share/uploads/2026/04/fallback.jpg")
        self.assertIsNone(result.post_url)
        self.assertIsNone(result.post_title)

    def test_fetch_getcomics_cover_prefers_main_series_over_infinity_comic(self) -> None:
        html = """
        <html><body>
          <article>
            <div class="post-header-image"><img src="https://img.test/infinity.jpg"></div>
            <div class="post-info"><h1 class="post-title"><a href="https://getcomics.org/marvel/ultimate-spider-man-infinity-comic-6-2024/">Ultimate Spider-Man &#8211; Infinity Comic #6 (2024)</a></h1></div>
          </article>
          <article>
            <div class="post-header-image"><img src="https://img.test/main.jpg"></div>
            <div class="post-info"><h1 class="post-title"><a href="https://getcomics.org/marvel/ultimate-spider-man-6-2024/">Ultimate Spider-Man #6 (2024)</a></h1></div>
          </article>
        </body></html>
        """

        mock_response = MagicMock()
        mock_response.read.return_value = html.encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False

        with patch("backend.app.services.covers.urlopen", return_value=mock_response):
            fetch_getcomics_cover.cache_clear()
            result = fetch_getcomics_cover(
                "Ultimate Spider-Man #6",
                expected_series_title="Ultimate Spider-Man",
                expected_issue_number="6",
                expected_year=2024,
            )

        self.assertEqual(result.post_title, "Ultimate Spider-Man #6 (2024)")
        self.assertEqual(result.image_url, "https://img.test/main.jpg")

    def test_fetch_getcomics_cover_prefers_the_ultimates_over_other_ultimate_books(self) -> None:
        html = """
        <html><body>
          <article>
            <div class="post-header-image"><img src="https://img.test/wolverine.jpg"></div>
            <div class="post-info"><h1 class="post-title"><a href="https://getcomics.org/marvel/ultimate-wolverine-6-2025/">Ultimate Wolverine #6 (2025)</a></h1></div>
          </article>
          <article>
            <div class="post-header-image"><img src="https://img.test/ultimates.jpg"></div>
            <div class="post-info"><h1 class="post-title"><a href="https://getcomics.org/marvel/the-ultimates-6-2024/">The Ultimates #6 (2024)</a></h1></div>
          </article>
        </body></html>
        """

        mock_response = MagicMock()
        mock_response.read.return_value = html.encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False

        with patch("backend.app.services.covers.urlopen", return_value=mock_response):
            fetch_getcomics_cover.cache_clear()
            result = fetch_getcomics_cover(
                "Ultimates #6",
                expected_series_title="Ultimates",
                expected_issue_number="6",
                expected_year=2024,
            )

        self.assertEqual(result.post_title, "The Ultimates #6 (2024)")
        self.assertEqual(result.image_url, "https://img.test/ultimates.jpg")

    def test_fetch_getcomics_cover_prefers_matching_year_for_ultimates(self) -> None:
        html = """
        <html><body>
          <article>
            <div class="post-header-image"><img src="https://img.test/old-ultimates.jpg"></div>
            <div class="post-info"><h1 class="post-title"><a href="https://getcomics.org/marvel/ultimates-2-6-2017/">Ultimates 2 #6 (2017)</a></h1></div>
          </article>
          <article>
            <div class="post-header-image"><img src="https://img.test/current-ultimates.jpg"></div>
            <div class="post-info"><h1 class="post-title"><a href="https://getcomics.org/marvel/the-ultimates-6-2024/">The Ultimates #6 (2024)</a></h1></div>
          </article>
        </body></html>
        """

        mock_response = MagicMock()
        mock_response.read.return_value = html.encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False

        with patch("backend.app.services.covers.urlopen", return_value=mock_response):
            fetch_getcomics_cover.cache_clear()
            result = fetch_getcomics_cover(
                "Ultimates #6",
                expected_series_title="Ultimates",
                expected_issue_number="6",
                expected_year=2024,
            )

        self.assertEqual(result.post_title, "The Ultimates #6 (2024)")
        self.assertEqual(result.image_url, "https://img.test/current-ultimates.jpg")

    def test_fetch_getcomics_cover_allows_collected_edition_titles(self) -> None:
        html = """
        <html><body>
          <article>
            <div class="post-header-image"><img src="https://img.test/issue-1096.jpg"></div>
            <div class="post-info"><h1 class="post-title"><a href="https://getcomics.org/dc/detective-comics-1096-2025/">Detective Comics #1096 (2025)</a></h1></div>
          </article>
          <article>
            <div class="post-header-image"><img src="https://img.test/mercy-tpb.jpg"></div>
            <div class="post-info"><h1 class="post-title"><a href="https://getcomics.org/dc/batman-detective-comics-vol-1-mercy-of-the-father-tpb-2025/">Batman - Detective Comics Vol. 1 - Mercy of the Father (TPB) (2025)</a></h1></div>
          </article>
        </body></html>
        """

        mock_response = MagicMock()
        mock_response.read.return_value = html.encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False

        with patch("backend.app.services.covers.urlopen", return_value=mock_response):
            fetch_getcomics_cover.cache_clear()
            result = fetch_getcomics_cover(
                "Batman - Detective Comics Vol. 1 - Mercy of the Father (TPB)",
                expected_series_title="Batman - Detective Comics Vol. 1 - Mercy of the Father (TPB)",
                expected_issue_number="1090-1096",
                expected_year=2025,
            )

        self.assertEqual(result.post_title, "Batman - Detective Comics Vol. 1 - Mercy of the Father (TPB) (2025)")
        self.assertEqual(result.image_url, "https://img.test/mercy-tpb.jpg")


class CoverAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "test.db"
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(bind=engine)
        self.session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_ensure_reading_path_cover_asset_downloads_and_caches_local_file(self) -> None:
        with self.session_factory() as db:
            reading_path = ReadingPath(slug="sample-path", title="Sample Path", status="published")
            db.add(reading_path)
            db.commit()
            db.refresh(reading_path)

            with (
                patch(
                    "backend.app.services.covers.fetch_getcomics_cover",
                    return_value=type(
                        "Cover",
                        (),
                        {
                            "query": "Sample Path",
                            "image_url": "https://img.test/sample.jpg",
                            "post_url": "https://getcomics.org/sample-path/",
                            "post_title": "Sample Path #1",
                        },
                    )(),
                ),
                patch(
                    "backend.app.services.covers.COVER_CACHE_DIR",
                    Path(self.temp_dir.name) / "cache",
                ),
                patch(
                    "backend.app.services.covers._download_binary",
                    return_value=(b"fake-image", "image/jpeg"),
                ),
            ):
                asset = ensure_reading_path_cover_asset(
                    db,
                    reading_path_id=reading_path.id,
                    query="Sample Path",
                    expected_series_title="Sample Path",
                    expected_issue_number="1",
                    expected_year=2026,
                )
                db.commit()

            self.assertEqual(asset.status, "ready")
            self.assertEqual(asset.post_url, "https://getcomics.org/sample-path/")
            self.assertTrue(asset.cached_path)
            self.assertTrue(Path(asset.cached_path or "").exists())
            self.assertEqual(Path(asset.cached_path or "").read_bytes(), b"fake-image")

    def test_ensure_reading_path_cover_asset_reuses_existing_cached_file(self) -> None:
        with self.session_factory() as db:
            reading_path = ReadingPath(slug="sample-path", title="Sample Path", status="published")
            db.add(reading_path)
            db.commit()
            db.refresh(reading_path)

            cache_dir = Path(self.temp_dir.name) / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached_file = cache_dir / "reading-path-1-existing.jpg"
            cached_file.write_bytes(b"cached")

            asset = ReadingPathCoverAsset(
                reading_path_id=reading_path.id,
                query="Sample Path",
                post_url="https://getcomics.org/sample-path/",
                post_title="Sample Path #1",
                source_image_url="https://img.test/sample.jpg",
                cached_path=str(cached_file),
                content_type="image/jpeg",
                status="ready",
            )
            db.add(asset)
            db.commit()

            with patch("backend.app.services.covers.fetch_getcomics_cover") as fetch_cover, patch(
                "backend.app.services.covers._download_binary"
            ) as download_binary:
                refreshed = ensure_reading_path_cover_asset(
                    db,
                    reading_path_id=reading_path.id,
                    query="Sample Path",
                    expected_series_title="Sample Path",
                    expected_issue_number="1",
                    expected_year=2026,
                )

            self.assertEqual(refreshed.cached_path, str(cached_file))
            fetch_cover.assert_not_called()
            download_binary.assert_not_called()

    def test_ensure_query_cover_image_downloads_and_reuses_cached_file(self) -> None:
        with (
            patch(
                "backend.app.services.covers.fetch_getcomics_cover",
                return_value=type(
                    "Cover",
                    (),
                    {
                        "query": "Ultimate Spider-Man #6",
                        "image_url": "https://img.test/ultimate-spider-man-6.jpg",
                        "post_url": "https://getcomics.org/marvel/ultimate-spider-man-6-2024/",
                        "post_title": "Ultimate Spider-Man #6 (2024)",
                    },
                )(),
            ),
            patch(
                "backend.app.services.covers.ENTRY_COVER_CACHE_DIR",
                Path(self.temp_dir.name) / "entry-cache",
            ),
            patch(
                "backend.app.services.covers._download_binary",
                return_value=(b"entry-image", "image/jpeg"),
            ) as download_binary,
        ):
            cached_path, content_type, cover = ensure_query_cover_image(
                cache_key="reading-path-12-entry-10905",
                query="Ultimate Spider-Man #6",
                expected_series_title="Ultimate Spider-Man",
                expected_issue_number="6",
                expected_year=2024,
            )

            self.assertIsNotNone(cached_path)
            self.assertEqual(content_type, "image/jpeg")
            self.assertEqual(cover.post_title, "Ultimate Spider-Man #6 (2024)")
            self.assertTrue(cached_path.exists())
            self.assertEqual(cached_path.read_bytes(), b"entry-image")

            cached_again, content_type_again, _ = ensure_query_cover_image(
                cache_key="reading-path-12-entry-10905",
                query="Ultimate Spider-Man #6",
                expected_series_title="Ultimate Spider-Man",
                expected_issue_number="6",
                expected_year=2024,
            )

            self.assertEqual(cached_again, cached_path)
            self.assertEqual(content_type_again, "image/jpeg")
            download_binary.assert_called_once()

    def test_get_reading_path_covers_returns_partial_results_when_one_cover_lookup_fails(self) -> None:
        with self.session_factory() as db:
            first = ReadingPath(slug="first-path", title="First Path", status="published")
            second = ReadingPath(slug="second-path", title="Second Path", status="published")
            db.add_all([first, second])
            db.commit()
            db.refresh(first)
            db.refresh(second)

            def fake_ensure_cover_asset(session, *, reading_path_id, **kwargs):
                if reading_path_id == second.id:
                    raise RuntimeError("boom")
                return SimpleNamespace(
                    status="ready",
                    cached_path="/tmp/fake-cover.jpg",
                    post_url="https://getcomics.org/sample/",
                    post_title="Sample #1",
                    query="Sample #1",
                )

            with patch("backend.app.main.ensure_reading_path_cover_asset", side_effect=fake_ensure_cover_asset):
                payload = get_reading_path_covers(ids=f"{first.id},{second.id}", db=db)

            self.assertEqual(len(payload.items), 2)
            self.assertEqual(payload.items[0].reading_path_id, first.id)
            self.assertEqual(payload.items[0].image_url, f"/reading-paths/{first.id}/cover-image")
            self.assertEqual(payload.items[1].reading_path_id, second.id)
            self.assertIsNone(payload.items[1].image_url)
            self.assertEqual(payload.items[1].query, "Second Path")


if __name__ == "__main__":
    unittest.main()
