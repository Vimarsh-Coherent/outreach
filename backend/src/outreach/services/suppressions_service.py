from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.models.suppression import Suppression


async def is_suppressed(
    session: AsyncSession, user_id: int, identity_hash: bytes, channel: str
) -> bool:
    r = await session.scalar(
        select(Suppression.id).where(
            Suppression.user_id == user_id,
            Suppression.identity_hash == identity_hash,
            Suppression.channel.in_((channel, "*")),
        ).limit(1)
    )
    return r is not None


async def add_suppression(
    session: AsyncSession, user_id: int, identity_hash: bytes, channel: str, reason: str
) -> None:
    sup = Suppression(
        user_id=user_id, identity_hash=identity_hash, channel=channel, reason=reason
    )
    session.add(sup)
    try:
        await session.flush()
    except Exception:  # noqa: BLE001 -- unique-violation tolerated (already suppressed)
        await session.rollback()
