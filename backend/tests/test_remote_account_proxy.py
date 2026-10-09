"""Cloud account proxy stays HTTPS-only and never trusts renderer entitlements."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api.accounts import get_account_service
from app.main import app
from app.services.account_service import AccountService, SQLiteAccountRepository
from app.services.remote_account_service import RemoteAccountService


class RemoteAccountProxyTests(unittest.TestCase):
    def test_rejects_insecure_or_credential_bearing_endpoints(self):
        for url in ('http://example.com', 'https://user:pass@example.com',
                    'https://example.com/api', 'https://example.com/?token=secret'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                RemoteAccountService(url)
        self.assertEqual(RemoteAccountService('https://accounts.example.com').base_url,
                         'https://accounts.example.com')

    def test_paid_entitlement_requires_boolean_true_and_future_expiry(self):
        service = RemoteAccountService('https://accounts.example.com')
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        for is_member, expiry, expected in (
            (True, future, True), ('true', future, False),
            (True, past, False), (True, None, False), (False, future, False),
        ):
            with self.subTest(is_member=is_member, expiry=expiry):
                with patch.object(service, 'me', return_value={
                    'user': {'is_member': is_member, 'member_until': expiry},
                }):
                    self.assertIs(service.is_member('token'), expected)

    def test_local_test_account_cannot_start_payment(self):
        with tempfile.TemporaryDirectory() as directory:
            accounts = AccountService(SQLiteAccountRepository(Path(directory) / 'accounts.db'))
            app.dependency_overrides[get_account_service] = lambda: accounts
            try:
                client = TestClient(app)
                self.assertEqual(client.get('/api/account/mode').json(), {'mode': 'local_test'})
                self.assertEqual(client.get('/api/membership/plans').status_code, 503)
                session = client.post('/api/account/register', json={
                    'phone': '13800138099', 'password': 'test-password-123',
                }).json()
                reply = client.post('/api/membership/orders',
                    headers={'Authorization': f"Bearer {session['token']}"},
                    json={'provider': 'alipay', 'plan_code': 'month'})
                self.assertEqual(reply.status_code, 503)
                self.assertFalse(client.get('/api/account/me',
                    headers={'Authorization': f"Bearer {session['token']}"}).json()['user']['is_member'])
            finally:
                app.dependency_overrides.pop(get_account_service, None)

    def test_cloud_mode_is_reported_without_contacting_remote_service(self):
        app.dependency_overrides[get_account_service] = lambda: RemoteAccountService('https://accounts.example.com')
        try:
            with TestClient(app) as client:
                self.assertEqual(client.get('/api/account/mode').json(), {'mode': 'cloud'})
        finally:
            app.dependency_overrides.pop(get_account_service, None)


if __name__ == '__main__':
    unittest.main()
