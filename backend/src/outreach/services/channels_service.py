from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.models.channel import Channel
from outreach.schemas.channels import (
    ChannelOut,
    EmailChannelCreate,
    WhatsAppChannelCreate,
)
from outreach.schemas.extension import LinkedInChannelCreate, LinkedInChannelCreated
from outreach.utils.crypto import (
    decrypt_json,
    encrypt_json,
    hmac_token,
    mint_extension_token,
)


async def list_channels(session: AsyncSession, user_id: int) -> list[ChannelOut]:
    result = await session.execute(
        select(Channel).where(Channel.user_id == user_id).order_by(Channel.id.desc())
    )
    rows = result.scalars().all()
    out: list[ChannelOut] = []
    for c in rows:
        cfg: dict = {}
        try:
            cfg = decrypt_json(c.config_encrypted)
        except Exception:  # noqa: BLE001
            cfg = {}
        smtp = cfg.get("smtp") or {}
        imap = cfg.get("imap") or {}
        out.append(
            ChannelOut(
                id=c.id,
                channel_type=c.channel_type,
                display_label=c.display_label,
                status=c.status,
                daily_cap=c.daily_cap,
                sent_today=c.sent_today,
                created_at=c.created_at,
                updated_at=c.updated_at,
                smtp_host=smtp.get("host"),
                smtp_port=smtp.get("port"),
                smtp_from_email=smtp.get("from_email"),
                imap_host=imap.get("host") if imap else None,
                imap_port=imap.get("port") if imap else None,
            )
        )
    return out


async def create_email_channel(
    session: AsyncSession, user_id: int, dto: EmailChannelCreate
) -> ChannelOut:
    payload = {
        "smtp": dto.smtp.model_dump(),
        "imap": dto.imap.model_dump() if dto.imap else None,
    }
    channel = Channel(
        user_id=user_id,
        channel_type="email",
        display_label=dto.display_label,
        config_encrypted=encrypt_json(payload),
        status="active",
        daily_cap=dto.daily_cap,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    return (await list_channels(session, user_id))[0]


async def create_linkedin_channel(
    session: AsyncSession, user_id: int, dto: LinkedInChannelCreate,
) -> LinkedInChannelCreated:
    raw_token = mint_extension_token()
    payload = {"linkedin": {"token_hash": hmac_token(raw_token)}}
    channel = Channel(
        user_id=user_id,
        channel_type="linkedin",
        display_label=dto.display_label,
        config_encrypted=encrypt_json(payload),
        status="active",
        daily_cap=dto.daily_cap,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    return LinkedInChannelCreated(
        id=channel.id, display_label=channel.display_label,
        daily_cap=channel.daily_cap, raw_token=raw_token,
    )


async def get_whatsapp_channel(session: AsyncSession, user_id: int) -> Channel | None:
    return await session.scalar(
        select(Channel)
        .where(Channel.user_id == user_id, Channel.channel_type == "whatsapp")
        .order_by(Channel.id.desc())
        .limit(1)
    )


async def create_whatsapp_channel(
    session: AsyncSession, user_id: int, dto: WhatsAppChannelCreate
) -> ChannelOut:
    """Create (or return the existing) WhatsApp channel for this user.

    The real WhatsApp session lives in the Baileys sidecar; this Channel row is
    the platform-side handle used for daily-cap accounting + activation guards.
    Idempotent: one WhatsApp number per user, so a second call just relabels.
    """
    existing = await get_whatsapp_channel(session, user_id)
    if existing is not None:
        existing.display_label = dto.display_label
        existing.daily_cap = dto.daily_cap
        existing.status = "active"
        await session.commit()
        return next(c for c in await list_channels(session, user_id) if c.id == existing.id)

    channel = Channel(
        user_id=user_id,
        channel_type="whatsapp",
        display_label=dto.display_label,
        config_encrypted=encrypt_json({"whatsapp": {"session": "default"}}),
        status="active",
        daily_cap=dto.daily_cap,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    return next(c for c in await list_channels(session, user_id) if c.id == channel.id)


async def delete_channel(session: AsyncSession, user_id: int, channel_id: int) -> bool:
    result = await session.execute(
        select(Channel).where(Channel.id == channel_id, Channel.user_id == user_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        return False
    await session.delete(channel)
    await session.commit()
    return True
