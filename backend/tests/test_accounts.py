import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.accounts import get_account_service, router
from app.services.account_service import AccountService, SQLiteAccountRepository


class LocalAccountApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "accounts.sqlite3"
        self.service = AccountService(SQLiteAccountRepository(self.db_path))
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api")
        self.app.dependency_overrides[get_account_service] = lambda: self.service
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        self.temp.cleanup()

    def test_register_login_me_logout_and_persist_without_plain_secrets(self):
        phone, password = "13800138000", "long-test-password-42"
        registration = self.client.post("/api/account/register", json={"phone": "+86" + phone, "password": password})
        self.assertEqual(registration.status_code, 201, registration.text)
        data = registration.json()
        self.assertEqual(data["user"]["phone"], phone)
        self.assertFalse(data["user"]["phone_verified"])
        self.assertIn("created_at", data["user"])
        self.assertNotIn("password_hash", data["user"])
        token = data["token"]
        self.assertGreaterEqual(len(token), 40)

        with closing(sqlite3.connect(self.db_path)) as connection:
            stored_password = connection.execute("SELECT password_hash FROM account_users").fetchone()[0]
            stored_token = connection.execute("SELECT token_hash FROM account_sessions").fetchone()[0]
        self.assertNotEqual(stored_password, password)
        self.assertTrue(stored_password.startswith("pbkdf2_sha256$"))
        self.assertNotEqual(stored_token, token)

        headers = {"Authorization": f"Bearer {token}"}
        profile = self.client.get("/api/account/me", headers=headers)
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()["user"]["id"], data["user"]["id"])

        self.assertEqual(self.client.post("/api/account/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/account/me", headers=headers).status_code, 401)

        # A new service instance must read the previous registration from disk.
        another = AccountService(SQLiteAccountRepository(self.db_path))
        self.app.dependency_overrides[get_account_service] = lambda: another
        login = self.client.post("/api/account/login", json={"phone": phone, "password": password})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertNotEqual(login.json()["token"], token)

    def test_duplicate_invalid_phone_and_generic_login_error(self):
        payload = {"phone": "13900139000", "password": "secure-test-password"}
        self.assertEqual(self.client.post("/api/account/register", json=payload).status_code, 201)
        duplicate = self.client.post("/api/account/register", json=payload)
        self.assertEqual(duplicate.status_code, 409)
        self.assertNotIn(payload["password"], duplicate.text)

        wrong = self.client.post("/api/account/login", json={**payload, "password": "wrong-password"})
        missing = self.client.post("/api/account/login", json={**payload, "phone": "13700137000"})
        self.assertEqual(wrong.status_code, missing.status_code)
        self.assertEqual(wrong.json()["detail"], missing.json()["detail"])
        self.assertEqual(self.client.get("/api/account/me").status_code, 401)

        invalid_phone = self.client.post("/api/account/register", json={**payload, "phone": "123"})
        self.assertEqual(invalid_phone.status_code, 422)
        self.assertFalse(self.db_path.read_bytes().find(b"secure-test-password") >= 0)
        short_password = self.client.post("/api/account/register", json={**payload, "phone": "13700137000", "password": "1234567"})
        self.assertEqual(short_password.status_code, 400)
        self.assertNotIn("1234567", short_password.text)


if __name__ == "__main__":
    unittest.main()
