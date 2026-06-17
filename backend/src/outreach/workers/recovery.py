"""Recovery sweeper — reclaim step_runs stuck in 'reserving' beyond TTL.

A run becomes orphaned if the worker crashed between Phase 1 (insert reserving)
and Phase 3 (finalise). Without this, the enrolment stalls forever on next_send_at=NULL.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from outreach.config import get_settings
from outreach.db import SessionLocal

log = logging.getLogger("outreach.recovery")

_FIND_SQL = text(
    """
    SELECT id, enrolment_id
      FROM outreach.step_runs
     WHERE status = 'reserving'
       AND created_at < NOW() - (:ttl_seconds * INTERVAL '1 second')
     ORDER BY created_at
     LIMIT 500
       FOR UPDATE SKIP LOCKED
    """
)


async def sweep() -> dict:
    settings = get_settings()
    swept = 0
    async with SessionLocal() as session:
        rows = (await session.execute(
            _FIND_SQL, {"ttl_seconds": settings.recovery_reservation_ttl_seconds}
        )).all()
        for run_id, enrol_id in rows:
            # Mark the run failed, reschedule the enrolment for 1 min from now.
            await session.execute(
                text(
                    "UPDATE outreach.step_runs SET status='failed', "
                    "error_message='recovered from stuck reserving', "
                    "updated_at=NOW() WHERE id=:id"
                ),
                {"id": run_id},
            )
            await session.execute(
                text(
                    "UPDATE outreach.enrolments "
                    "SET next_send_at = NOW() + INTERVAL '1 minute', updated_at=NOW() "
                    "WHERE id=:id AND status='active' AND next_send_at IS NULL"
                ),
                {"id": enrol_id},
            )
            swept += 1
        await session.commit()
    if swept:
        log.warning("recovery: swept %d stuck runs", swept)
    return {"swept": swept, "at": datetime.now(timezone.utc).isoformat()}
