"""Desktop proxy for a separately deployed, verified merchant service.

This module cannot grant membership. Only the cloud payment callback, after
provider signature and order checks, may do that. Without a configured HTTPS
account service, all billing endpoints are unavailable.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .accounts import get_account_service, get_token
from ..services.account_service import AccountService, InvalidSession
from ..services.remote_account_service import (
    AccountServiceUnavailable, PaymentUnavailable, RemoteAccountService,
)


router = APIRouter(prefix='/membership', tags=['membership'])


class NewOrder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['alipay', 'wechat']
    plan_code: str = Field(min_length=1, max_length=32, pattern=r'^[a-z0-9_-]+$')


def _cloud(accounts: AccountService | RemoteAccountService) -> RemoteAccountService:
    if not isinstance(accounts, RemoteAccountService):
        raise HTTPException(status_code=503, detail='会员支付尚未开放：云端账号和商户服务未接通')
    return accounts


@router.get('/plans')
def list_plans(accounts: AccountService | RemoteAccountService = Depends(get_account_service)):
    try:
        return _cloud(accounts).list_plans()
    except PaymentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post('/orders')
def create_order(payload: NewOrder, token: str = Depends(get_token),
                 accounts: AccountService | RemoteAccountService = Depends(get_account_service)):
    try:
        return _cloud(accounts).create_order(token, payload.provider, payload.plan_code)
    except InvalidSession:
        raise HTTPException(status_code=401, detail='登录已失效，请重新登录') from None
    except (PaymentUnavailable, AccountServiceUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get('/orders/{order_id}')
def get_order(order_id: str, token: str = Depends(get_token),
              accounts: AccountService | RemoteAccountService = Depends(get_account_service)):
    if not re.fullmatch(r'[0-9a-f]{32}', order_id):
        raise HTTPException(status_code=404, detail='订单不存在')
    try:
        return _cloud(accounts).get_order(token, order_id)
    except InvalidSession:
        raise HTTPException(status_code=401, detail='登录已失效，请重新登录') from None
    except (PaymentUnavailable, AccountServiceUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
