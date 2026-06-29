"""Acts on a `ParsedInbound`:
  - reply       -> emit `reply` event, stop enrolment with `stopped_reply`
  - bounce      -> emit `bounce` event, stop enrolment with `stopped_bounce`,
                    add suppression on the recipient
  - auto_reply  -> emit `auto_reply` event, DO NOT stop
  - unrelated   -> noop

After a successful event insert we fire two background tasks:
  - sentiment.classify_and_store(event_id)  -> Claude Haiku classification + auto-suppress on unsubscribe
  - vault.index_lead(lead_id)               -> per-lead VectorVault append

Background tasks are fire-and-forget; failures are logged but never propagate.

Idempotent on inbound_message_id: re-running the same message is safe (the
events table's partial unique index handles it).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from outreach.services.time_util import utcnow

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import SessionLocal
from outreach.models.enrolment import Enrolment
from outreach.models.event import Event
from outreach.models.step_run import StepRun
from outreach.services.email_parse import ParsedInbound
from outreach.services.identity import canonical_identity
from outreach.services.suppressions_service import add_suppression

log = logging.getLogger("outreach.reply_processor")

_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """Spawn a fire-and-forget task and keep a reference so it isn't GC'd mid-run."""
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


async def _bg_classify(event_id: int) -> None:
    try:
        from outreach.services import sentiment
        async with SessionLocal() as s:
            await sentiment.classify_and_store(s, event_id)
    except Exception:  # noqa: BLE001
        log.exception("background sentiment failed for event %d", event_id)


async def _bg_index_vault(lead_id: int) -> None:
    try:
        from outreach.services import vault
        async with SessionLocal() as s:
            await vault.index_lead(s, lead_id)
    except Exception:  # noqa: BLE001
        log.exception("background vault index failed for lead %d", lead_id)


async def wait_for_background() -> None:
    """Test helper: drain pending background tasks."""
    if not _bg_tasks:
        return
    await asyncio.gather(*list(_bg_tasks), return_exceptions=True)


async def process(session: AsyncSession, parsed: ParsedInbound) -> dict:
    """Apply a parsed inbound message. Returns a summary dict for logging/tests."""
    if parsed.kind == "unrelated" or parsed.matched_step_run_id is None:
        return {"kind": parsed.kind, "action": "ignored"}

    run = await session.scalar(
        select(StepRun).where(StepRun.id == parsed.matched_step_run_id)
    )
    if run is None:
        return {"kind": parsed.kind, "action": "ignored", "reason": "step_run not found"}

    enrolment = await session.scalar(select(Enrolment).where(Enrolment.id == run.enrolment_id))
    if enrolment is None:
        return {"kind": parsed.kind, "action": "ignored", "reason": "enrolment not found"}

    # Stale-reply guard: if the inbound email's date predates when we sent the step,
    # it's an old thread the IMAP scanner pulled up — ignore it. Allow 1-hour
    # tolerance for sender-side clock skew (Date header set by recipient's client).
    if (
        parsed.kind in ("reply", "auto_reply")
        and run.sent_at is not None
        and parsed.received_at < run.sent_at - timedelta(hours=1)
    ):
        log.info(
            "skipping stale reply: received_at=%s < step_run %d sent_at=%s",
            parsed.received_at, run.id, run.sent_at,
        )
        return {"kind": parsed.kind, "action": "ignored", "reason": "stale_reply"}

    # Insert the event. The partial unique index on (enrolment_id, event_type, external_id)
    # WHERE external_id IS NOT NULL drops duplicates from re-fetched IMAP messages.
    event = Event(
        enrolment_id=enrolment.id,
        step_run_id=run.id,
        event_type=parsed.kind,
        channel=parsed.channel,
        external_id=parsed.inbound_message_id,
        payload={
            "from": parsed.from_addr,
            "subject": parsed.subject,
            "snippet": (parsed.body_snippet or "")[:500],
            "bounce_detail": parsed.bounce_detail,
        },
        occurred_at=parsed.received_at,
    )
    session.add(event)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return {"kind": parsed.kind, "action": "duplicate_event"}

    action = "logged"
    if parsed.kind == "reply":
        if enrolment.status == "active":
            # Branching: if this enrolment is waiting on a step that has a
            # 'replied' transition, route the reply through the branch (wake it)
            # instead of the default stop-on-reply.
            branch_from = (enrolment.runtime_state or {}).get("branch_from")
            has_reply_edge = False
            if branch_from is not None:
                from outreach.models.step import SequenceStep
                from outreach.services import branching
                fs = await session.get(SequenceStep, branch_from)
                if fs is not None:
                    has_reply_edge = any(
                        t.get("on") == "replied"
                        for t in branching.normalize_transitions(fs.transitions)
                    )
            if has_reply_edge:
                enrolment.next_send_at = utcnow()
                action = "branch_on_reply"
            else:
                enrolment.status = "stopped_reply"
                enrolment.stopped_at = utcnow()
                enrolment.stopped_reason = "lead replied"
                enrolment.next_send_at = None
                action = "stopped_reply"
    elif parsed.kind == "bounce":
        if enrolment.status == "active":
            enrolment.status = "stopped_bounce"
            enrolment.stopped_at = utcnow()
            enrolment.stopped_reason = parsed.bounce_detail or "hard bounce"
            enrolment.next_send_at = None
            action = "stopped_bounce"
        # Suppress this contact on the email channel so other sequences also stop.
        snapshot = enrolment.contact_snapshot or {}
        ident = canonical_identity(
            email=snapshot.get("email"),
            phone=snapshot.get("phone"),
            linkedin=snapshot.get("linkedin_url"),
        )
        if ident.hash is not None:
            await add_suppression(session, enrolment.user_id, ident.hash, "email", "bounce_hard")
    # auto_reply -> action stays "logged"; enrolment unchanged on purpose.

    await session.commit()
    log.info(
        "reply_processor: kind=%s action=%s enrol=%d run=%d ext_id=%s",
        parsed.kind, action, enrolment.id, run.id, parsed.inbound_message_id,
    )

    # Background: sentiment classification + vault index for this lead.
    _spawn(_bg_classify(event.id))
    _spawn(_bg_index_vault(enrolment.lead_id))

    return {
        "kind": parsed.kind,
        "action": action,
        "enrolment_id": enrolment.id,
        "step_run_id": run.id,
        "event_id": event.id,
    }
