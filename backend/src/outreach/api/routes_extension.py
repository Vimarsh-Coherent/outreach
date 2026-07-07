"""Chrome-extension endpoints. The extension polls `/next-command` every 30s,
executes the DOM automation, and reports back via `/complete` or `/replies`.

All routes are auth'd via `get_extension_channel` (HMAC-verified token).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import re

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.models.channel import Channel
from outreach.models.enrolment import Enrolment
from outreach.models.event import Event
from outreach.models.li_command import LinkedInCommand
from outreach.models.sequence import Sequence
from outreach.models.step import SequenceStep
from outreach.models.step_run import StepRun
from outreach.config import get_settings
from outreach.schemas.extension import (
    CommandCompleteRequest,
    CommandOut,
    ConnectionSeenItem,
    ConnectionSeenResult,
    InboundReplyItem,
    InboundReplyResult,
)
from outreach.schemas.selector_heal import HealRequest, HealResponse
from outreach.services import reply_processor, selector_healer, watchdog_state
from outreach.services.cap_check import bump_or_reject
from outreach.services.extension_auth import get_extension_channel
from outreach.services.send_window import next_valid_slot

log = logging.getLogger("outreach.extension")

router = APIRouter(prefix="/api/extension", tags=["extension"])

# Error fragments that mean "our DOM automation broke", not "this lead is bad".
# These are platform-side faults (LinkedIn UI drift, closed tab, dead service
# worker) that the watchdog actively retries — so they get a higher errored
# threshold than genuine failures. Keep in sync with the Tier-3 retry patterns
# in workers/watchdog.py and FRAGILITY_TO_INTENT in content_linkedin.js.
_FRAGILITY_PATTERNS = (
    "_not_found_even_after_heal",
    "composer_remained_empty_after_insertion",
    "senddmbutton_not_enabled_text_didnt_register",
    "send_click_was_noop_composer_still_has_text",
    "sendinvitationbutton_disabled_note_didnt_register",
    "dm_target_window_not_found",
    "dm_target_name_unresolved",
    "wrong_profile_page",
    "profile_dom_mismatch",
    "receiving end does not exist",
    "no_linkedin_tab_open",
    "all_tabs_unresponsive",
)


def _is_fragility_error(error: str | None) -> bool:
    e = (error or "").lower()
    return any(p in e for p in _FRAGILITY_PATTERNS)


# LinkedIn profile URLs arrive in many spellings: leads store
# `https://www.linkedin.com/in/<slug>` (no trailing slash, via
# identity.linkedin_url_from_slug) while the extension scrapes hrefs and posts
# `https://www.linkedin.com/in/<slug>/` (trailing slash) — and country
# subdomains / query params / case differences also occur. Exact string
# equality therefore NEVER matched, which silently broke acceptance detection
# and LinkedIn reply matching. Match on the lowercased /in/ slug instead.
_LI_SLUG_RE = re.compile(r"/in/([^/?#]+)", re.IGNORECASE)
# Postgres POSIX regex used to extract the same slug from the stored snapshot
# URL inside the query (substring() returns capture group 1).
_LI_SLUG_PG_PATTERN = "/in/([^/?#]+)"


def _li_slug(url: str | None) -> str | None:
    m = _LI_SLUG_RE.search(url or "")
    return m.group(1).rstrip("/").lower() if m else None


def _enrolment_li_url_matches(slug: str):
    """SQLAlchemy criterion: enrolment's snapshot linkedin_url has this slug."""
    return func.lower(
        func.substring(
            Enrolment.contact_snapshot["linkedin_url"].astext, _LI_SLUG_PG_PATTERN
        )
    ) == slug


@router.get("/heartbeat", status_code=204)
async def heartbeat(
    channel: Channel = Depends(get_extension_channel),
    session: AsyncSession = Depends(get_session),
) -> Response:
    channel.ext_last_heartbeat_at = datetime.now(timezone.utc)
    # Wake any enrolments that were parked because the extension was offline at
    # send time — pull them forward so the next scheduler tick (≤60s) sends
    # them immediately, instead of waiting out the backstop window.
    await session.execute(
        text(
            "UPDATE outreach.enrolments "
            "   SET next_send_at = NOW(), "
            "       runtime_state = runtime_state - 'deferred_offline', "
            "       updated_at = NOW() "
            " WHERE user_id = :uid AND status = 'active' "
            "   AND runtime_state->>'deferred_offline' = 'true'"
        ),
        {"uid": channel.user_id},
    )
    await session.commit()
    return Response(status_code=204)


