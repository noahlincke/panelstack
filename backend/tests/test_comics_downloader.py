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

        def session_get(url: str, *, timeout: int, allow_redirects: bool, stream: bool = False) -> FakeResponse:
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


if __name__ == "__main__":
    unittest.main()
