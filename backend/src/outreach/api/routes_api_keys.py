"""Manage public-API keys (raw key shown once at creation)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.api_key import ApiKey
from outreach.models.user import User
from outreach.services import api_keys

router = APIRouter(prefix="/api/keys", tags=["api-keys"])


class ApiKeyCreate(BaseModel):
    label: str = Field(default="", max_length=120)


class ApiKeyCreated(BaseModel):
    id: int
    label: str
    prefix: str
    raw_key: str  # shown ONCE


class ApiKeyOut(BaseModel):
    id: int
    label: str
    prefix: str
    last_used_at: datetime | None
    created_at: datetime


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(
    user: User = Depends(get_current_user), session=Depends(get_session),
) -> list[ApiKeyOut]:
    rows = (await session.execute(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.id.desc())
    )).scalars().all()
    return [ApiKeyOut(id=k.id, label=k.label, prefix=k.prefix,
                      last_used_at=k.last_used_at, created_at=k.created_at) for k in rows]


@router.post("", response_model=ApiKeyCreated, status_code=201)
async def create_key(
    dto: ApiKeyCreate,
    user: User = Depends(get_current_user), session=Depends(get_session),
) -> ApiKeyCreated:
    raw, key_hash, prefix = api_keys.generate_key()
    k = ApiKey(user_id=user.id, key_hash=key_hash, prefix=prefix, label=dto.label)
    session.add(k)
    await session.commit()
    await session.refresh(k)
    return ApiKeyCreated(id=k.id, label=k.label, prefix=k.prefix, raw_key=raw)


@router.delete("/{key_id}", status_code=204)
async def delete_key(
    key_id: int,
    user: User = Depends(get_current_user), session=Depends(get_session),
) -> None:
    k = await session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id)
    )
    if k is None:
        raise HTTPException(404, "key not found")
    await session.delete(k)
    await session.commit()
