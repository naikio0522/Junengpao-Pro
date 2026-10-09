"""Payment contract: only a verified provider notification can grant membership.

There are deliberately no production Alipay/WeChat adapters in this package:
the merchant's API credentials, callback protocol and order product are not
available yet. The default registry is empty and every payment route fails
closed until an audited adapter is installed server-side.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Mapping, Protocol

from .config import ConfigurationError, Settings


class PaymentUnavailable(Exception):
    pass


class InvalidPayment(Exception):
    pass


@dataclass(frozen=True)
class VerifiedPayment:
    """An adapter may return this only after authenticating a provider notice."""

    order_id: str
    provider_trade_no: str
    amount_fen: int
    merchant_id: str
    status: str


class PaymentGateway(Protocol):
    def create_order(self, *, order_id: str, amount_fen: int, title: str,
                     notification_url: str) -> str:
        """Return a provider-issued pay URL / QR payload."""

    def verify_notification(self, raw_body: bytes, headers: Mapping[str, str]) -> VerifiedPayment:
        """Verify signature (and decrypt WeChat API v3 resource) before returning."""


class PaymentRepository(Protocol):
    def create_pending_order(self, order: dict) -> None: ...
    def get_order_for_user(self, order_id: str, user_id: str) -> dict | None: ...
    def settle_verified_payment(self, provider: str, notice: VerifiedPayment) -> int: ...


class PaymentService:
    def __init__(self, settings: Settings, repository: PaymentRepository,
                 gateways: Mapping[str, PaymentGateway]):
        self.settings = settings
        self.repository = repository
        self.gateways = gateways

    def _gateway(self, provider: str) -> PaymentGateway:
        if provider not in {"alipay", "wechat"}:
            raise InvalidPayment("不支持的支付渠道")
        gateway = self.gateways.get(provider)
        if gateway is None:
            raise PaymentUnavailable("支付通道尚未完成商户配置与验签联调")
        return gateway

    def open_order(self, user_id: str, provider: str, plan_code: str) -> dict:
        gateway = self._gateway(provider)
        plan = self.settings.plan(plan_code)
        merchant_id = self.settings.merchant_id(provider)
        if plan is None or not merchant_id:
            raise PaymentUnavailable("会员套餐或商户号尚未配置")
        try:
            notify_url = self.settings.notification_url(provider)
        except ConfigurationError as exc:
            raise PaymentUnavailable(str(exc)) from exc
        order_id = uuid.uuid4().hex
        self.repository.create_pending_order({
            "id": order_id, "user_id": user_id, "provider": provider,
            "plan_code": plan.code, "amount_fen": plan.amount_fen,
            "duration_days": plan.duration_days, "merchant_id": merchant_id,
            "created_at_epoch": int(time.time()),
        })
        pay_url = gateway.create_order(order_id=order_id, amount_fen=plan.amount_fen,
                                       title=plan.title, notification_url=notify_url)
        if not pay_url:
            raise PaymentUnavailable("支付通道未返回有效的付款地址")
        return {"order_id": order_id, "provider": provider, "amount_fen": plan.amount_fen,
                "plan_code": plan.code, "status": "pending", "pay_url": pay_url}

    def order_for_user(self, order_id: str, user_id: str) -> dict | None:
        return self.repository.get_order_for_user(order_id, user_id)

    def process_notification(self, provider: str, raw_body: bytes,
                             headers: Mapping[str, str]) -> int:
        gateway = self._gateway(provider)
        # This call must perform the provider's cryptographic verification.
        notice = gateway.verify_notification(raw_body, headers)
        if (notice.status != "SUCCESS" or not notice.order_id or not notice.provider_trade_no
                or type(notice.amount_fen) is not int or notice.amount_fen <= 0
                or notice.merchant_id != self.settings.merchant_id(provider)):
            raise InvalidPayment("支付通知与商户配置不一致")
        # The repository then checks the stored amount, merchant, provider and
        # pending state inside a transaction before extending membership.
        return self.repository.settle_verified_payment(provider, notice)
