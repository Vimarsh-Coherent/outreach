"""Tick + send pipeline.

claim_due()          claims N enrolments whose next_send_at <= NOW(), marks them
                      with status='reserving' on a fresh step_run row, returns IDs.

process_one(run_id)  loads the run, decrypts SMTP creds, renders the template,
                      sends via aiosmtplib, marks the run sent/failed, advances
                      the enrolment, computes the next next_send_at.

tick()               periodic cron entry. Claims + processes in parallel up to
                      SEND_CONCURRENCY.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.channels.email_channel import send_email
from outreach.config import get_settings
from outreach.db import SessionLocal
from outreach.models.channel import Channel
from outreach.models.enrolment import Enrolment
from outreach.models.event import Event
from outreach.models.lead import Lead
from outreach.models.sequence import Sequence
from outreach.models.step import SequenceStep
from outreach.models.step_run import StepRun
from outreach.models.user import User
from outreach.schemas.channels import SMTPConfig
from outreach.services.cap_check import bump_or_reject, refund_one
from outreach.services.jitter import add_jitter
from outreach.services.send_window import next_valid_slot
from outreach.services.suppressions_service import is_suppressed
from outreach.services.template_render import render
from outreach.services.threading_email import make_message_id
from outreach.utils.crypto import decrypt_json

log = logging.getLogger("outreach.workers")

# ----------- claim phase -----------

# Picks `LIMIT` enrolments due to send NOW. Uses SKIP LOCKED so multiple workers
# (or the recovery sweep firing concurrently) never collide.
_CLAIM_SQL = text(
    """
    SELECT e.id, e.sequence_id, e.user_id, e.lead_id, e.current_step_order
      FROM outreach.enrolments e
      JOIN outreach.sequences s ON s.id = e.sequence_id
     WHERE e.status = 'active'
       AND e.next_send_at IS NOT NULL
       AND e.next_send_at <= NOW()
       AND s.status = 'active'
     ORDER BY e.next_send_at
     LIMIT :limit
       FOR UPDATE OF e SKIP LOCKED
    """
)


# Reclaim li_commands the extension claimed but never reported back on, so the
# next /next-command poll retries them. Recovers from a dispatch that bailed
# (navigation), a closed tab, or a killed service worker — within one tick
# instead of the hourly watchdog sweep.
_RECLAIM_CMD_SQL = text(
    """
    UPDATE outreach.li_commands
       SET status = 'pending', claimed_at = NULL
     WHERE status = 'claimed'
       AND claimed_at < NOW() - (:mins * INTERVAL '1 minute')
    RETURNING id
    """
)


async def reclaim_stale_li_commands(session: AsyncSession, minutes: int) -> list[int]:
    r = await session.execute(_RECLAIM_CMD_SQL, {"mins": minutes})
    ids = [row[0] for row in r.all()]
    if ids:
        log.info("reclaimed %d stale claimed li_commands -> pending: %s", len(ids), ids)
    return ids


async def claim_due(session: AsyncSession, limit: int = 500) -> list[dict]:
    """Phase 1: lock + clear next_send_at + insert reserving step_run.

    Returns a list of `{enrolment_id, step_run_id, step_id, channel}` dicts that
    the send phase will consume.
    """
    r = await session.execute(_CLAIM_SQL, {"limit": limit})
    claimed_rows = r.all()
    if not claimed_rows:
        return []

    claims: list[dict] = []
    now = datetime.now(timezone.utc)
    for enrol_id, sequence_id, _user_id, _lead_id, current_step_order in claimed_rows:
        # Identify the next step to send.
        step = await session.scalar(
            select(SequenceStep)
            .where(
                SequenceStep.sequence_id == sequence_id,
                SequenceStep.step_order > current_step_order,
            )
            .order_by(SequenceStep.step_order.asc())
            .limit(1)
        )
        if step is None:
            # Sequence completed.
            await session.execute(
                text(
                    "UPDATE outreach.enrolments SET status='done', next_send_at=NULL, "
                    "stopped_at=NOW(), stopped_reason='completed', updated_at=NOW() "
                    "WHERE id=:id"
                ),
                {"id": enrol_id},
            )
            continue

        run = StepRun(
            enrolment_id=enrol_id,
            step_id=step.id,
            channel=step.channel,
            status="reserving",
            scheduled_at=now,
        )
        session.add(run)
        await session.flush()
        # Clear next_send_at so we don't double-claim before the send finishes.
        await session.execute(
            text(
                "UPDATE outreach.enrolments SET next_send_at=NULL, updated_at=NOW() "
                "WHERE id=:id"
            ),
            {"id": enrol_id},
        )
        claims.append({
            "enrolment_id": enrol_id,
            "step_run_id": run.id,
            "step_id": step.id,
            "channel": step.channel,
        })
    await session.commit()
    return claims


# ----------- send phase -----------


async def _advance_enrolment(
    session: AsyncSession, enrolment_id: int, finished_step_order: int
) -> None:
    e = await session.scalar(select(Enrolment).where(Enrolment.id == enrolment_id))
    if e is None:
        return
    seq = await session.scalar(select(Sequence).where(Sequence.id == e.sequence_id))
    if seq is None:
        return
    e.current_step_order = finished_step_order
    e.runtime_state = {**(e.runtime_state or {}), "consecutive_failures": 0}

    next_step = await session.scalar(
        select(SequenceStep)
        .where(
            SequenceStep.sequence_id == e.sequence_id,
            SequenceStep.step_order > finished_step_order,
        )
        .order_by(SequenceStep.step_order.asc())
        .limit(1)
    )
    if next_step is None:
        e.status = "done"
        e.stopped_at = datetime.now(timezone.utc)
        e.stopped_reason = "completed"
        e.next_send_at = None
    else:
        slot = next_valid_slot(
            base=datetime.now(timezone.utc),
            delay_days=next_step.delay_days,
            delay_hours=next_step.delay_hours,
            tz_name=seq.timezone,
            window_start=seq.send_window_start,
            window_end=seq.send_window_end,
            days_mask=seq.send_days_mask,
        )
        e.next_send_at = add_jitter(slot)


async def _mark_run_failed(
    session: AsyncSession,
    step_run_id: int,
    error: str,
    *,
    reschedule_minutes: int = 60,
    count_failure: bool = True,
) -> None:
    """Mark a run failed and reschedule (or terminate) its enrolment.

    count_failure=False is for CONFIG faults (no channel configured, bad
    credentials): those are the operator's to fix, and burning the lead to
    `errored` after 5 of them would punish the lead for our misconfiguration.
    The enrolment just keeps rescheduling until the config is fixed.
    """
    run = await session.scalar(select(StepRun).where(StepRun.id == step_run_id))
    if run is None:
        return
    if run.status not in ("reserving", "queued"):
        return  # already finalised
    run.status = "failed"
    run.error_message = error[:1000]
    run.sent_at = None

    e = await session.scalar(select(Enrolment).where(Enrolment.id == run.enrolment_id))
    if e is not None:
        # Fresh dict, NOT a mutate-then-reassign of the same object — SQLAlchemy
        # does not detect re-assignment of the identical dict and silently drops
        # the change (same bug class as the acceptance-flag fix in
        # routes_extension._advance_after_linkedin). With the old code the
        # failure counter never persisted once runtime_state was non-empty, so
        # enrolments could retry forever instead of erroring out at 5.
        state = {**(e.runtime_state or {})}
        if count_failure:
            state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
        e.runtime_state = state
        if count_failure and state["consecutive_failures"] >= 5:
            e.status = "errored"
            e.stopped_at = datetime.now(timezone.utc)
            e.stopped_reason = f"5 consecutive failures: {error[:200]}"
            e.next_send_at = None
        else:
            seq = await session.scalar(select(Sequence).where(Sequence.id == e.sequence_id))
            if seq is not None:
                from datetime import timedelta
                # Snap the retry into the send window — flat offsets retried
                # through the night (3 AM sends look robotic).
                e.next_send_at = next_valid_slot(
                    base=datetime.now(timezone.utc) + timedelta(minutes=reschedule_minutes),
                    delay_days=0, delay_hours=0,
                    tz_name=seq.timezone,
                    window_start=seq.send_window_start,
                    window_end=seq.send_window_end,
                    days_mask=seq.send_days_mask,
                )


async def _pick_email_channel(session: AsyncSession, user_id: int) -> Channel | None:
    return await session.scalar(
        select(Channel)
        .where(
            Channel.user_id == user_id,
            Channel.channel_type == "email",
            Channel.status == "active",
        )
        .order_by(Channel.id.asc())
        .limit(1)
    )


async def _build_threading_headers(
    session: AsyncSession, enrolment_id: int
) -> tuple[str | None, list[str]]:
    """Return (In-Reply-To, References list) for the next email in this thread.

    Only `status='sent'` runs participate — failed/skipped runs never appear in
    the chain (matches v3 PLAN §6a #13).
    """
    runs = (await session.execute(
        select(StepRun.provider_message_id)
        .where(
            StepRun.enrolment_id == enrolment_id,
            StepRun.status == "sent",
            StepRun.provider_message_id.isnot(None),
        )
        .order_by(StepRun.sent_at.asc())
    )).all()
    chain = [r[0] for r in runs if r[0]]
    if not chain:
        return None, []
    return chain[-1], chain


async def process_one(claim: dict) -> dict:
    """Send + finalise. Pure async — call directly from tests or workers."""
    step_run_id = claim["step_run_id"]
    async with SessionLocal() as session:
        run = await session.scalar(select(StepRun).where(StepRun.id == step_run_id))
        if run is None:
            return {"step_run_id": step_run_id, "result": "missing_run"}
        if run.status != "reserving":
            return {"step_run_id": step_run_id, "result": f"already_{run.status}"}

        enrolment = await session.scalar(select(Enrolment).where(Enrolment.id == run.enrolment_id))
        if enrolment is None:
            return {"step_run_id": step_run_id, "result": "missing_enrolment"}

        # Sender name for {{sender_name}} sign-offs — the sending user's display name.
        sender = await session.get(User, enrolment.user_id)
        sender_name = sender.display_name if sender else ""

        step = await session.scalar(select(SequenceStep).where(SequenceStep.id == run.step_id))
        if step is None:
            await _mark_run_failed(session, run.id, "step deleted")
            await session.commit()
            return {"step_run_id": step_run_id, "result": "step_missing"}

        # Suppression check
        if await is_suppressed(session, enrolment.user_id, enrolment.identity_hash, step.channel):
            run.status = "skipped"
            run.error_message = "suppressed"
            await _advance_enrolment(session, enrolment.id, step.step_order)
            await session.commit()
            return {"step_run_id": step_run_id, "result": "suppressed"}

        # Manual tasks (call / sms / whatsapp) — log reminder and advance
        if step.channel in ("call", "sms", "whatsapp"):
            from outreach.models.event import Event

            run.status = "sent"
            run.sent_at = datetime.now(timezone.utc)
            run.provider_message_id = f"manual-{step.channel}-{run.id}"
            session.add(Event(
                enrolment_id=run.enrolment_id,
                step_run_id=run.id,
                event_type="manual_task",
                channel=step.channel,
                payload={
                    "task": (step.body or "")[:1000],
                    "subject": step.subject,
                    "step_order": step.step_order,
                },
                occurred_at=run.sent_at,
            ))
            await _advance_enrolment(session, enrolment.id, step.step_order)
            await session.commit()
            return {"step_run_id": step_run_id, "result": "manual_task"}

        # LinkedIn: mint a li_command, mark step_run as queued_external, leave
        # enrolment paused (next_send_at=NULL) — the extension webhook on
        # /api/extension/commands/{id}/complete will advance it. Per PLAN §7.
        if step.channel in ("linkedin_dm", "linkedin_connect"):
            from datetime import timedelta
            from outreach.models.li_command import LinkedInCommand

            # Acceptance gate: a DM that follows a connect is parked until the
            # invite is accepted. If we got here with the gate still set, the
            # timeout deadline elapsed without acceptance — skip the DM (we can't
            # message a non-connection) and advance to the next step.
            if step.channel == "linkedin_dm":
                state = enrolment.runtime_state or {}
                if state.get("awaiting_acceptance"):
                    run.status = "skipped"
                    run.error_message = "connection not accepted within timeout window"
                    enrolment.runtime_state = {
                        k: v for k, v in state.items()
                        if k not in ("awaiting_acceptance", "awaiting_since")
                    }
                    await _advance_enrolment(session, enrolment.id, step.step_order)
                    await session.commit()
                    return {"step_run_id": step_run_id, "result": "skipped_unaccepted"}

            target_li_url = (enrolment.contact_snapshot or {}).get("linkedin_url")
            if not target_li_url:
                run.status = "skipped"
                run.error_message = "no linkedin_url on contact"
                await _advance_enrolment(session, enrolment.id, step.step_order)
                await session.commit()
                return {"step_run_id": step_run_id, "result": "no_linkedin_url"}

            li_channel = await session.scalar(
                select(Channel).where(
                    Channel.user_id == enrolment.user_id,
                    Channel.channel_type == "linkedin",
                    Channel.status == "active",
                ).order_by(Channel.id.asc()).limit(1)
            )
            if li_channel is None:
                await _mark_run_failed(
                    session, run.id,
                    "no active linkedin channel — install the Chrome extension and create a LinkedIn channel",
                    count_failure=False,
                )
                await session.commit()
                return {"step_run_id": step_run_id, "result": "no_linkedin_channel"}

            # Per-type LinkedIn daily caps (PLAN §7.3 / §8). These settings
            # existed but were enforced NOWHERE — the only limit was the
            # channel-level daily_cap (default 100!), charged after the fact.
            # Unbounded connects/DMs per day is how accounts get restricted, so
            # enforce here at mint time: count this user's commands of this
            # type in the rolling 24h window; at cap, defer a few hours
            # without burning the lead or advancing the step.
            command_type = "dm" if step.channel == "linkedin_dm" else "connect"
            settings = get_settings()
            type_cap = (
                settings.li_daily_cap_dm if command_type == "dm"
                else settings.li_daily_cap_connect
            )
            used = int(await session.scalar(text(
                "SELECT COUNT(*) FROM outreach.li_commands "
                "WHERE user_id = :uid AND command_type = :ct "
                "  AND created_at >= NOW() - INTERVAL '24 hours' "
                "  AND status NOT IN ('failed','expired')"
            ), {"uid": enrolment.user_id, "ct": command_type}) or 0)
            if used >= type_cap:
                from datetime import timedelta
                run.status = "failed"
                run.error_message = (
                    f"linkedin daily {command_type} cap reached ({type_cap}/24h) — deferred"
                )
                seq_for_window = await session.scalar(
                    select(Sequence).where(Sequence.id == enrolment.sequence_id)
                )
                cap_base = datetime.now(timezone.utc) + timedelta(hours=3)
                enrolment.next_send_at = (
                    next_valid_slot(
                        base=cap_base, delay_days=0, delay_hours=0,
                        tz_name=seq_for_window.timezone,
                        window_start=seq_for_window.send_window_start,
                        window_end=seq_for_window.send_window_end,
                        days_mask=seq_for_window.send_days_mask,
                    ) if seq_for_window is not None else cap_base
                )
                await session.commit()
                return {"step_run_id": step_run_id, "result": "li_cap_hit"}

            # Heartbeat freshness check — if the extension hasn't checked in for
            # >1h, don't burn a command attempt. Instead of punishing a just-
            # opened extension with a fixed +1h wait, we PARK the enrolment and
            # tag it `deferred_offline`; the /heartbeat endpoint pulls it forward
            # the instant the extension reconnects. The +backstop is only a
            # safety net in case that wake is ever missed.
            hb_stale = (
                li_channel.ext_last_heartbeat_at is None
                or (datetime.now(timezone.utc) - li_channel.ext_last_heartbeat_at)
                    > timedelta(hours=1)
            )
            if hb_stale:
                run.status = "skipped"
                run.error_message = "deferred: extension offline (resumes on reconnect)"
                enrolment.runtime_state = {
                    **(enrolment.runtime_state or {}), "deferred_offline": True,
                }
                enrolment.next_send_at = datetime.now(timezone.utc) + timedelta(
                    minutes=get_settings().li_offline_backstop_minutes
                )
                await session.commit()
                return {"step_run_id": step_run_id, "result": "extension_offline"}

            snapshot = {**(enrolment.contact_snapshot or {}), "sender_name": sender_name}
            rendered_body = render(step.body, snapshot)
            cmd = LinkedInCommand(
                user_id=enrolment.user_id,
                enrolment_id=enrolment.id,
                step_id=step.id,
                command_type=command_type,
                target_li_url=target_li_url,
                body_text=rendered_body,
                status="pending",
            )
            session.add(cmd)
            run.status = "queued_external"
            # Don't advance enrolment — wait for /commands/{id}/complete webhook.
            await session.commit()
            return {"step_run_id": step_run_id, "result": "linkedin_queued"}

        # ---- email path ----
        channel = await _pick_email_channel(session, enrolment.user_id)
        if channel is None:
            await _mark_run_failed(
                session, run.id, "no active email channel configured",
                count_failure=False,
            )
            await session.commit()
            return {"step_run_id": step_run_id, "result": "no_channel"}

        try:
            cfg_dict = decrypt_json(channel.config_encrypted)
            cfg = SMTPConfig.model_validate(cfg_dict["smtp"])
        except Exception as e:  # noqa: BLE001
            await _mark_run_failed(
                session, run.id, f"channel config invalid: {e}",
                count_failure=False,
            )
            await session.commit()
            return {"step_run_id": step_run_id, "result": "bad_channel_cfg"}

        to_address = (enrolment.contact_snapshot or {}).get("email")
        if not to_address:
            run.status = "skipped"
            run.error_message = "no email on contact"
            await _advance_enrolment(session, enrolment.id, step.step_order)
            await session.commit()
            return {"step_run_id": step_run_id, "result": "no_to_address"}

        # Daily-cap CAS *before* any external call. If hit, refund the slot is a
        # no-op (we never charged) and we reschedule for 1h.
        new_count = await bump_or_reject(session, channel.id)
        if new_count is None:
            run.status = "failed"
            run.error_message = "channel daily cap reached"
            from datetime import timedelta
            enrolment.next_send_at = datetime.now(timezone.utc) + timedelta(hours=1)
            await session.commit()
            return {"step_run_id": step_run_id, "result": "cap_hit"}
        await session.commit()  # release the row locks before SMTP

    # ---- SMTP call OUTSIDE any DB transaction ----
    message_id = make_message_id(step_run_id)
    snapshot = {**(enrolment.contact_snapshot or {}), "sender_name": sender_name}
    rendered_subject = render(step.subject, snapshot)
    rendered_body = render(step.body, snapshot)

    # Auto-draft for sequences with AI follow-ups enabled, starting at step >= 2
    # (step 1 is the cold opener — no prior context to personalise against).
    async with SessionLocal() as session_seq:
        seq = await session_seq.scalar(select(Sequence).where(Sequence.id == enrolment.sequence_id))
    if seq is not None and seq.ai_followups_enabled and step.step_order >= 2:
        try:
            from outreach.services.followup_agent import draft_for_lead
            async with SessionLocal() as session_draft:
                draft = await draft_for_lead(
                    session_draft, enrolment.lead_id,
                    template_subject=step.subject or "Following up",
                    template_body=step.body,
                    contact_snapshot=snapshot,
                )
            rendered_subject = draft.subject
            rendered_body = draft.body
            log.info(
                "auto-drafted step %d for enrol %d via %s (notes: %s)",
                step.step_order, enrolment.id, draft.model, draft.notes[:60],
            )
        except Exception:  # noqa: BLE001
            log.exception("auto-draft failed for enrol %d step %d; falling back to template",
                          enrolment.id, step.step_order)

    in_reply_to, references = None, []
    async with SessionLocal() as session2:
        in_reply_to, references = await _build_threading_headers(session2, enrolment.id)

    result = await send_email(
        cfg=cfg,
        to_address=to_address,
        subject=rendered_subject,
        body=rendered_body,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
    )

    # ---- finalise ----
    async with SessionLocal() as session3:
        run = await session3.scalar(select(StepRun).where(StepRun.id == step_run_id))
        if run is None:
            return {"step_run_id": step_run_id, "result": "missing_run_finalise"}
        if result.ok:
            run.status = "sent"
            run.sent_at = datetime.now(timezone.utc)
            run.provider_message_id = message_id
            session3.add(Event(
                enrolment_id=run.enrolment_id, step_run_id=run.id,
                event_type="delivered", channel="email",
                external_id=message_id,
                payload={"to": to_address, "subject": rendered_subject[:200]},
                occurred_at=run.sent_at,
            ))
            await _advance_enrolment(session3, run.enrolment_id, step.step_order)
        else:
            await refund_one(session3, channel.id)
            await _mark_run_failed(session3, run.id, result.error or "send failed")
        await session3.commit()

    return {
        "step_run_id": step_run_id,
        "result": "sent" if result.ok else "failed",
        "error": result.error,
    }


# ----------- tick orchestrator -----------


async def tick(limit: int = 500) -> dict:
    """One scheduler tick. Returns counts."""
    settings = get_settings()
    async with SessionLocal() as session:
        # Self-heal any commands the extension claimed but never finished, then
        # claim newly-due enrolments.
        await reclaim_stale_li_commands(session, settings.li_claim_stale_minutes)
        await session.commit()
        claims = await claim_due(session, limit=limit)
    if not claims:
        return {"claimed": 0, "sent": 0, "failed": 0}

    sem = asyncio.Semaphore(settings.send_concurrency)

    async def _run(claim: dict) -> dict:
        async with sem:
            try:
                return await process_one(claim)
            except Exception as e:  # noqa: BLE001
                log.exception("process_one crashed for run %s", claim.get("step_run_id"))
                async with SessionLocal() as s:
                    await _mark_run_failed(s, claim["step_run_id"], f"crash: {e}")
                    await s.commit()
                return {"step_run_id": claim["step_run_id"], "result": "crashed", "error": str(e)}

    results = await asyncio.gather(*[_run(c) for c in claims])
    sent = sum(1 for r in results if r.get("result") == "sent")
    failed = sum(1 for r in results if r.get("result") in ("failed", "crashed", "cap_hit", "li_cap_hit", "no_channel", "bad_channel_cfg"))
    log.info("tick: claimed=%d sent=%d failed=%d", len(claims), sent, failed)
    return {"claimed": len(claims), "sent": sent, "failed": failed, "details": results}
