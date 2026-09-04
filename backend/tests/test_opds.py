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


class DownloadClientContractTests(unittest.TestCase):
    """What a download manager needs before it will show progress or resume.

    Panels probed with HEAD, got 405 from a GET-only route, and sat at 0%.
    """

    def _download_routes(self):
        import backend.app.main as main

        # The same paths also carry a POST route for downloading into the local
        # library; only the fetching routes are part of this contract.
        return [
            route
            for route in main.app.routes
            if "GET" in (getattr(route, "methods", None) or set())
            and (
                getattr(route, "path", "").endswith("/download/{reading_path_id}/{entry_id}")
                or getattr(route, "path", "").endswith("/entries/{entry_id}/download")
            )
        ]

    def test_download_routes_answer_head(self) -> None:
        routes = self._download_routes()
        self.assertTrue(routes)
        for route in routes:
            self.assertIn("HEAD", route.methods, f"{route.path} does not answer HEAD")

    def test_head_does_not_pull_the_body_off_the_mirror(self) -> None:
        import backend.app.main as main

        closed = []

        class Chunks:
            def close(self) -> None:
                closed.append(True)

        download = main.EntryDownload(
            chunks=Chunks(), filename="a.cbz", media_type="application/vnd.comicbook+zip", size_bytes=5
        )
        main._close_download(download)
        self.assertEqual(closed, [True])

    def test_a_ranged_reply_from_the_mirror_is_relayed(self) -> None:
        import backend.app.main as main

        download = main.EntryDownload(
            chunks=[b"x"],
            filename="a.cbz",
            media_type="application/vnd.comicbook+zip",
            size_bytes=10,
            content_range="bytes 0-9/100",
            status_code=206,
        )
        self.assertEqual(download.status_code, 206)
        self.assertEqual(download.content_range, "bytes 0-9/100")


class RedirectInsteadOfProxyTests(unittest.TestCase):
    """Handing over the mirror URL keeps the host out of the transfer.

    Relaying a several-hundred-megabyte archive held a connection open on the
    host for minutes per file, and a few of those at once read as a connection
    flood to the host firewall, which banned the client mid-download.
    """

    def test_a_remote_entry_redirects_to_the_mirror(self) -> None:
        import backend.app.main as main

        with patch.object(main, "_entry_local_issue", return_value=None), patch.object(
            main, "_entry_resolved_getcomics_post_url", return_value="https://getcomics.test/post"
        ), patch.object(main.comics, "build_session") as build, patch.object(
            main.comics, "resolve_download_plan"
        ) as resolve:
            resolve.return_value = type("Plan", (), {"resolved_url": "https://mirror.test/a.cbz"})()
            url = main._entry_mirror_url(_stub_entry())

        self.assertEqual(url, "https://mirror.test/a.cbz")
        build.return_value.close.assert_called_once()

    def test_a_local_entry_has_no_mirror_to_redirect_to(self) -> None:
        import backend.app.main as main

        archive = object()
        with patch.object(main, "_entry_local_issue", return_value=object()), patch.object(
            main, "_issue_downloadable_archive", return_value=archive
        ):
            self.assertIsNone(main._entry_mirror_url(_stub_entry()))


def _stub_entry():
    class Entry:
        entry_type = "issue"
        canonical_issue = None
        issue = None

    return Entry()


class DeliveryModeTests(unittest.TestCase):
    """Which handover a reader supports can only be settled by trying it.

    The default relays the bytes, which is what Panels works with; redirect=1
    hands over the mirror URL. The flag has to reach acquisition links, not just
    the catalog URL.
    """

    def test_the_default_link_carries_only_the_token(self) -> None:
        import backend.app.main as main

        self.assertEqual(
            main._opds_link("https://example.test/panels/opds/lists", "tok"),
            "https://example.test/panels/opds/lists?key=tok",
        )

    def test_the_delivery_mode_travels_with_the_token(self) -> None:
        import backend.app.main as main

        self.assertEqual(
            main._opds_link("https://example.test/panels/opds/lists", "tok", True),
            "https://example.test/panels/opds/lists?key=tok&redirect=1",
        )

    def test_acquisition_links_inherit_the_mode(self) -> None:
        import backend.app.main as main

        plain = main._opds_entry_download_href("https://example.test/panels", 2, 64329, "tok", False)
        redirected = main._opds_entry_download_href("https://example.test/panels", 2, 64329, "tok", True)
        self.assertEqual(plain, "https://example.test/panels/opds/download/2/64329?key=tok")
        self.assertEqual(redirected, "https://example.test/panels/opds/download/2/64329?key=tok&redirect=1")

    def test_covers_never_carry_the_mode(self) -> None:
        import backend.app.main as main

        cover = main._opds_entry_cover_href("https://example.test/panels", 2, 64329, "tok")
        self.assertNotIn("redirect", cover)


if __name__ == "__main__":
    unittest.main()


class AccessTokenTests(unittest.TestCase):
    """A token in the URL avoids the 401 round trip that got the client banned.

    Basic auth makes a reader send an unauthenticated request first, so every
    catalog fetch and every download produced a 401. A burst of those reads as a
    brute-force attempt to the host firewall.
    """

    def setUp(self) -> None:
        self.env = {"APP_PASSWORD_HASH": hash_password("hunter2", iterations=1000), "APP_SESSION_SECRET": "secret-a"}

    def test_the_token_is_stable_for_a_given_session_secret(self) -> None:
        from backend.app.auth import opds_access_token

        with patch.dict(os.environ, self.env):
            first = opds_access_token()
            self.assertEqual(first, opds_access_token())

    def test_rotating_the_session_secret_rotates_the_token(self) -> None:
        from backend.app.auth import opds_access_token

        with patch.dict(os.environ, self.env):
            before = opds_access_token()
        with patch.dict(os.environ, {**self.env, "APP_SESSION_SECRET": "secret-b"}):
            self.assertNotEqual(before, opds_access_token())

    def test_only_the_real_token_is_accepted(self) -> None:
        from backend.app.auth import opds_access_token, verify_opds_token

        with patch.dict(os.environ, self.env):
            self.assertTrue(verify_opds_token(opds_access_token()))
            self.assertFalse(verify_opds_token("deadbeef"))
            self.assertFalse(verify_opds_token(None))
            self.assertFalse(verify_opds_token(""))

    def test_links_carry_the_token_forward(self) -> None:
        import backend.app.main as main

        plain = main._opds_link("https://example.test/panels/opds/lists", None)
        tokened = main._opds_link("https://example.test/panels/opds/lists", "abc123")
        existing_query = main._opds_link("https://example.test/panels/opds/x?a=1", "abc123")

        self.assertEqual(plain, "https://example.test/panels/opds/lists")
        self.assertEqual(tokened, "https://example.test/panels/opds/lists?key=abc123")
        self.assertEqual(existing_query, "https://example.test/panels/opds/x?a=1&key=abc123")
