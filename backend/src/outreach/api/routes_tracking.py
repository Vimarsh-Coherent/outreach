"""Public open/click tracking endpoints (no auth — hit by recipients' mail
clients). Each records an `open`/`click` Event for the step_run, deduped to the
first per step_run via the events partial-unique index.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from outreach.db import SessionLocal
from outreach.models.event import Event
from outreach.models.step_run import StepRun
from outreach.services import email_tracking

log = logging.getLogger("outreach.tracking")

router = APIRouter(prefix="/t", tags=["tracking"])

_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


async def _record(step_run_id: int, event_type: str, url: str | None) -> None:
    """Insert the open/click event, deduped to the first per step_run."""
    async with SessionLocal() as session:
        run = await session.scalar(select(StepRun).where(StepRun.id == step_run_id))
        if run is None:
            return
        session.add(Event(
            enrolment_id=run.enrolment_id,
            step_run_id=run.id,
            event_type=event_type,
            channel="email",
            external_id=f"{event_type}:{step_run_id}",  # first-per-step_run dedup
            payload={"url": url} if url else {},
            occurred_at=datetime.now(timezone.utc),
        ))
        try:
            await session.flush()
            # Branching: if this enrolment is in a branch-wait, wake it so the
            # open/click condition is evaluated promptly (not just at deadline).
            await session.execute(text(
                "UPDATE outreach.enrolments SET next_send_at = NOW() "
                "WHERE id = :eid AND status = 'active' AND runtime_state ? 'branch_from'"
            ), {"eid": run.enrolment_id})
            await session.commit()
        except IntegrityError:
            await session.rollback()  # already recorded — fine


@router.get("/o/{token}")
async def track_open(token: str) -> Response:
    step_run_id = email_tracking.parse_token(token, "o")
    if step_run_id is not None:
        try:
            await _record(step_run_id, "open", None)
        except Exception:  # noqa: BLE001 — never let tracking break the pixel
            log.exception("failed to record open for step_run %s", step_run_id)
    return Response(content=email_tracking.pixel_gif(), media_type="image/gif", headers=_NO_CACHE)


@router.get("/c/{token}")
async def track_click(token: str, u: str = "") -> Response:
    step_run_id = email_tracking.parse_token(token, "c")
    target: str | None = None
    if u:
        try:
            target = email_tracking.b64url_decode(u)
        except Exception:  # noqa: BLE001
            target = None
    if step_run_id is not None:
        try:
            await _record(step_run_id, "click", target)
        except Exception:  # noqa: BLE001
            log.exception("failed to record click for step_run %s", step_run_id)
    if target and target.startswith(("http://", "https://")):
        return RedirectResponse(target, status_code=302, headers=_NO_CACHE)
    return Response(status_code=204, headers=_NO_CACHE)
