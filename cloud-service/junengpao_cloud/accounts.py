"""Phone/password accounts compatible with the desktop test-account format."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Protocol


PASSWORD_ITERATIONS = 600_000
SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60
MOBILE = re.compile(r"^1[3-9]\d{9}$")


class AccountAlreadyExists(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class InvalidSession(Exception):
    pass


class AccountRepository(Protocol):
    def create_user(self, user_id: str, phone: str, password_hash: str, created_at: str) -> dict: ...
    def get_user_by_phone(self, phone: str) -> dict | None: ...
    def save_session(self, token_hash: str, user_id: str, created_at: int, expires_at: int) -> None: ...
    def get_session_user(self, token_hash: str, now: int) -> dict | None: ...
    def delete_session(self, token_hash: str) -> None: ...
    def membership_until(self, user_id: str) -> int | None: ...


def normalize_phone(value: str) -> str:
    phone = value.strip()
    if phone.startswith("+86"):
        phone = phone[3:]
    elif phone.startswith("86") and len(phone) == 13:
        phone = phone[2:]
    if not MOBILE.fullmatch(phone):
        raise ValueError("请输入正确的中国大陆手机号")
    return phone


def validate_password(password: str) -> None:
    if not 8 <= len(password) <= 128 or not password.strip():
        raise ValueError("密码长度需为 8–128 位")


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS, dklen=32)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$")
        iterations = int(iterations_text)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= iterations <= 10_000_000:
            return False
        salt, expected = _decode(salt_text), _decode(digest_text)
        if len(salt) < 16 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (ValueError, UnicodeError, binascii.Error, OverflowError):
        return False


class AccountService:
    def __init__(self, repository: AccountRepository):
        self.repository = repository

    def _profile(self, user: dict) -> dict:
        expiry = self.repository.membership_until(user["id"])
        return {
            "id": user["id"], "phone": user["phone"], "created_at": user["created_at"],
            "phone_verified": bool(user["phone_verified"]),
            "is_member": bool(expiry and expiry > int(time.time())),
            "member_until": datetime.fromtimestamp(expiry, timezone.utc).isoformat() if expiry else None,
        }

    def _new_session(self, user: dict) -> dict:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        self.repository.save_session(hashlib.sha256(token.encode("ascii")).hexdigest(), user["id"], now,
                                     now + SESSION_LIFETIME_SECONDS)
        return {"token": token, "user": self._profile(user)}

    def register(self, phone: str, password: str) -> dict:
        phone = normalize_phone(phone)
        validate_password(password)
        user = self.repository.create_user(str(uuid.uuid4()), phone, hash_password(password),
                                           datetime.now(timezone.utc).isoformat(timespec="seconds"))
        return self._new_session(user)

    def login(self, phone: str, password: str) -> dict:
        phone = normalize_phone(phone)
        user = self.repository.get_user_by_phone(phone)
        if user is None:
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
        return {"user": self._profile(user)}

    def logout(self, token: str) -> None:
        self.repository.delete_session(hashlib.sha256(token.encode("utf-8")).hexdigest())
