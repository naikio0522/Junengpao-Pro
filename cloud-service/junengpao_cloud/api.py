"""Cloud API. No database credentials or merchant keys belong in the desktop app."""

from __future__ import annotations

import re
from typing import Mapping

import pymysql
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, SecretStr

from .accounts import (
    AccountAlreadyExists, AccountService, InvalidCredentials, InvalidSession,
)
from .config import ConfigurationError, Settings
from .mysql_repository import MySqlRepository
from .payments import InvalidPayment, PaymentGateway, PaymentService, PaymentUnavailable


bearer = HTTPBearer(auto_error=False)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str
    password: SecretStr


class NewOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    plan_code: str


def create_app(*, settings: Settings | None = None, repository=None,
               gateways: Mapping[str, PaymentGateway] | None = None) -> FastAPI:
    app = FastAPI(title="巨能跑 Pro 云端账号服务", version="0.1.0")
    installed_gateways = dict(gateways or {})

    def runtime():
        try:
            config = settings if settings is not None else Settings.from_env()
        except ConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        store = repository if repository is not None else MySqlRepository(config)
        return config, store, AccountService(store), PaymentService(config, store, installed_gateways)

    def token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
        if credentials is None or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", credentials.credentials):
            raise HTTPException(status_code=401, detail="请先登录")
        return credentials.credentials

    def current_user(session_token: str = Depends(token)) -> dict:
        _, _, accounts, _ = runtime()
        try:
            return accounts.me(session_token)["user"]
        except InvalidSession:
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录") from None

    @app.exception_handler(pymysql.MySQLError)
    async def database_error(_request: Request, _exc: pymysql.MySQLError):
        # Do not reveal database endpoints, SQL or user records to clients.
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"detail": "账号服务暂时不可用"})

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/ready")
    def ready():
        _, store, _, _ = runtime()
        try:
            store.ready()
        except (pymysql.MySQLError, OSError) as exc:
            raise HTTPException(status_code=503, detail="数据库或迁移尚未就绪") from exc
        return {"ok": True}

    @app.post("/api/account/register", status_code=status.HTTP_201_CREATED)
    def register(payload: Credentials):
        _, _, accounts, _ = runtime()
        try:
            return accounts.register(payload.phone, payload.password.get_secret_value())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AccountAlreadyExists:
            raise HTTPException(status_code=409, detail="无法完成注册，请尝试登录或更换手机号") from None

    @app.post("/api/account/login")
    def login(payload: Credentials):
        _, _, accounts, _ = runtime()
        try:
            return accounts.login(payload.phone, payload.password.get_secret_value())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except InvalidCredentials:
            raise HTTPException(status_code=401, detail="手机号或密码错误") from None

    @app.get("/api/account/me")
    def me(user: dict = Depends(current_user)):
        return {"user": user}

    @app.post("/api/account/logout")
    def logout(session_token: str = Depends(token)):
        _, _, accounts, _ = runtime()
        try:
            accounts.me(session_token)
        except InvalidSession:
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录") from None
        accounts.logout(session_token)
        return {"message": "已退出登录"}

    @app.get("/api/membership/plans")
    def plans():
        config, _, _, _ = runtime()
        return [{"code": p.code, "title": p.title, "amount_fen": p.amount_fen,
                 "duration_days": p.duration_days} for p in config.plans]

    @app.get("/api/membership/status")
    def membership_status(user: dict = Depends(current_user)):
        return {"is_member": user["is_member"], "member_until": user["member_until"]}

    @app.post("/api/membership/orders")
    def open_order(payload: NewOrder, user: dict = Depends(current_user)):
        _, _, _, payments = runtime()
        try:
            return payments.open_order(user["id"], payload.provider, payload.plan_code)
        except PaymentUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except InvalidPayment as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/membership/orders/{order_id}")
    def order(order_id: str, user: dict = Depends(current_user)):
        if not re.fullmatch(r"[0-9a-f]{32}", order_id):
            raise HTTPException(status_code=404, detail="订单不存在")
        _, _, _, payments = runtime()
        result = payments.order_for_user(order_id, user["id"])
        if result is None:
            raise HTTPException(status_code=404, detail="订单不存在")
        return result

    @app.post("/api/payment/callback/{provider}")
    async def payment_callback(provider: str, request: Request):
        _, _, _, payments = runtime()
        if provider not in {"alipay", "wechat"}:
            raise HTTPException(status_code=404, detail="支付渠道不存在")
        raw_body = await request.body()
        if not raw_body or len(raw_body) > 128 * 1024:
            raise HTTPException(status_code=400, detail="通知格式无效")
        try:
            payments.process_notification(provider, raw_body, request.headers)
        except PaymentUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except InvalidPayment:
            raise HTTPException(status_code=400, detail="支付通知验证失败") from None
        if provider == "alipay":
            return PlainTextResponse("success")
        return {"code": "SUCCESS", "message": "成功"}

    return app


app = create_app()
