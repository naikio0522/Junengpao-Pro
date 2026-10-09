from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from junengpao_cloud.accounts import AccountService, InvalidCredentials
from junengpao_cloud.api import create_app
from junengpao_cloud.config import Plan, Settings
from junengpao_cloud.mysql_repository import MySqlRepository
from junengpao_cloud.payments import InvalidPayment, PaymentService, PaymentUnavailable, VerifiedPayment
from junengpao_cloud.rate_limit import RateLimitExceeded, RateLimiter


class MemoryRepository:
    def __init__(self):
        self.users = {}
        self.by_phone = {}
        self.sessions = {}
        self.membership = {}
        self.orders = {}
        self.grants = 0
        self.rate_buckets = {}

    def create_user(self, user_id, phone, password_hash, created_at):
        user = dict(id=user_id, phone=phone, password_hash=password_hash,
                    phone_verified=False, created_at=created_at)
        self.users[user_id] = user
        self.by_phone[phone] = user
        return user

    def get_user_by_phone(self, phone):
        return self.by_phone.get(phone)

    def save_session(self, token_hash, user_id, created_at, expires_at):
        self.sessions[token_hash] = (user_id, expires_at)

    def get_session_user(self, token_hash, now):
        row = self.sessions.get(token_hash)
        return self.users[row[0]] if row and row[1] > now else None

    def delete_session(self, token_hash):
        self.sessions.pop(token_hash, None)

    def membership_until(self, user_id):
        return self.membership.get(user_id)

    def create_pending_order(self, order):
        self.orders[order['id']] = dict(order, status='pending')

    def get_order_for_user(self, order_id, user_id):
        order = self.orders.get(order_id)
        return order if order and order['user_id'] == user_id else None

    def settle_verified_payment(self, provider, notice):
        order = self.orders.get(notice.order_id)
        if (not order or order['provider'] != provider or order['merchant_id'] != notice.merchant_id
                or order['amount_fen'] != notice.amount_fen):
            raise InvalidPayment
        if order['status'] == 'paid':
            if order['provider_trade_no'] != notice.provider_trade_no:
                raise InvalidPayment
            return self.membership[order['user_id']]
        if order['status'] != 'pending':
            raise InvalidPayment
        order['status'] = 'paid'
        order['provider_trade_no'] = notice.provider_trade_no
        expires = max(self.membership.get(order['user_id'], 0), int(time.time())) + order['duration_days'] * 86400
        self.membership[order['user_id']] = expires
        self.grants += 1
        return expires

    def consume_rate_limits(self, buckets, now):
        staged = dict(self.rate_buckets)
        for digest, maximum, window_seconds in buckets:
            started, attempts = staged.get(digest, (now, 0))
            if started <= now - window_seconds:
                started, attempts = now, 0
            if attempts >= maximum:
                raise RateLimitExceeded
            staged[digest] = (started, attempts + 1)
        self.rate_buckets = staged


class FakeVerifiedGateway:
    def __init__(self):
        self.notification = None

    def create_order(self, **kwargs):
        return 'https://pay.example.invalid/qr/' + kwargs['order_id']

    def verify_notification(self, raw_body, headers):
        if raw_body != b'provider-verified-fixture':
            raise InvalidPayment('invalid signature')
        return self.notification


class CloudServiceTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.settings = Settings(
            db_host='example.invalid', db_port=3306, db_name='junengpao', db_user='test',
            db_password='unused', db_ca_file=Path(__file__),
            notify_base_url='https://api.example.invalid',
            alipay_app_id='app-test', wechat_mchid='mch-test',
            plans=(Plan('month', '月会员', 1900, 30),),
        )
        self.accounts = AccountService(self.repo)
        self.session = self.accounts.register('13800138000', 'secret-pass-123')
        self.user_id = self.session['user']['id']

    def test_account_roundtrip_and_no_implicit_membership(self):
        self.assertFalse(self.session['user']['is_member'])
        self.assertEqual(self.accounts.login('+8613800138000', 'secret-pass-123')['user']['id'], self.user_id)
        with self.assertRaises(InvalidCredentials):
            self.accounts.login('13800138000', 'wrong-password')

    def test_missing_gateway_fails_closed_without_creating_order(self):
        service = PaymentService(self.settings, self.repo, {})
        with self.assertRaises(PaymentUnavailable):
            service.open_order(self.user_id, 'alipay', 'month')
        self.assertEqual(self.repo.orders, {})
        with self.assertRaises(PaymentUnavailable):
            service.process_notification('wechat', b'forged', {})
        self.assertEqual(self.repo.grants, 0)

    def test_verified_callback_checks_merchant_amount_and_is_idempotent(self):
        gateway = FakeVerifiedGateway()
        service = PaymentService(self.settings, self.repo, {'alipay': gateway})
        order = service.open_order(self.user_id, 'alipay', 'month')
        self.assertEqual(self.repo.grants, 0)
        gateway.notification = VerifiedPayment(order['order_id'], 'trade-1', 1900, 'wrong-app', 'SUCCESS')
        with self.assertRaises(InvalidPayment):
            service.process_notification('alipay', b'provider-verified-fixture', {})
        gateway.notification = VerifiedPayment(order['order_id'], 'trade-1', 1800, 'app-test', 'SUCCESS')
        with self.assertRaises(InvalidPayment):
            service.process_notification('alipay', b'provider-verified-fixture', {})
        gateway.notification = VerifiedPayment(order['order_id'], 'trade-1', 1900, 'app-test', 'SUCCESS')
        first = service.process_notification('alipay', b'provider-verified-fixture', {})
        repeated = service.process_notification('alipay', b'provider-verified-fixture', {})
        self.assertEqual(first, repeated)
        self.assertEqual(self.repo.grants, 1)
        self.assertTrue(self.accounts.me(self.session['token'])['user']['is_member'])

    def test_api_recharge_is_unavailable_by_default(self):
        client = TestClient(create_app(settings=self.settings, repository=self.repo))
        headers = {'Authorization': 'Bearer ' + self.session['token']}
        response = client.post('/api/membership/orders', json={'provider': 'wechat', 'plan_code': 'month'},
                               headers=headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(client.post('/api/payment/callback/alipay', content=b'forged').status_code, 503)
        self.assertFalse(client.get('/api/account/me', headers=headers).json()['user']['is_member'])
        self.assertEqual(client.get('/api/membership/status', headers=headers).json(),
                         {'is_member': False, 'member_until': None})

    def test_order_status_requires_owner_token(self):
        gateway = FakeVerifiedGateway()
        service = PaymentService(self.settings, self.repo, {'alipay': gateway})
        created = service.open_order(self.user_id, 'alipay', 'month')
        other = self.accounts.register('13900139000', 'other-pass-123')
        client = TestClient(create_app(settings=self.settings, repository=self.repo,
                                       gateways={'alipay': gateway}))
        mine = client.get('/api/membership/orders/' + created['order_id'],
                          headers={'Authorization': 'Bearer ' + self.session['token']})
        theirs = client.get('/api/membership/orders/' + created['order_id'],
                            headers={'Authorization': 'Bearer ' + other['token']})
        self.assertEqual(mine.status_code, 200)
        self.assertEqual(theirs.status_code, 404)

    def test_account_only_mode_blocks_orders_even_with_gateway(self):
        settings = Settings(**{**self.settings.__dict__, 'account_only': True})
        gateway = FakeVerifiedGateway()
        client = TestClient(create_app(settings=settings, repository=self.repo,
                                       gateways={'alipay': gateway}))
        headers = {'Authorization': 'Bearer ' + self.session['token']}
        self.assertEqual(client.get('/api/membership/plans').json(), [])
        self.assertEqual(client.post('/api/membership/orders', headers=headers,
                           json={'provider': 'alipay', 'plan_code': 'month'}).status_code, 503)
        self.assertEqual(client.post('/api/payment/callback/alipay', content=b'provider-verified-fixture').status_code, 503)
        self.assertEqual(client.get('/api/membership/orders/' + 'a' * 32,
                         headers=headers).status_code, 503)
        self.assertEqual(self.repo.orders, {})

    def test_existing_two_table_database_is_ready_in_account_only_mode(self):
        settings = Settings(**{**self.settings.__dict__, 'account_only': True})
        executed = []

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(self, sql): executed.append(sql)

        class Connection:
            def cursor(self): return Cursor()
            def close(self): pass

        repo = MySqlRepository(settings)
        with patch.object(repo, '_connect', return_value=Connection()) as connect:
            self.assertTrue(repo.ready())
            self.assertIsNone(repo.membership_until('some-user'))
            connect.assert_called_once()
        self.assertEqual(executed, [
            'SELECT 1 FROM account_users LIMIT 1',
            'SELECT 1 FROM account_sessions LIMIT 1',
        ])

    def test_mysql_driver_must_require_tls_before_connecting(self):
        class Connection:
            _ssl_required = False
            connected = False
            closed = False
            def connect(self): self.connected = True
            def close(self): self.closed = True

        connection = Connection()
        with patch('junengpao_cloud.mysql_repository.ssl.create_default_context'), \
                patch('junengpao_cloud.mysql_repository.pymysql.connect', return_value=connection):
            with self.assertRaisesRegex(RuntimeError, 'TLS'):
                MySqlRepository(self.settings)._connect()
        self.assertFalse(connection.connected)
        self.assertTrue(connection.closed)

    def test_public_registration_requires_persistent_rate_limit_configuration(self):
        client = TestClient(create_app(settings=self.settings, repository=self.repo))
        response = client.post('/api/account/register', json={
            'phone': '13700137000', 'password': 'some-password-123',
        })
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('13700137000', self.repo.by_phone)

    def test_public_rate_limit_uses_hashed_ip_and_phone_and_returns_429(self):
        settings = Settings(**{**self.settings.__dict__,
                               'public_accounts': True, 'account_only': True,
                               'rate_limit_hmac_key': 'x' * 32})
        client = TestClient(create_app(settings=settings, repository=self.repo))
        for _ in range(10):
            response = client.post('/api/account/login', json={
                'phone': '+8613800138099', 'password': 'incorrect-password',
            })
            self.assertEqual(response.status_code, 401)
        blocked = client.post('/api/account/login', json={
            'phone': '13800138099', 'password': 'incorrect-password',
        })
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(len(self.repo.rate_buckets), 2)
        self.assertTrue(all(len(digest) == 64 for digest in self.repo.rate_buckets))
        self.assertNotIn('13800138099', str(self.repo.rate_buckets))
        self.assertNotIn('testclient', str(self.repo.rate_buckets))

    def test_rate_limit_window_recovers_and_rejects_bad_secret(self):
        limiter = RateLimiter(self.repo, 'y' * 32)
        with patch('junengpao_cloud.rate_limit.time.time', return_value=1000):
            for _ in range(5):
                limiter.consume('register', '127.0.0.1', '13800138099')
            with self.assertRaises(RateLimitExceeded):
                limiter.consume('register', '127.0.0.1', '13800138099')
        with patch('junengpao_cloud.rate_limit.time.time', return_value=4601):
            limiter.consume('register', '127.0.0.1', '13800138099')
        with self.assertRaises(ValueError):
            RateLimiter(self.repo, 'short')


if __name__ == '__main__':
    unittest.main()
