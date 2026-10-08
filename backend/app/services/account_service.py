"""Local test-account storage behind a replaceable repository boundary.

This is deliberately a local SQLite implementation, not a claim that a
company database or a phone-verification service has been connected.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


PASSWORD_ITERATIONS = 600_000
SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60


class AccountAlreadyExists(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class InvalidSession(Exception):
    pass


def default_account_db_path() -> Path:
    """Keep the test database out of the source tree and install directory."""
    override = os.environ.get("VIDEOMATRIX_ACCOUNT_DB")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base or Path.home() / "AppData" / "Local") / "巨能跑pro版" / "accounts.sqlite3"
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base).expanduser() / "junengpao-pro" / "accounts.sqlite3" if base else Path.home() / ".local" / "share" / "junengpao-pro" / "accounts.sqlite3"


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 with a unique salt and versioned work factor."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS, dklen=32)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if not 100_000 <= iterations <= 10_000_000:
            return False
        salt, expected = _decode(salt_text), _decode(digest_text)
        if len(salt) < 16 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, UnicodeError, binascii.Error, OverflowError):
        return False


class AccountRepository(Protocol):
    """Swap this implementation for a company database adapter later."""

    def create_user(self, user_id: str, phone: str, password_hash: str, created_at: str) -> dict: ...

    def get_user_by_phone(self, phone: str) -> dict | None: ...

    def get_user_by_id(self, user_id: str) -> dict | None: ...

    def save_session(self, token_hash: str, user_id: str, created_at: int, expires_at: int) -> None: ...

    def get_session_user(self, token_hash: str, now: int) -> dict | None: ...

    def delete_session(self, token_hash: str) -> None: ...


class SQLiteAccountRepository:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_account_db_path()
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with closing(self._connect()) as connection, connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS account_users (
                        id TEXT PRIMARY KEY,
                        phone TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        phone_verified INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS account_sessions (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
                        created_at INTEGER NOT NULL,
                        expires_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_account_sessions_expiry
                    ON account_sessions(expires_at);
                """)
            if os.name != "nt":
                self.path.chmod(0o600)
            self._schema_ready = True

    @staticmethod
    def _user(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            "id": row["id"], "phone": row["phone"],
            "phone_verified": bool(row["phone_verified"]), "created_at": row["created_at"],
            "password_hash": row["password_hash"],
        }

    def create_user(self, user_id: str, phone: str, password_hash: str, created_at: str) -> dict:
        self._ensure_schema()
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT INTO account_users(id, phone, password_hash, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, phone, password_hash, created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise AccountAlreadyExists from exc
        return self.get_user_by_id(user_id)

    def get_user_by_phone(self, phone: str) -> dict | None:
        self._ensure_schema()
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM account_users WHERE phone = ?", (phone,)
            ).fetchone()
        return self._user(row)

    def get_user_by_id(self, user_id: str) -> dict | None:
        self._ensure_schema()
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM account_users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._user(row)

    def save_session(self, token_hash: str, user_id: str, created_at: int, expires_at: int) -> None:
        self._ensure_schema()
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM account_sessions WHERE expires_at <= ?", (created_at,))
            connection.execute(
                "INSERT INTO account_sessions(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash, user_id, created_at, expires_at),
            )

    def get_session_user(self, token_hash: str, now: int) -> dict | None:
        self._ensure_schema()
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """SELECT u.* FROM account_sessions AS s
                   JOIN account_users AS u ON u.id = s.user_id
                   WHERE s.token_hash = ? AND s.expires_at > ?""",
                (token_hash, now),
            ).fetchone()
        return self._user(row)

    def delete_session(self, token_hash: str) -> None:
        self._ensure_schema()
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM account_sessions WHERE token_hash = ?", (token_hash,))


class AccountService:
    def __init__(self, repository: AccountRepository):
        self.repository = repository

    @staticmethod
    def public_user(user: dict) -> dict:
        return {key: user[key] for key in ("id", "phone", "created_at", "phone_verified")}

    def _new_session(self, user: dict) -> dict:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        self.repository.save_session(hashlib.sha256(token.encode("ascii")).hexdigest(), user["id"], now,
                                     now + SESSION_LIFETIME_SECONDS)
        return {"token": token, "user": self.public_user(user)}

    def register(self, phone: str, password: str) -> dict:
        user_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        user = self.repository.create_user(user_id, phone, hash_password(password), created_at)
        return self._new_session(user)

    def login(self, phone: str, password: str) -> dict:
        user = self.repository.get_user_by_phone(phone)
        if user is None:
            # Spend the same kind of work as an existing account. The error
            # message also does not disclose whether the phone exists.
            hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"\0" * 16,
                                PASSWORD_ITERATIONS, dklen=32)
            raise InvalidCredentials
        if not verify_password(password, user["password_hash"]):
            raise InvalidCredentials
        return self._new_session(user)

    def me(self, token: str) -> dict:
        user = self.repository.get_session_user(hashlib.sha256(token.encode("utf-8")).hexdigest(), int(time.time()))
        if user is None:
            raise InvalidSession
        return {"user": self.public_user(user)}

    def logout(self, token: str) -> None:
        self.repository.delete_session(hashlib.sha256(token.encode("utf-8")).hexdigest())
