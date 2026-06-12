from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.models.user import User
from outreach.services.seed_user import get_or_create_default_user


async def get_current_user(session: AsyncSession = Depends(get_session)) -> User:
    return await get_or_create_default_user(session)
