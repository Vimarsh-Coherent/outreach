from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Atomic CAS that bumps sent_today if the channel still has capacity OR rolls the
# window when 24h has elapsed. Returns NULL when cap is hit and the window
# hasn't rolled (caller reschedules).
#
# Port of PLAN.md §4.2 (bumpChannelSentToday).
_CAP_BUMP_SQL = text(
    """
    UPDATE outreach.channels
       SET sent_today_window_start = CASE
             WHEN sent_today_window_start IS NULL
                  OR NOW() >= sent_today_window_start + INTERVAL '1 day'
               THEN NOW()
             ELSE sent_today_window_start
           END,
           sent_today = CASE
             WHEN sent_today_window_start IS NULL
                  OR NOW() >= sent_today_window_start + INTERVAL '1 day'
               THEN 1
             ELSE sent_today + 1
           END,
           updated_at = NOW()
     WHERE id = :channel_id
       AND status = 'active'
       AND (
             sent_today_window_start IS NULL
             OR sent_today < daily_cap
             OR NOW() >= sent_today_window_start + INTERVAL '1 day'
           )
     RETURNING sent_today;
    """
)


async def bump_or_reject(session: AsyncSession, channel_id: int) -> int | None:
    """Atomically reserve one send slot. Returns new sent_today on success, None on cap."""
    r = await session.execute(_CAP_BUMP_SQL, {"channel_id": channel_id})
    row = r.first()
    return None if row is None else int(row[0])


async def refund_one(session: AsyncSession, channel_id: int) -> None:
    """Roll back one slot if a send failed mid-flight. Best-effort, never raises."""
    await session.execute(
        text(
            "UPDATE outreach.channels SET sent_today = GREATEST(0, sent_today - 1) "
            "WHERE id = :cid"
        ),
        {"cid": channel_id},
    )
