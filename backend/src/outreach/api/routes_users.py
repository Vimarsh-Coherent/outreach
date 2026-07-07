from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.user import User
from outreach.schemas.user import UserProfileOut, UserProfileUpdate

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/profile", response_model=UserProfileOut)
async def get_profile(user: User = Depends(get_current_user)) -> UserProfileOut:
    return UserProfileOut.model_validate(user, from_attributes=True)


@router.patch("/profile", response_model=UserProfileOut)
async def update_profile(
    dto: UserProfileUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserProfileOut:
    for field, value in dto.model_dump(exclude_unset=True).items():
        setattr(user, field, (value or None) if isinstance(value, str) else value)
    await session.commit()
    await session.refresh(user)
    return UserProfileOut.model_validate(user, from_attributes=True)
