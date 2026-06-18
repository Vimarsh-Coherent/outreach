"""Inbound WhatsApp webhook — the Baileys sidecar POSTs every incoming message
here. We match the sender's phone to an enrolment that received a WhatsApp step,
then route it through the same reply_processor + sentiment pipeline as email:
emits a `reply` Event (channel=whatsapp), stops the enrolment (stopped_reply),
and classifies sentiment. Idempotent on the WhatsApp message id.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.config import get_settings
from outreach.db import SessionLocal
from outreach.models.enrolment import Enrolment
from outreach.models.step import SequenceStep
from outreach.models.step_run import StepRun
from outreach.services import reply_processor
from outreach.services.email_parse import ParsedInbound
from outreach.services.identity import normalize_phone

log = logging.getLogger("outreach.whatsapp")

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


async def _match_step_run(session: AsyncSession, sender_e164: str) -> int | None:
    """Find the most recently sent WhatsApp step_run whose enrolment's contact
    phone normalizes to the same E.164 as the sender."""
    rows = (await session.execute(
        select(Enrolment.contact_snapshot, StepRun.id)
        .join(StepRun, StepRun.enrolment_id == Enrolment.id)
        .join(SequenceStep, SequenceStep.id == StepRun.step_id)
        .where(SequenceStep.channel == "whatsapp", StepRun.status == "sent")
        .order_by(StepRun.sent_at.desc())
        .limit(500)
    )).all()
    for snapshot, run_id in rows:
        phone = normalize_phone((snapshot or {}).get("phone"))
        if phone and phone == sender_e164:
            return run_id
    return None


@router.post("/inbound")
async def whatsapp_inbound(
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> dict:
    settings = get_settings()
    # Shared-secret check (the sidecar injects X-Api-Key). Skip only if unset.
    if settings.wa_api_key and x_api_key != settings.wa_api_key:
        raise HTTPException(401, "bad api key")

    payload = await request.json()
    sender_raw = str(payload.get("from") or "")
    text = str(payload.get("text") or "").strip()
    msg_id = payload.get("id")
    if not sender_raw or not text:
        return {"ok": True, "matched": False, "reason": "empty"}

    sender_e164 = normalize_phone(sender_raw)
    if not sender_e164:
        return {"ok": True, "matched": False, "reason": "unparseable_sender"}

    async with SessionLocal() as session:
        run_id = await _match_step_run(session, sender_e164)
        if run_id is None:
            log.info("whatsapp inbound from %s — no matching enrolment", sender_e164)
            return {"ok": True, "matched": False, "reason": "no_enrolment"}

        parsed = ParsedInbound(
            kind="reply",
            matched_step_run_id=run_id,
            inbound_message_id=str(msg_id) if msg_id else None,
            from_addr=sender_e164,
            body_snippet=text[:500],
            channel="whatsapp",
        )
        result = await reply_processor.process(session, parsed)

    return {"ok": True, "matched": True, "action": result.get("action")}
