from __future__ import annotations

import base64
import os
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from backend.app.auth import hash_password, verify_basic_auth
from backend.app.services.opds import (
    ACQUISITION_LINK_TYPE,
    ACQUISITION_REL,
    ACQUISITION_TYPE,
    NAVIGATION_LINK_TYPE,
    NAVIGATION_TYPE,
    acquisition_entry,
    archive_media_type,
    feed,
    navigation_entry,
)

ATOM = "{http://www.w3.org/2005/Atom}"


def parse(document: str) -> ElementTree.Element:
    return ElementTree.fromstring(document)


class BasicAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hash = hash_password("hunter2", iterations=1000)

    def _header(self, user: str, password: str) -> str:
        return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def test_the_app_password_is_accepted_whatever_the_username(self) -> None:
        with patch.dict(os.environ, {"APP_PASSWORD_HASH": self.hash}):
            self.assertTrue(verify_basic_auth(self._header("panels", "hunter2")))
            self.assertTrue(verify_basic_auth(self._header("anything", "hunter2")))

    def test_a_wrong_password_is_refused(self) -> None:
        with patch.dict(os.environ, {"APP_PASSWORD_HASH": self.hash}):
            self.assertFalse(verify_basic_auth(self._header("panels", "wrong")))
            self.assertFalse(verify_basic_auth(None))
            self.assertFalse(verify_basic_auth("Bearer token"))
            self.assertFalse(verify_basic_auth("Basic not-base64"))

    def test_auth_off_lets_everything_through(self) -> None:
        with patch.dict(os.environ, {"APP_PASSWORD_HASH": ""}):
            self.assertTrue(verify_basic_auth(None))


class MediaTypeTests(unittest.TestCase):
    def test_archives_map_to_comic_book_types(self) -> None:
        self.assertEqual(archive_media_type("Book 001.cbz"), "application/vnd.comicbook+zip")
        self.assertEqual(archive_media_type("Book 001.cbr"), "application/vnd.comicbook-rar")
        self.assertEqual(archive_media_type("Book.pdf"), "application/pdf")

    def test_an_unknown_name_falls_back_rather_than_failing(self) -> None:
        self.assertEqual(archive_media_type(None), "application/vnd.comicbook+zip")
        self.assertEqual(archive_media_type("mystery"), "application/vnd.comicbook+zip")


class FeedTests(unittest.TestCase):
    def test_a_navigation_feed_is_well_formed_atom(self) -> None:
        document = feed(
            feed_id="urn:test",
            title="Panel Stack",
            self_href="https://example.test/opds",
            start_href="https://example.test/opds",
            entries=[
                navigation_entry(identifier="urn:test:lists", title="Reading lists", href="https://example.test/opds/lists")
            ],
        )
        root = parse(document)
        self.assertEqual(root.find(f"{ATOM}title").text, "Panel Stack")
        self.assertEqual(len(root.findall(f"{ATOM}entry")), 1)
        self_link = next(l for l in root.findall(f"{ATOM}link") if l.get("rel") == "self")
        self.assertEqual(self_link.get("type"), NAVIGATION_LINK_TYPE)

    def test_a_list_entry_advertises_an_acquisition_feed(self) -> None:
        entry = navigation_entry(
            identifier="urn:test:list:1", title="Absolute Batman", href="https://example.test/opds/lists/1", kind="acquisition"
        )
        self.assertEqual(entry.links[0].type, ACQUISITION_LINK_TYPE)

    def test_an_acquisition_entry_carries_a_download_and_a_cover(self) -> None:
        document = feed(
            feed_id="urn:test:list:1",
            title="Absolute Batman",
            self_href="https://example.test/opds/lists/1",
            start_href="https://example.test/opds",
            kind="acquisition",
            entries=[
                acquisition_entry(
                    identifier="urn:test:item:1",
                    title="Absolute Batman Vol. 1",
                    download_href="https://example.test/api/reading-paths/1/entries/2/download",
                    media_type="application/vnd.comicbook+zip",
                    cover_href="https://example.test/api/reading-paths/1/entries/2/cover-image",
                )
            ],
        )
        root = parse(document)
        entry = root.find(f"{ATOM}entry")
        acquisition = next(l for l in entry.findall(f"{ATOM}link") if l.get("rel") == ACQUISITION_REL)
        self.assertEqual(acquisition.get("type"), "application/vnd.comicbook+zip")
        self.assertTrue(acquisition.get("href").endswith("/download"))
        self.assertTrue(any(l.get("rel").endswith("thumbnail") for l in entry.findall(f"{ATOM}link")))

    def test_titles_with_xml_characters_do_not_break_the_feed(self) -> None:
        document = feed(
            feed_id="urn:test",
            title="Ampersands & <angles>",
            self_href="https://example.test/opds",
            start_href="https://example.test/opds",
            entries=[
                navigation_entry(identifier="urn:test:1", title='Quote " and & more', href="https://example.test/opds/1")
            ],
        )
        root = parse(document)
        self.assertEqual(root.find(f"{ATOM}title").text, "Ampersands & <angles>")



