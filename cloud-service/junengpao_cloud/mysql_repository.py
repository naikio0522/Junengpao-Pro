"""MySQL repository used by the server process only."""

from __future__ import annotations

import ssl
import time
from contextlib import closing

import pymysql
from pymysql.cursors import DictCursor

from .accounts import AccountAlreadyExists
from .config import Settings
from .payments import InvalidPayment, VerifiedPayment


class MySqlRepository:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _connect(self):
        context = ssl.create_default_context(cafile=str(self.settings.db_ca_file))
        # Alibaba's current CA chain trips Python 3.13's extra strict X.509
        # extension check. Keep CA validation and hostname checking enabled.
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        return pymysql.connect(
            host=self.settings.db_host, port=self.settings.db_port,
            user=self.settings.db_user, password=self.settings.db_password,
            database=self.settings.db_name, charset="utf8mb4", cursorclass=DictCursor,
            ssl=context, connect_timeout=5, read_timeout=15, write_timeout=15,
            autocommit=False,
        )

    def ready(self) -> bool:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM account_users LIMIT 1")
                cursor.execute("SELECT 1 FROM account_sessions LIMIT 1")
                cursor.execute("SELECT 1 FROM account_memberships LIMIT 1")
                cursor.execute("SELECT 1 FROM payment_orders LIMIT 1")
        return True

    def create_user(self, user_id: str, phone: str, password_hash: str, created_at: str) -> dict:
        try:
            with closing(self._connect()) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO account_users(id, phone, password_hash, created_at) VALUES (%s, %s, %s, %s)",
                        (user_id, phone, password_hash, created_at),
                    )
                connection.commit()
        except pymysql.IntegrityError as exc:
            if exc.args and exc.args[0] == 1062:
                raise AccountAlreadyExists from exc
            raise
        user = self.get_user_by_id(user_id)
        if user is None:
            raise RuntimeError("新建账户后无法读回")
        return user

    def get_user_by_phone(self, phone: str) -> dict | None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM account_users WHERE phone = %s", (phone,))
                return cursor.fetchone()

    def get_user_by_id(self, user_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM account_users WHERE id = %s", (user_id,))
                return cursor.fetchone()

    def save_session(self, token_hash: str, user_id: str, created_at: int, expires_at: int) -> None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM account_sessions WHERE expires_at <= %s", (created_at,))
                cursor.execute(
                    "INSERT INTO account_sessions(token_hash, user_id, created_at, expires_at) "
                    "VALUES (%s, %s, %s, %s)",
                    (token_hash, user_id, created_at, expires_at),
                )
            connection.commit()

    def get_session_user(self, token_hash: str, now: int) -> dict | None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT u.* FROM account_sessions s JOIN account_users u ON u.id = s.user_id "
                    "WHERE s.token_hash = %s AND s.expires_at > %s", (token_hash, now),
                )
                return cursor.fetchone()

    def delete_session(self, token_hash: str) -> None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM account_sessions WHERE token_hash = %s", (token_hash,))
            connection.commit()

    def membership_until(self, user_id: str) -> int | None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT valid_until_epoch FROM account_memberships WHERE user_id = %s", (user_id,))
                row = cursor.fetchone()
        return int(row["valid_until_epoch"]) if row else None

    def create_pending_order(self, order: dict) -> None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO payment_orders(id, user_id, provider, plan_code, amount_fen, "
                    "duration_days, merchant_id, status, created_at_epoch) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)",
                    (order["id"], order["user_id"], order["provider"], order["plan_code"],
                     order["amount_fen"], order["duration_days"], order["merchant_id"],
                     order["created_at_epoch"]),
                )
            connection.commit()

    def get_order_for_user(self, order_id: str, user_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id AS order_id, provider, plan_code, amount_fen, status, created_at_epoch, "
                    "paid_at_epoch FROM payment_orders WHERE id = %s AND user_id = %s",
                    (order_id, user_id),
                )
                return cursor.fetchone()

    def settle_verified_payment(self, provider: str, notice: VerifiedPayment) -> int:
        """Atomic, idempotent grant. Never call on unverified request data."""
        with closing(self._connect()) as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT * FROM payment_orders WHERE id = %s FOR UPDATE", (notice.order_id,))
                    order = cursor.fetchone()
                    if (order is None or order["provider"] != provider
                            or order["merchant_id"] != notice.merchant_id
                            or int(order["amount_fen"]) != notice.amount_fen):
                        raise InvalidPayment("通知与原订单不一致")
                    if order["status"] == "paid":
                        if order["provider_trade_no"] != notice.provider_trade_no:
                            raise InvalidPayment("订单已由另一笔交易结算")
                        cursor.execute(
                            "SELECT valid_until_epoch FROM account_memberships WHERE user_id = %s",
                            (order["user_id"],),
                        )
                        member = cursor.fetchone()
                        if member is None:
                            raise InvalidPayment("已付款订单缺少会员记录，需人工核查")
                        connection.commit()
                        return int(member["valid_until_epoch"])
                    if order["status"] != "pending":
                        raise InvalidPayment("订单状态不可结算")

                    # Serialize two different successful orders for one user.
                    cursor.execute("SELECT id FROM account_users WHERE id = %s FOR UPDATE", (order["user_id"],))
                    if cursor.fetchone() is None:
                        raise InvalidPayment("订单账户不存在")
                    cursor.execute(
                        "SELECT valid_until_epoch FROM account_memberships WHERE user_id = %s FOR UPDATE",
                        (order["user_id"],),
                    )
                    current = cursor.fetchone()
                    now = int(time.time())
                    expires = max(int(current["valid_until_epoch"]) if current else 0, now) + int(order["duration_days"]) * 86400
                    cursor.execute(
                        "UPDATE payment_orders SET status = 'paid', provider_trade_no = %s, "
                        "paid_at_epoch = %s WHERE id = %s AND status = 'pending'",
                        (notice.provider_trade_no, now, order["id"]),
                    )
                    if cursor.rowcount != 1:
                        raise InvalidPayment("订单已被并发结算")
                    cursor.execute(
                        "INSERT INTO account_memberships(user_id, valid_until_epoch, updated_at_epoch) "
                        "VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE "
                        "valid_until_epoch = VALUES(valid_until_epoch), updated_at_epoch = VALUES(updated_at_epoch)",
                        (order["user_id"], expires, now),
                    )
                connection.commit()
                return expires
            except pymysql.IntegrityError as exc:
                connection.rollback()
                raise InvalidPayment("交易号已用于其他订单") from exc
            except Exception:
                connection.rollback()
                raise
