"""HTTPS-only proxy to the separately deployed account and merchant service.

The desktop backend never receives database or merchant credentials. Its only
credential is the user's short-lived bearer session, sent to the configured
company API. A missing/unreachable API must never create a paid entitlement.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .account_service import AccountAlreadyExists, InvalidCredentials, InvalidSession


class AccountServiceUnavailable(Exception):
    pass


class PaymentUnavailable(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # A redirect to another host must not receive the user's bearer token.
        return None


class RemoteAccountService:
    def __init__(self, base_url: str):
        parsed = urlsplit(base_url.strip())
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError('云端账户地址必须是 HTTPS 域名根地址')
        self.base_url = base_url.strip().rstrip('/')
        self._opener = build_opener(_NoRedirect())

    def _request(self, path: str, *, method: str = 'GET', body: dict | None = None,
                 token: str | None = None, payment: bool = False) -> dict | list[dict]:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
        headers = {'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        if token:
            headers['Authorization'] = f'Bearer {token}'
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=10) as response:
                payload = response.read(256 * 1024)
                result = json.loads(payload)
                if not isinstance(result, dict) and not (path == '/api/membership/plans'
                    and isinstance(result, list) and all(isinstance(item, dict) for item in result)):
                    raise ValueError('服务器响应格式无效')
                return result
        except HTTPError as exc:
            if exc.code == 409 and path.endswith('/register'):
                raise AccountAlreadyExists from None
            if exc.code == 401:
                if path.endswith('/login'):
                    raise InvalidCredentials from None
                raise InvalidSession from None
            if payment:
                raise PaymentUnavailable('支付服务暂不可用，请稍后再试') from None
            raise AccountServiceUnavailable('云端账号服务暂不可用，请稍后再试') from None
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            if payment:
                raise PaymentUnavailable('无法连接支付服务，请稍后再试') from exc
            raise AccountServiceUnavailable('无法连接云端账号服务，请稍后再试') from exc

    def register(self, phone: str, password: str) -> dict:
        return self._request('/api/account/register', method='POST', body={'phone': phone, 'password': password})

    def login(self, phone: str, password: str) -> dict:
        return self._request('/api/account/login', method='POST', body={'phone': phone, 'password': password})

    def me(self, token: str) -> dict:
        return self._request('/api/account/me', token=token)

    def logout(self, token: str) -> None:
        self._request('/api/account/logout', method='POST', token=token)

    def is_member(self, token: str) -> bool:
        # Entitlement is verified online for every export. Never use a local
        # cached flag supplied by the renderer process.
        profile = self.me(token).get('user')
        if not isinstance(profile, dict) or profile.get('is_member') is not True:
            return False
        try:
            expires = datetime.fromisoformat(profile['member_until'])
            return expires.tzinfo is not None and expires > datetime.now(timezone.utc)
        except (TypeError, ValueError, KeyError):
            return False

    def list_plans(self) -> list[dict]:
        return self._request('/api/membership/plans', payment=True)

    def create_order(self, token: str, provider: str, plan_code: str) -> dict:
        return self._request('/api/membership/orders', method='POST',
                             body={'provider': provider, 'plan_code': plan_code},
                             token=token, payment=True)

    def get_order(self, token: str, order_id: str) -> dict:
        return self._request(f'/api/membership/orders/{order_id}', token=token, payment=True)