class CollectedEditionQueryTests(unittest.TestCase):
    """Trade subtitles are unreliable, so the search loosens before giving up."""

    def test_the_subtitle_is_dropped_after_the_exact_title(self) -> None:
        from backend.app.services.opds import collected_edition_queries

        queries = collected_edition_queries("Absolute Batman Vol. 2 - The Hunt (TPB)")
        self.assertEqual(queries[0], "Absolute Batman Vol. 2 - The Hunt (TPB)")
        self.assertIn("Absolute Batman Vol. 2 (TPB)", queries)
        self.assertIn("Absolute Batman Vol. 2", queries)

    def test_a_title_without_a_volume_is_not_truncated(self) -> None:
        from backend.app.services.opds import collected_edition_queries

        queries = collected_edition_queries("Far Sector (TPB)")
        self.assertEqual(queries, ["Far Sector (TPB)", "Far Sector"])

    def test_queries_are_unique_and_ordered(self) -> None:
        from backend.app.services.opds import collected_edition_queries

        queries = collected_edition_queries("Immortal X-Men Vol. 1 (TPB)")
        self.assertEqual(len(queries), len(set(queries)))
        self.assertEqual(queries[0], "Immortal X-Men Vol. 1 (TPB)")


class FeedCharsetTests(unittest.TestCase):
    def test_feed_content_types_declare_utf8(self) -> None:
        self.assertIn("charset=utf-8", NAVIGATION_TYPE)
        self.assertIn("charset=utf-8", ACQUISITION_TYPE)

    def test_link_types_stay_charset_free(self) -> None:
        self.assertNotIn("charset", NAVIGATION_LINK_TYPE)
        self.assertNotIn("charset", ACQUISITION_LINK_TYPE)


class ChallengeTests(unittest.TestCase):
    """An OPDS reader can only authenticate if the 401 carries a challenge.

    Acquisition and cover URLs used to live under /api, whose 401 is a plain JSON
    body, so Panels loaded the catalog and then failed every download.
    """

    def _routes(self) -> set[str]:
        import backend.app.main as main

        return {getattr(route, "path", "") for route in main.app.routes}

    def test_downloads_and_covers_are_served_under_opds(self) -> None:
        routes = self._routes()
        self.assertIn("/opds/download/{reading_path_id}/{entry_id}", routes)
        self.assertIn("/opds/cover/{reading_path_id}/{entry_id}", routes)

    def test_feed_links_point_at_the_opds_surface(self) -> None:
        import backend.app.main as main

        download = main._opds_entry_download_href("https://example.test/panels", 71, 64804)
        cover = main._opds_entry_cover_href("https://example.test/panels", 71, 64804)
        self.assertEqual(download, "https://example.test/panels/opds/download/71/64804")
        self.assertEqual(cover, "https://example.test/panels/opds/cover/71/64804")
        # /api carries no WWW-Authenticate, so an acquisition link must never use it.
        self.assertNotIn("/api/", download)
        self.assertNotIn("/api/", cover)


if __name__ == "__main__":
    unittest.main()
