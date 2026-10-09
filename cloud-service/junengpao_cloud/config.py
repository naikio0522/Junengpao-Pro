"""Configuration is read only on the cloud server, never bundled with Electron."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    amount_fen: int
    duration_days: int


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str = field(repr=False)
    db_ca_file: Path = Path()
    notify_base_url: str = ""
    alipay_app_id: str = ""
    wechat_mchid: str = ""
    plans: tuple[Plan, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        names = (
            "JNP_DB_HOST", "JNP_DB_NAME", "JNP_DB_USER", "JNP_DB_PASSWORD", "JNP_DB_CA_FILE",
        )
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise ConfigurationError("缺少云端数据库配置：" + ", ".join(missing))
        try:
            port = int(os.environ.get("JNP_DB_PORT", "3306"))
            raw_plans = json.loads(os.environ.get("JNP_MEMBERSHIP_PLANS_JSON", "[]"))
        except (ValueError, TypeError) as exc:
            raise ConfigurationError("端口或会员套餐配置无效") from exc
        if not 1 <= port <= 65535 or not isinstance(raw_plans, list):
            raise ConfigurationError("端口或会员套餐配置无效")
        plans = []
        for item in raw_plans:
            if not isinstance(item, dict):
                raise ConfigurationError("会员套餐格式无效")
            code, title = item.get("code"), item.get("title")
            amount, days = item.get("amount_fen"), item.get("duration_days")
            if (not isinstance(code, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}", code)
                    or not isinstance(title, str) or not 1 <= len(title) <= 80
                    or type(amount) is not int or not 1 <= amount <= 10_000_000
                    or type(days) is not int or not 1 <= days <= 3660):
                raise ConfigurationError("会员套餐字段无效")
            plans.append(Plan(code, title, amount, days))
        if len({plan.code for plan in plans}) != len(plans):
            raise ConfigurationError("会员套餐编号重复")
        ca_file = Path(os.environ["JNP_DB_CA_FILE"])
        if not ca_file.is_file():
            raise ConfigurationError("数据库 CA 证书不存在")
        return cls(
            db_host=os.environ["JNP_DB_HOST"], db_port=port,
            db_name=os.environ["JNP_DB_NAME"], db_user=os.environ["JNP_DB_USER"],
            db_password=os.environ["JNP_DB_PASSWORD"], db_ca_file=ca_file,
            notify_base_url=os.environ.get("JNP_PAYMENT_NOTIFY_BASE_URL", "").rstrip("/"),
            alipay_app_id=os.environ.get("JNP_ALIPAY_APP_ID", ""),
            wechat_mchid=os.environ.get("JNP_WECHAT_MCHID", ""), plans=tuple(plans),
        )

    def plan(self, code: str) -> Plan | None:
        return next((plan for plan in self.plans if plan.code == code), None)

    def merchant_id(self, provider: str) -> str:
        return self.alipay_app_id if provider == "alipay" else self.wechat_mchid if provider == "wechat" else ""

    def notification_url(self, provider: str) -> str:
        parsed = urlsplit(self.notify_base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise ConfigurationError("支付回调地址必须为公网 HTTPS URL")
        return f"{self.notify_base_url}/api/payment/callback/{provider}"
