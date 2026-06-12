from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.config import get_settings
from outreach.models.user import User


async def get_or_create_default_user(session: AsyncSession) -> User:
    settings = get_settings()
    result = await session.execute(select(User).order_by(User.id.asc()).limit(1))
    user = result.scalar_one_or_none()
    if user is not None:
        return user
    user = User(email=settings.seed_user_email, display_name=settings.seed_user_name)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
