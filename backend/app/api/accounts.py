"""Phone/password account endpoints backed by cloud or an explicit local test store."""

from __future__ import annotations

import re
import os
import sys
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from ..services.account_service import (
    AccountAlreadyExists, AccountService, InvalidCredentials, InvalidSession,
    SQLiteAccountRepository,
)
from ..services.remote_account_service import AccountServiceUnavailable, RemoteAccountService


router = APIRouter(prefix="/account", tags=["account"])
bearer = HTTPBearer(auto_error=False)
MAINLAND_MOBILE = re.compile(r"^1[3-9]\d{9}$")


class AccountCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str
    password: SecretStr

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        phone = value.strip()
        if phone.startswith("+86"):
            phone = phone[3:]
        elif phone.startswith("86") and len(phone) == 13:
            phone = phone[2:]
        if not MAINLAND_MOBILE.fullmatch(phone):
            raise ValueError("请输入正确的中国大陆手机号")
        return phone


class AccountUser(BaseModel):
    id: str
    phone: str
    created_at: str
    phone_verified: bool
    is_member: bool = False
    member_until: str | None = None


class SessionResponse(BaseModel):
    token: str
    user: AccountUser


class UserResponse(BaseModel):
    user: AccountUser


class AccountModeResponse(BaseModel):
    mode: Literal["local_test", "cloud", "cloud_unconfigured"]


class UnconfiguredAccountService:
    """Fail closed when a desktop release has no approved cloud endpoint."""

    @staticmethod
    def _unavailable():
        raise AccountServiceUnavailable('云端账号服务尚未配置，请更新安装包后重试')

    def register(self, phone: str, password: str):
        self._unavailable()

    def login(self, phone: str, password: str):
        self._unavailable()

    def me(self, token: str):
        self._unavailable()

    def logout(self, token: str):
        self._unavailable()

    def is_member(self, token: str) -> bool:
        return False


AccountProvider = AccountService | RemoteAccountService | UnconfiguredAccountService


@lru_cache(maxsize=1)
def get_account_service() -> AccountProvider:
    # A bundled backend is release code even when launched directly, without
    # Electron. Its account store cannot be switched to local via environment.
    mode = ('cloud' if getattr(sys, 'frozen', False)
            else os.environ.get('JNP_ACCOUNT_MODE', '').strip().lower())
    cloud_url = os.environ.get('JNP_ACCOUNT_API_URL', '').strip()
    if mode == 'local_test':
        return AccountService(SQLiteAccountRepository())
    if cloud_url:
        try:
            return RemoteAccountService(cloud_url)
        except ValueError:
            return UnconfiguredAccountService()
    if mode:
        return UnconfiguredAccountService()
    return AccountService(SQLiteAccountRepository())


def get_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if credentials is None or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", credentials.credentials):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return credentials.credentials


@router.get("/mode", response_model=AccountModeResponse)
def account_mode(accounts: AccountProvider = Depends(get_account_service)):
    mode = ('cloud' if isinstance(accounts, RemoteAccountService)
            else 'cloud_unconfigured' if isinstance(accounts, UnconfiguredAccountService)
            else 'local_test')
    return {"mode": mode}


@router.post("/register", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def register(payload: AccountCredentials, accounts: AccountProvider = Depends(get_account_service)):
    password = payload.password.get_secret_value()
    if not 8 <= len(password) <= 128 or not password.strip():
        raise HTTPException(status_code=400, detail="密码长度需为 8–128 位")
    try:
        return accounts.register(payload.phone, password)
    except AccountAlreadyExists:
        raise HTTPException(status_code=409, detail="无法完成注册，请尝试登录或更换手机号") from None
    except AccountServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/login", response_model=SessionResponse)
def login(payload: AccountCredentials, accounts: AccountProvider = Depends(get_account_service)):
    try:
        return accounts.login(payload.phone, payload.password.get_secret_value())
    except InvalidCredentials:
        raise HTTPException(status_code=401, detail="手机号或密码错误") from None
    except AccountServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/me", response_model=UserResponse)
def me(token: str = Depends(get_token), accounts: AccountProvider = Depends(get_account_service)):
    try:
        return accounts.me(token)
    except InvalidSession:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录") from None
    except AccountServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/logout")
def logout(token: str = Depends(get_token), accounts: AccountProvider = Depends(get_account_service)):
    try:
        accounts.me(token)
    except InvalidSession:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录") from None
    except AccountServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        accounts.logout(token)
    except AccountServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"message": "已退出登录"}
