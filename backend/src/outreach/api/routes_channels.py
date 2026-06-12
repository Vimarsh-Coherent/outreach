from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.user import User
from outreach.schemas.channels import (
    EMAIL_PRESETS,
    ChannelOut,
    EmailChannelCreate,
    EmailPreset,
    TestEmailChannelRequest,
    TestEmailChannelResponse,
)
from outreach.schemas.extension import LinkedInChannelCreate, LinkedInChannelCreated
from outreach.services import channels_service
from outreach.services.email_test import test_email_channel

router = APIRouter(prefix="/api/channels", tags=["channels"])


@router.get("/email/presets", response_model=list[EmailPreset])
async def get_email_presets() -> list[EmailPreset]:
    return EMAIL_PRESETS


@router.post("/email/test", response_model=TestEmailChannelResponse)
async def test_email(req: TestEmailChannelRequest) -> TestEmailChannelResponse:
    return await test_email_channel(req)


@router.get("", response_model=list[ChannelOut])
async def list_channels(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ChannelOut]:
    return await channels_service.list_channels(session, user.id)


@router.post("/email", response_model=ChannelOut, status_code=201)
async def create_email_channel(
    dto: EmailChannelCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ChannelOut:
    return await channels_service.create_email_channel(session, user.id, dto)


@router.post("/linkedin", response_model=LinkedInChannelCreated, status_code=201)
async def create_linkedin_channel(
    dto: LinkedInChannelCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LinkedInChannelCreated:
    """Create a LinkedIn channel and mint a one-time extension token.

    The raw token is returned ONLY in this response — only its HMAC hash is
    stored at rest. The user pastes channel_id + raw_token into the extension
    popup. Save it somewhere; you cannot retrieve it again.
    """
    return await channels_service.create_linkedin_channel(session, user.id, dto)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    ok = await channels_service.delete_channel(session, user.id, channel_id)
    if not ok:
        raise HTTPException(404, "channel not found")
