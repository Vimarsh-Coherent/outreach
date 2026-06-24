from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.models.api_key import ApiKey
from outreach.models.user import User
from outreach.services import api_keys
from outreach.services.seed_user import get_or_create_default_user


async def get_current_user(session: AsyncSession = Depends(get_session)) -> User:
    return await get_or_create_default_user(session)


async def get_api_user(
    x_api_key: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Authenticate a public-API request via the X-Api-Key header."""
    if not x_api_key:
        raise HTTPException(401, "missing X-Api-Key header")
    key = await session.scalar(
        select(ApiKey).where(ApiKey.key_hash == api_keys.hash_key(x_api_key))
    )
    if key is None:
        raise HTTPException(401, "invalid API key")
    user = await session.get(User, key.user_id)
    if user is None:
        raise HTTPException(401, "API key has no owner")
    key.last_used_at = datetime.now(timezone.utc)
    await session.commit()
    return user
