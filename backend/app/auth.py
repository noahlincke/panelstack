from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


SESSION_COOKIE_NAME = "comic_library_session"
SESSION_TTL_DAYS = 365
PASSWORD_HASH_ENV = "APP_PASSWORD_HASH"
SESSION_SECRET_ENV = "APP_SESSION_SECRET"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def auth_enabled() -> bool:
    return bool(os.getenv(PASSWORD_HASH_ENV))


def session_secret() -> str:
    return os.getenv(SESSION_SECRET_ENV, "dev-session-secret-change-me")


def verify_password(password: str) -> bool:
    encoded_hash = os.getenv(PASSWORD_HASH_ENV)
    if not encoded_hash:
        return True
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded_hash.split("$", 3)
        iteration_count = int(iterations)
        salt = _b64decode(salt_b64)
        expected = _b64decode(digest_b64)
    except (ValueError, TypeError):
        return False

    if algorithm != "pbkdf2_sha256":
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iteration_count)
    return hmac.compare_digest(candidate, expected)


def hash_password(password: str, *, iterations: int = 390000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64encode(salt)}${_b64encode(digest)}"


@dataclass(frozen=True)
class SessionState:
    authenticated: bool
    expires_at: datetime | None = None


def create_session_cookie() -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=SESSION_TTL_DAYS)
    payload = {
        "authenticated": True,
        "issued_at": int(now.timestamp()),
        "expires_at": int(expires_at.timestamp()),
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_token = _b64encode(payload_json)
    signature = hmac.new(session_secret().encode("utf-8"), payload_token.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload_token}.{_b64encode(signature)}"


def verify_session_cookie(cookie_value: str | None) -> SessionState:
    if not cookie_value:
        return SessionState(authenticated=not auth_enabled(), expires_at=None)

    try:
        payload_token, signature_token = cookie_value.split(".", 1)
        signature = _b64decode(signature_token)
    except (ValueError, TypeError):
        return SessionState(authenticated=False, expires_at=None)

    expected_signature = hmac.new(
        session_secret().encode("utf-8"),
        payload_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected_signature):
        return SessionState(authenticated=False, expires_at=None)

    try:
        payload = json.loads(_b64decode(payload_token).decode("utf-8"))
        expires_at = datetime.fromtimestamp(int(payload["expires_at"]), tz=timezone.utc)
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return SessionState(authenticated=False, expires_at=None)

    if expires_at <= datetime.now(timezone.utc):
        return SessionState(authenticated=False, expires_at=expires_at)
    return SessionState(authenticated=bool(payload.get("authenticated")), expires_at=expires_at)
