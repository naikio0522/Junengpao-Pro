"""Phone/password account endpoints for the local test database."""

from __future__ import annotations

import re
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator

from ..services.account_service import (
    AccountAlreadyExists, AccountService, InvalidCredentials, InvalidSession,
    SQLiteAccountRepository,
)


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


class SessionResponse(BaseModel):
    token: str
    user: AccountUser


class UserResponse(BaseModel):
    user: AccountUser


@lru_cache(maxsize=1)
def get_account_service() -> AccountService:
    return AccountService(SQLiteAccountRepository())


def get_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if credentials is None or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", credentials.credentials):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return credentials.credentials


@router.post("/register", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def register(payload: AccountCredentials, accounts: AccountService = Depends(get_account_service)):
    password = payload.password.get_secret_value()
    if not 8 <= len(password) <= 128 or not password.strip():
        raise HTTPException(status_code=400, detail="密码长度需为 8–128 位")
    try:
        return accounts.register(payload.phone, password)
    except AccountAlreadyExists:
        raise HTTPException(status_code=409, detail="无法完成注册，请尝试登录或更换手机号") from None


@router.post("/login", response_model=SessionResponse)
def login(payload: AccountCredentials, accounts: AccountService = Depends(get_account_service)):
    try:
        return accounts.login(payload.phone, payload.password.get_secret_value())
    except InvalidCredentials:
        raise HTTPException(status_code=401, detail="手机号或密码错误") from None


@router.get("/me", response_model=UserResponse)
def me(token: str = Depends(get_token), accounts: AccountService = Depends(get_account_service)):
    try:
        return accounts.me(token)
    except InvalidSession:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录") from None


@router.post("/logout")
def logout(token: str = Depends(get_token), accounts: AccountService = Depends(get_account_service)):
    try:
        accounts.me(token)
    except InvalidSession:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录") from None
    accounts.logout(token)
    return {"message": "已退出登录"}
