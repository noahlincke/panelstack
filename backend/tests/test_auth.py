from __future__ import annotations

import os
import unittest

from backend.app import auth


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_password_hash = os.environ.get(auth.PASSWORD_HASH_ENV)
        self._original_session_secret = os.environ.get(auth.SESSION_SECRET_ENV)

    def tearDown(self) -> None:
        if self._original_password_hash is None:
            os.environ.pop(auth.PASSWORD_HASH_ENV, None)
        else:
            os.environ[auth.PASSWORD_HASH_ENV] = self._original_password_hash

        if self._original_session_secret is None:
            os.environ.pop(auth.SESSION_SECRET_ENV, None)
        else:
            os.environ[auth.SESSION_SECRET_ENV] = self._original_session_secret

    def test_hash_and_verify_password(self) -> None:
        os.environ[auth.PASSWORD_HASH_ENV] = auth.hash_password("test-password")
        self.assertTrue(auth.verify_password("test-password"))
        self.assertFalse(auth.verify_password("wrong-password"))

    def test_create_and_verify_session_cookie(self) -> None:
        os.environ[auth.SESSION_SECRET_ENV] = "test-session-secret"
        cookie = auth.create_session_cookie()
        state = auth.verify_session_cookie(cookie)
        self.assertTrue(state.authenticated)
        self.assertIsNotNone(state.expires_at)


if __name__ == "__main__":
    unittest.main()
