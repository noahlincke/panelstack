from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import requests

import comics


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        status_code: int = 200,
        text: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def close(self) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class ComicsDownloaderTests(unittest.TestCase):
    def test_resolve_download_plan_follows_getcomics_redirect_wrappers(self) -> None:
        source_url = "https://getcomics.org/dc/absolute-batman-17-2026/"
        redirect_url = "https://getcomics.org/dls/pixeldrain-token"
        page_html = f"""
        <html>
          <head>
            <title>Absolute Batman #17 (2026) - GetComics</title>
          </head>
          <body>
            <a href="https://getcomics.org/how-to-download/">How To Download</a>
            <a href="{redirect_url}">PIXELDRAIN</a>
          </body>
        </html>
        """

        session = MagicMock()

        def session_get(
            url: str,
            *,
            timeout: int,
            allow_redirects: bool,
            stream: bool = False,
            headers: dict[str, str] | None = None,
        ) -> FakeResponse:
            # A ranged probe confirms the mirror actually serves the file.
            if headers and "Range" in headers:
                self.assertEqual(url, "https://pixeldrain.com/api/file/ZdxbahwL")
                return FakeResponse(url=url, status_code=206)
            self.assertEqual(timeout, 60)
            self.assertFalse(stream)
            if url == source_url:
                self.assertTrue(allow_redirects)
                return FakeResponse(url=source_url, text=page_html)
            if url == redirect_url:
                self.assertFalse(allow_redirects)
                return FakeResponse(
                    url=redirect_url,
                    status_code=302,
                    headers={"location": "https://pixeldrain.com/u/ZdxbahwL"},
                )
            raise AssertionError(f"Unexpected URL fetched: {url}")

        session.get.side_effect = session_get

        plan = comics.resolve_download_plan(source_url, session, preferred_host=None)

        self.assertEqual(plan.post_title, "Absolute Batman #17 (2026)")
        self.assertEqual(plan.selected_link.url, redirect_url)
        self.assertEqual(plan.resolved_url, "https://pixeldrain.com/api/file/ZdxbahwL")

    def test_a_mirror_that_refuses_the_file_falls_through_to_the_next(self) -> None:
        candidates = [
            comics.DownloadCandidate(label="comicfiles", url="https://fs2.comicfiles.ru/a.cbz"),
            comics.DownloadCandidate(label="pixeldrain", url="https://pixeldrain.com/u/ok"),
        ]
        session = MagicMock()

        def session_get(url: str, **kwargs) -> FakeResponse:
            forbidden = "comicfiles" in url
            return FakeResponse(url=url, status_code=403 if forbidden else 206)

        session.get.side_effect = session_get
        selected, resolved = comics.resolve_first_supported(session, candidates)

        self.assertEqual(selected.label, "pixeldrain")
        self.assertEqual(resolved, "https://pixeldrain.com/api/file/ok")

    def test_all_mirrors_refusing_still_returns_a_resolvable_url(self) -> None:
        candidates = [comics.DownloadCandidate(label="comicfiles", url="https://fs2.comicfiles.ru/a.cbz")]
        session = MagicMock()
        session.get.side_effect = lambda url, **kwargs: FakeResponse(url=url, status_code=403)

        selected, resolved = comics.resolve_first_supported(session, candidates)

        self.assertEqual(resolved, "https://fs2.comicfiles.ru/a.cbz")


if __name__ == "__main__":
    unittest.main()


class ArchiveResponseTests(unittest.TestCase):
    """A mirror answering with a page is not a mirror serving the file.

    datanodes.to returns 200 text/html for a .cbz request. That page used to be
    streamed through as the comic, giving a 51 KB ".cbz" of gzipped HTML that no
    reader could open, and no mirror after it was ever tried.
    """

    def _response(self, content_type: str, status_code: int = 200) -> FakeResponse:
        return FakeResponse(url="https://mirror.test/x.cbz", status_code=status_code, headers={"content-type": content_type})

    def test_an_archive_content_type_is_accepted(self) -> None:
        for content_type in (
            "application/octet-stream",
            "application/vnd.comicbook+zip",
            "application/zip",
            "application/x-rar-compressed",
        ):
            self.assertTrue(comics.serves_an_archive(self._response(content_type)), content_type)

    def test_an_html_page_is_refused_even_with_a_200(self) -> None:
        self.assertFalse(comics.serves_an_archive(self._response("text/html; charset=UTF-8")))
        self.assertFalse(comics.serves_an_archive(self._response("application/xhtml+xml")))
        self.assertFalse(comics.serves_an_archive(self._response("text/plain")))

    def test_an_error_status_is_refused_whatever_the_type(self) -> None:
        self.assertFalse(comics.serves_an_archive(self._response("application/octet-stream", status_code=403)))

    def test_a_response_with_no_content_type_is_given_the_benefit_of_the_doubt(self) -> None:
        self.assertTrue(comics.serves_an_archive(FakeResponse(url="https://mirror.test/x.cbz", headers={})))


class MirrorFallthroughTests(unittest.TestCase):
    def _session(self, by_url: dict[str, FakeResponse]) -> MagicMock:
        session = MagicMock()
        session.get.side_effect = lambda url, **kwargs: by_url[url]
        return session

    def test_a_page_serving_mirror_is_skipped_for_one_that_serves_bytes(self) -> None:
        page = "https://datanodes.to/a/x.cbz"
        real = "https://pixeldrain.com/api/file/abc"
        session = self._session(
            {
                page: FakeResponse(url=page, headers={"content-type": "text/html; charset=UTF-8"}),
                real: FakeResponse(url=real, status_code=206, headers={"content-type": "application/vnd.comicbook+zip"}),
            }
        )
        self.assertFalse(comics.mirror_serves_file(session, page))
        self.assertTrue(comics.mirror_serves_file(session, real))

    def test_a_mirror_that_raises_is_not_treated_as_working(self) -> None:
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("boom")
        self.assertFalse(comics.mirror_serves_file(session, "https://mirror.test/x.cbz"))