# ── Side-panel data endpoints (extension-token auth) ────────────────────────
# These power the in-extension dashboard: campaigns, live activity, and health.
# All scoped to the channel's user_id so the extension only sees its own data.

@router.get("/campaigns")
async def list_campaigns(
    channel: Channel = Depends(get_extension_channel),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (await session.execute(text(
        """
        SELECT s.id, s.name, s.status,
               COUNT(DISTINCT e.id)                                                     AS enrolled,
               COUNT(DISTINCT e.id) FILTER (WHERE e.status='active')                    AS active,
               COUNT(DISTINCT e.id) FILTER (WHERE e.status='done')                      AS done,
               COUNT(DISTINCT e.id) FILTER (WHERE e.status LIKE 'stopped%')             AS stopped,
               COUNT(DISTINCT e.id) FILTER (WHERE e.runtime_state->>'awaiting_acceptance'='true') AS awaiting,
               COUNT(DISTINCT lc.id) FILTER (WHERE lc.command_type='connect' AND lc.status='done') AS connects_sent,
               COUNT(DISTINCT lc.id) FILTER (WHERE lc.command_type='dm' AND lc.status='done')      AS dms_sent,
               COUNT(DISTINCT ev.id) FILTER (WHERE ev.event_type='connection_accepted') AS accepted,
               COUNT(DISTINCT ev.id) FILTER (WHERE ev.event_type='reply')               AS replied
          FROM outreach.sequences s
          LEFT JOIN outreach.enrolments e  ON e.sequence_id = s.id
          LEFT JOIN outreach.li_commands lc ON lc.enrolment_id = e.id
          LEFT JOIN outreach.events ev      ON ev.enrolment_id = e.id
         WHERE s.user_id = :uid
         GROUP BY s.id, s.name, s.status
         ORDER BY s.id DESC
        """
    ), {"uid": channel.user_id})).all()
    return [
        {
            "id": int(r[0]), "name": r[1], "status": r[2],
            "enrolled": int(r[3]), "active": int(r[4]), "done": int(r[5]),
            "stopped": int(r[6]), "awaiting_acceptance": int(r[7]),
            "connects_sent": int(r[8]), "dms_sent": int(r[9]),
            "accepted": int(r[10]), "replied": int(r[11]),
        }
        for r in rows
    ]


@router.get("/leads")
async def list_leads(
    channel: Channel = Depends(get_extension_channel),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Per-lead LinkedIn connection status — what the user asked to see:
    which profiles are connected, which are pending (invited), which not sent."""
    rows = (await session.execute(text(
        """
        SELECT e.id, s.name, e.status,
               e.contact_snapshot->>'linkedin_url'  AS url,
               e.contact_snapshot->>'first_name'    AS fn,
               e.contact_snapshot->>'last_name'     AS ln,
               e.runtime_state->>'li_status'        AS li_status,
               (e.runtime_state->>'awaiting_acceptance' = 'true') AS awaiting
          FROM outreach.enrolments e
          JOIN outreach.sequences s ON s.id = e.sequence_id
         WHERE e.user_id = :uid
         ORDER BY e.id DESC
         LIMIT 200
        """
    ), {"uid": channel.user_id})).all()
    out = []
    for r in rows:
        li = r[6]
        awaiting = bool(r[7])
        if li == "dm_sent":
            conn = "dm_sent"
        elif li == "connected":
            conn = "connected"
        elif awaiting or li == "invited_pending":
            conn = "pending"
        else:
            conn = "not_sent"
        name = " ".join(p for p in (r[4], r[5]) if p) or (r[3] or "").split("/in/")[-1].rstrip("/")
        out.append({
            "enrolment_id": int(r[0]), "campaign": r[1], "enrolment_status": r[2],
            "name": name, "url": r[3], "connection": conn,
        })
    return out


@router.get("/activity")
async def recent_activity(
    limit: int = 30,
    channel: Channel = Depends(get_extension_channel),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    limit = max(1, min(limit, 100))
    rows = (await session.execute(text(
        """
        SELECT id, command_type, status, target_li_url, error_message,
               created_at, completed_at
          FROM outreach.li_commands
         WHERE user_id = :uid
         ORDER BY id DESC
         LIMIT :lim
        """
    ), {"uid": channel.user_id, "lim": limit})).all()
    return [
        {
            "id": int(r[0]), "type": r[1], "status": r[2],
            "target": r[3], "error": r[4],
            "created_at": r[5].isoformat() if r[5] else None,
            "completed_at": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]


@router.get("/status")
async def extension_status(
    channel: Channel = Depends(get_extension_channel),
    session: AsyncSession = Depends(get_session),
) -> dict:
    st = watchdog_state.state()
    counts = (await session.execute(text(
        """
        SELECT
          COUNT(*) FILTER (WHERE status='pending')                                 AS pending_cmds,
          COUNT(*) FILTER (WHERE status='claimed')                                 AS claimed_cmds,
          COUNT(*) FILTER (WHERE status='done'   AND completed_at::date = NOW()::date) AS done_today,
          COUNT(*) FILTER (WHERE status='failed' AND completed_at::date = NOW()::date) AS failed_today
          FROM outreach.li_commands WHERE user_id = :uid
        """
    ), {"uid": channel.user_id})).first()
    return {
        "channel": {
            "id": channel.id, "status": channel.status,
            "sent_today": channel.sent_today, "daily_cap": channel.daily_cap,
            "heartbeat_at": channel.ext_last_heartbeat_at.isoformat()
                if channel.ext_last_heartbeat_at else None,
        },
        "queue": {
            "pending": int(counts[0] or 0), "claimed": int(counts[1] or 0),
            "done_today": int(counts[2] or 0), "failed_today": int(counts[3] or 0),
        },
        "watchdog": {
            "db_alive": st.db_alive,
            "awaiting_acceptance": st.awaiting_acceptance,
            "acceptance_detection_stalled": st.acceptance_detection_stalled,
            "li_successes_today": st.linkedin_command_successes_today,
            "li_failures_today": st.linkedin_command_failures_today,
            "connection_accepts_today": st.connection_accepts_today,
            "fragile_intents": watchdog_state.fragile_intents(),
            "circuit_breaker_open": st.disabled_until is not None,
        },
    }


# Atomic claim of one pending command. SKIP LOCKED + UPDATE in one transaction
# guarantees no two extension polls grab the same row.
_CLAIM_SQL = text(
    """
    WITH cte AS (
      SELECT id
        FROM outreach.li_commands
       WHERE user_id = :uid
         AND status = 'pending'
       ORDER BY created_at
       LIMIT 1
         FOR UPDATE SKIP LOCKED
    )
    UPDATE outreach.li_commands c
       SET status = 'claimed', claimed_at = NOW()
      FROM cte
     WHERE c.id = cte.id
     RETURNING c.id, c.command_type, c.target_li_url, c.body_text;
    """
)


@router.get("/next-command", response_model=CommandOut | None)
async def next_command(
    channel: Channel = Depends(get_extension_channel),
    session: AsyncSession = Depends(get_session),
) -> Response | CommandOut:
    r = await session.execute(_CLAIM_SQL, {"uid": channel.user_id})
    row = r.first()
    await session.commit()
    if row is None:
        return Response(status_code=204)
    return CommandOut(
        id=int(row[0]), command_type=row[1],
        target_li_url=row[2], body_text=row[3] or "",
    )


@router.post("/commands/{cmd_id}/complete")
async def complete_command(
    cmd_id: int,
    body: CommandCompleteRequest,
    channel: Channel = Depends(get_extension_channel),
    session: AsyncSession = Depends(get_session),
) -> dict:
    cmd = await session.scalar(
        select(LinkedInCommand).where(
            LinkedInCommand.id == cmd_id, LinkedInCommand.user_id == channel.user_id
        )
    )
    if cmd is None:
        raise HTTPException(404, "command not found")
    if cmd.status not in ("pending", "claimed"):
        return {"already": cmd.status}

    # Find the step_run we created when the dispatcher minted this command.
    run = await session.scalar(
        select(StepRun).where(
            StepRun.enrolment_id == cmd.enrolment_id,
            StepRun.step_id == cmd.step_id,
            StepRun.status == "queued_external",
        ).order_by(StepRun.id.desc()).limit(1)
    )
    enrolment = await session.scalar(select(Enrolment).where(Enrolment.id == cmd.enrolment_id))
    step = await session.scalar(select(SequenceStep).where(SequenceStep.id == cmd.step_id))

    now = datetime.now(timezone.utc)
    if body.status == "done":
        cmd.status = "done"
        cmd.completed_at = now
        if run is not None:
            run.status = "sent"
            run.sent_at = now
            run.provider_message_id = body.provider_message_id
        if enrolment is not None and step is not None:
            session.add(Event(
                enrolment_id=enrolment.id, step_run_id=run.id if run else None,
                event_type="delivered", channel="linkedin",
                external_id=body.provider_message_id,
                payload={"target": cmd.target_li_url},
                occurred_at=now,
            ))
            # Advance enrolment to next step / done (a connect followed by a DM
            # is parked until the invite is accepted — see _advance_after_linkedin).
            await _advance_after_linkedin(
                session, enrolment, step.step_order, finished_channel=step.channel,
                provider_message_id=body.provider_message_id,
            )
        # Charge the channel cap *after* success (best-effort).
        await bump_or_reject(session, channel.id)
        await session.commit()
        # Real-time watchdog: this command finished successfully.
        watchdog_state.note_li_command_outcome(
            cmd_id=cmd_id, status="done", command_type=cmd.command_type,
        )
        return {"finalised": "sent"}
    # failed
    cmd.status = "failed"
    cmd.error_message = body.error or "extension reported failure"
    cmd.completed_at = now
    if run is not None:
        run.status = "failed"
        run.error_message = body.error or "extension reported failure"
    # Real-time watchdog: classify the failure (DOM-fragility vs unknown), bump
    # per-intent counters, surface it on the dashboard immediately. The Tier 3
    # sweep will pick this up on its next pass and either retry or escalate.
    watchdog_state.note_li_command_outcome(
        cmd_id=cmd_id, status="failed",
        command_type=cmd.command_type, error=body.error,
    )
    if enrolment is not None:
        prev = enrolment.runtime_state or {}
        state = {**prev, "consecutive_failures": int(prev.get("consecutive_failures", 0)) + 1}
        enrolment.runtime_state = state
        # DOM-fragility errors are OUR automation breaking, not the lead being
        # unreachable — don't burn the lead after 5 of them while a selector is
        # broken. They still terminate eventually (bounded at 10) so a
        # permanent regression can't retry a lead forever.
        threshold = 10 if _is_fragility_error(body.error) else 5
        if state["consecutive_failures"] >= threshold:
            enrolment.status = "errored"
            enrolment.stopped_at = now
            enrolment.stopped_reason = (
                body.error or f"{threshold} consecutive linkedin failures"
            )
            enrolment.next_send_at = None
        else:
            # Retry in ~1h, but SNAPPED into the sequence's send window — a flat
            # +1h kept retrying through the night (observed: a DM attempt at
            # 02:55 IST), which is exactly the robotic pattern LinkedIn flags.
            seq = await session.scalar(
                select(Sequence).where(Sequence.id == enrolment.sequence_id)
            )
            base = now + timedelta(hours=1)
            if seq is not None:
                enrolment.next_send_at = next_valid_slot(
                    base=base, delay_days=0, delay_hours=0,
                    tz_name=seq.timezone,
                    window_start=seq.send_window_start,
                    window_end=seq.send_window_end,
                    days_mask=seq.send_days_mask,
                )
            else:
                enrolment.next_send_at = base
    await session.commit()
    return {"finalised": "failed"}


async def _advance_after_linkedin(
    session: AsyncSession,
    enrolment: Enrolment,
    finished_step_order: int,
    finished_channel: str | None = None,
    provider_message_id: str | None = None,
) -> None:
    seq = await session.scalar(select(Sequence).where(Sequence.id == enrolment.sequence_id))
    if seq is None:
        return
    now = datetime.now(timezone.utc)
    pmid = provider_message_id or ""
    already_connected = "already-connected" in pmid

    enrolment.current_step_order = finished_step_order
    # Build a FRESH dict each time — re-assigning the SAME mutated dict object is
    # not detected by SQLAlchemy and silently fails to persist (that was the
    # acceptance-flag bug). Also record the lead's LinkedIn connection status:
    #   invited_pending → invite sent, awaiting accept
    #   connected       → already / now a 1st-degree connection
    #   dm_sent         → follow-up DM delivered
    state = {**(enrolment.runtime_state or {}), "consecutive_failures": 0}
    if finished_channel == "linkedin_connect":
        state["li_status"] = "connected" if already_connected else "invited_pending"
        state["li_status_at"] = now.isoformat()
    elif finished_channel == "linkedin_dm":
        state["li_status"] = "dm_sent"
        state["li_status_at"] = now.isoformat()
    elif finished_channel == "linkedin_like":
        state["li_status"] = "post_liked"
        state["li_status_at"] = now.isoformat()

    next_step = await session.scalar(
        select(SequenceStep).where(
            SequenceStep.sequence_id == enrolment.sequence_id,
            SequenceStep.step_order > finished_step_order,
        ).order_by(SequenceStep.step_order.asc()).limit(1)
    )
    if next_step is None:
        enrolment.status = "done"
        enrolment.stopped_at = now
        enrolment.stopped_reason = "completed"
        enrolment.next_send_at = None
        enrolment.runtime_state = state
        return

    # Acceptance gate: after a connect, ANY later linkedin_dm step in this
    # sequence — not just the one immediately next — must wait until the
    # invite is accepted. Other channel steps (e.g. whatsapp) may run in
    # between and are unaffected; the dispatcher enforces this gate itself
    # when it actually reaches a linkedin_dm step_run, using this flag.
    if finished_channel == "linkedin_connect":
        future_dm = await session.scalar(
            select(SequenceStep.id).where(
                SequenceStep.sequence_id == enrolment.sequence_id,
                SequenceStep.step_order > finished_step_order,
                SequenceStep.channel == "linkedin_dm",
            ).order_by(SequenceStep.step_order.asc()).limit(1)
        )
        if future_dm is not None and not already_connected:
            state["awaiting_acceptance"] = True
            state["awaiting_since"] = now.isoformat()
        else:
            state["awaiting_acceptance"] = False

        # When the DM IS the immediate next step and still gated, park the
        # enrolment for the full accept-timeout window rather than cycling
        # the dispatcher every tick only to have it re-defer.
        if next_step.channel == "linkedin_dm" and state["awaiting_acceptance"]:
            enrolment.next_send_at = now + timedelta(days=get_settings().li_accept_timeout_days)
            enrolment.runtime_state = state
            return

    if next_step.delay_days == 0 and next_step.delay_hours == 0:
        # Zero-delay step: fire immediately, back-to-back — see matching
        # comment in dispatcher._advance_enrolment.
        enrolment.next_send_at = now
    else:
        enrolment.next_send_at = next_valid_slot(
            base=now, delay_days=next_step.delay_days, delay_hours=next_step.delay_hours,
            tz_name=seq.timezone, window_start=seq.send_window_start,
            window_end=seq.send_window_end, days_mask=seq.send_days_mask,
        )
    enrolment.runtime_state = state


@router.post("/heal-selector", response_model=HealResponse)
async def heal_selector_endpoint(
    req: HealRequest,
    channel: Channel = Depends(get_extension_channel),
    session: AsyncSession = Depends(get_session),
) -> HealResponse:
    """AI selector healer — ported from watchlink's SelectorHealerTool.

    Content script calls this when its registry selectors fail. We send the
    DOM snapshot + intent to Claude Haiku, get back 3 ranked CSS selectors,
    persist a small audit log row, and return the selectors.
    """
    response = await selector_healer.heal_selector(req)
    # Audit trail
    try:
        await selector_healer.log_heal_in_db(
            session, channel.user_id, req.intent, response.selectors, response.cost_usd,
        )
    except Exception:  # noqa: BLE001
        pass
    if response.selectors:
        heal_count = await selector_healer.upsert_current_selectors(
            session, channel.id, req.intent, response.selectors, source="reactive",
        )
        # A single heal is normal drift. The SAME intent needing healing again
        # is the actual "LinkedIn changed its pattern" signal — surface it
        # distinctly instead of quietly logging one more routine heal.
        if heal_count is not None and heal_count >= 2:
            watchdog_state.log_event(
                "stuck_state_sweep", "pattern_change",
                f"LinkedIn UI pattern change detected for '{req.intent}' — healed "
                f"{heal_count} times; latest selectors pushed to the extension registry",
                intent=req.intent, heal_count=heal_count, source="reactive",
            )
    return response


@router.get("/current-selectors")
async def current_selectors_endpoint(
    channel: Channel = Depends(get_extension_channel),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Server-persisted selector overrides for this channel — populated by both
    the extension's own reactive heals and the watchdog's preemptive Tier-3
    heals. Polled alongside /next-command so a preemptive heal (which the
    extension itself never triggered, and has no browser tab to push into
    directly) still reaches this browser within one 30s poll cycle instead of
    sitting unused until a fresh in-session failure."""
    return await selector_healer.get_current_selectors(session, channel.id)


@router.post("/watchdog-note", status_code=204)
async def watchdog_note(
    body: dict,
    channel: Channel = Depends(get_extension_channel),
) -> Response:
    """Realtime self-heal telemetry from the content script.

    During the in-loop retry-with-heal sequence the content script reports
    each step (attempt failed / heal requested / heal succeeded / recovered)
    here so the watchdog dashboard reflects the recovery as it happens —
    not only on the hourly Tier 3 sweep.
    """
    tier = str(body.get("tier", "extension_failure"))[:40]
    status = str(body.get("status", "issue"))[:20]
    message = str(body.get("message", ""))[:500]
    detail = {
        k: v for k, v in body.items()
        if k not in ("kind", "tier", "status", "message")
    }
    detail["channel_id"] = channel.id
    # `tier` Literal in watchdog_state is open enough for these; if a future
    # tier name is introduced the log_event call still appends to the rolling
    # event deque, so the dashboard still shows it.
    try:
        watchdog_state.log_event(tier, status, message, **detail)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        # Even malformed notes shouldn't break the recovery loop.
        pass
    return Response(status_code=204)


@router.post("/connections-seen", response_model=ConnectionSeenResult)
async def connections_seen(
    items: list[ConnectionSeenItem],
    channel: Channel = Depends(get_extension_channel),
    session: AsyncSession = Depends(get_session),
) -> ConnectionSeenResult:
    """Release the gated DM step for leads whose invitation was accepted.

    The extension reports profile URLs observed as 1st-degree connections. For
    each one that belongs to an enrolment currently `awaiting_acceptance`, we
    record a `connection_accepted` event and schedule the held DM step (applying
    that step's configured delay from the acceptance moment).
    """
    matched = 0
    released = 0
    now = datetime.now(timezone.utc)
    for item in items:
        slug = _li_slug(item.li_url)
        if not slug:
            continue
        enrolment = await session.scalar(
            select(Enrolment).where(
                Enrolment.user_id == channel.user_id,
                _enrolment_li_url_matches(slug),
                Enrolment.status == "active",
            ).order_by(Enrolment.id.desc()).limit(1)
        )
        if enrolment is None:
            continue
        state = enrolment.runtime_state or {}
        if not state.get("awaiting_acceptance"):
            continue  # not gated (already released, or not a connect→DM lead)
        matched += 1

        # Record the acceptance once (idempotent on (enrolment, type, url)).
        existing = await session.scalar(
            select(Event.id).where(
                Event.enrolment_id == enrolment.id,
                Event.event_type == "connection_accepted",
                Event.external_id == item.li_url,
            )
        )
        if existing is None:
            session.add(Event(
                enrolment_id=enrolment.id, step_run_id=None,
                event_type="connection_accepted", channel="linkedin",
                external_id=item.li_url,
                payload={"source": item.source or "scan"},
                occurred_at=item.accepted_at or now,
            ))

        # Release the gate and schedule the held DM step.
        seq = await session.scalar(select(Sequence).where(Sequence.id == enrolment.sequence_id))
        next_step = await session.scalar(
            select(SequenceStep).where(
                SequenceStep.sequence_id == enrolment.sequence_id,
                SequenceStep.step_order > enrolment.current_step_order,
            ).order_by(SequenceStep.step_order.asc()).limit(1)
        )
        # Fresh dict so the change actually persists (see _advance_after_linkedin).
        new_state = {k: v for k, v in state.items()
                     if k not in ("awaiting_acceptance", "awaiting_since")}
        new_state["connection_accepted_at"] = now.isoformat()
        new_state["li_status"] = "connected"
        new_state["li_status_at"] = now.isoformat()
        enrolment.runtime_state = new_state
        if next_step is not None and seq is not None:
            enrolment.next_send_at = next_valid_slot(
                base=now,
                delay_days=next_step.delay_days, delay_hours=next_step.delay_hours,
                tz_name=seq.timezone,
                window_start=seq.send_window_start,
                window_end=seq.send_window_end,
                days_mask=seq.send_days_mask,
            )
        else:
            enrolment.next_send_at = now
        released += 1
        # Real-time telemetry so the watchdog dashboard reflects the acceptance
        # funnel and the Tier-3 stall check knows detection is alive.
        watchdog_state.note_connection_accepted(enrolment.id, item.li_url)
    await session.commit()
    return ConnectionSeenResult(matched=matched, released=released)


@router.post("/replies", response_model=InboundReplyResult)
async def report_replies(
    items: list[InboundReplyItem],
    channel: Channel = Depends(get_extension_channel),
    session: AsyncSession = Depends(get_session),
) -> InboundReplyResult:
    matched = 0
    inserted = 0
    duplicates = 0
    spawned: list[int] = []
    for item in items:
        # Skip outbound snippets — LinkedIn prefixes them with "You:" in the inbox list
        if (item.body or "").lstrip().lower().startswith("you:"):
            continue
        # Primary match: linkedin slug extracted from li_url
        slug = _li_slug(item.li_url)
        enrolment = None
        if slug:
            enrolment = await session.scalar(
                select(Enrolment).where(
                    Enrolment.user_id == channel.user_id,
                    _enrolment_li_url_matches(slug),
                ).order_by(Enrolment.id.desc()).limit(1)
            )

        # Fallback: match by from_name (first + last) when profile URL unavailable
        if enrolment is None and item.from_name:
            name_parts = item.from_name.strip().split()
            if name_parts:
                first = name_parts[0].lower()
                last = name_parts[-1].lower() if len(name_parts) > 1 else None
                q = select(Enrolment).where(
                    Enrolment.user_id == channel.user_id,
                    func.lower(Enrolment.contact_snapshot["first_name"].astext) == first,
                )
                if last and last != first:
                    q = q.where(
                        func.lower(Enrolment.contact_snapshot["last_name"].astext) == last
                    )
                enrolment = await session.scalar(q.order_by(Enrolment.id.desc()).limit(1))
                if enrolment:
                    log.info("linkedin reply matched by name '%s' (no profile URL)", item.from_name)

        if enrolment is None:
            continue
        matched += 1
        ev = Event(
            enrolment_id=enrolment.id, step_run_id=None,
            event_type="reply", channel="linkedin",
            external_id=item.message_id,
            payload={
                "from": item.from_name or item.li_url,
                "subject": "(LinkedIn DM)",
                "snippet": (item.body or "")[:500],
                "thread_id": item.thread_id,
            },
            occurred_at=item.received_at,
        )
        session.add(ev)
        try:
            await session.flush()
            inserted += 1
            spawned.append(ev.id)
            if enrolment.status == "active":
                enrolment.status = "stopped_reply"
                enrolment.stopped_at = datetime.now(timezone.utc)
                enrolment.stopped_reason = "lead replied on linkedin"
                enrolment.next_send_at = None
        except IntegrityError:
            await session.rollback()
            duplicates += 1
            continue
    await session.commit()
    # Fire sentiment classification + vault indexing for each inserted event.
    for ev_id in spawned:
        try:
            reply_processor._spawn(reply_processor._bg_classify(ev_id))  # noqa: SLF001
        except Exception:  # noqa: BLE001
            log.exception("failed to spawn sentiment task for event %d", ev_id)
    return InboundReplyResult(matched=matched, inserted=inserted, duplicates=duplicates)
