"""Outreach platform watchdog — 5-tier patrol daemon.

Ported from watchlink-main/server/src/query/watchdog.ts. Tiers are adapted to
our domain: instead of patrolling LinkedIn DOM selectors, we patrol the things
that actually break in an outreach platform — SMTP/IMAP creds, the extension's
heartbeat, stuck step_runs/li_commands, API key validity, and the sentiment
classification backlog.

  Tier 1 — Quick check          every 5 min   | backend + DB + scheduler heartbeat
  Tier 2 — Channel patrol       every 30 min  | live SMTP/IMAP connect-tests
  Tier 3 — Stuck-state sweep    every 1 hour  | step_runs, li_commands, enrolments
  Tier 4 — Deep verify          every 6 hours | Anthropic + OpenAI + Qdrant
  Tier 5 — Daily reset          every 24 hour | cap reset, daily summary, AI heal

A circuit breaker across all tiers pauses everything after 5 consecutive failures
of any patrol (auto-resets after 30 min).
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from datetime import datetime, timedelta, timezone

import aioimaplib
import aiosmtplib
from sqlalchemy import select, text

from outreach.config import get_settings
from outreach.db import SessionLocal
from outreach.models.channel import Channel
from outreach.schemas.channels import IMAPConfig, SMTPConfig
from outreach.services import watchdog_state
from outreach.utils.crypto import decrypt_json

log = logging.getLogger("outreach.watchdog")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


# ──────────────────────────────────────────────────────────────────────────
# Tier 1 — Quick check (every 5 min)
# ──────────────────────────────────────────────────────────────────────────
async def quick_check() -> dict:
    if watchdog_state.is_disabled():
        return {"skipped": "circuit_breaker_open"}
    st = watchdog_state.state()
    try:
        async with SessionLocal() as session:
            ok = await session.scalar(text("SELECT 1"))
            st.db_alive = ok == 1
            st.backend_alive = True
        if st.db_alive:
            watchdog_state.log_event("quick_check", "healthy", "backend + db OK")
            watchdog_state.record_success()
        else:
            watchdog_state.log_event("quick_check", "issue", "DB ping returned non-1")
            watchdog_state.record_failure()
    except Exception as e:  # noqa: BLE001
        st.db_alive = False
        st.backend_alive = True  # we ran, but DB broke
        watchdog_state.log_event(
            "quick_check", "emergency", f"DB unreachable: {type(e).__name__}: {e}",
        )
        watchdog_state.record_failure()
    st.last_quick_check = _utcnow()
    return {
        "backend_alive": st.backend_alive,
        "db_alive": st.db_alive,
    }


# ──────────────────────────────────────────────────────────────────────────
# Tier 2 — Channel patrol (every 30 min)
# ──────────────────────────────────────────────────────────────────────────
async def _probe_smtp(cfg: SMTPConfig) -> tuple[bool, str]:
    use_ssl = cfg.security == "ssl_tls"
    use_starttls = cfg.security == "starttls"
    client = aiosmtplib.SMTP(
        hostname=cfg.host, port=cfg.port,
        use_tls=use_ssl, start_tls=False,
        tls_context=_ssl_ctx() if (use_ssl or use_starttls) else None,
        timeout=10,
    )
    try:
        await client.connect()
        if use_starttls:
            await client.starttls(tls_context=_ssl_ctx())
        if cfg.username:
            await client.login(cfg.username, cfg.password)
        return True, "ok"
    except aiosmtplib.SMTPAuthenticationError as e:
        return False, f"AUTH: {e.code} {e.message}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:80]}"
    finally:
        try: await client.quit()
        except Exception: pass  # noqa: BLE001, S110


async def _probe_imap(cfg: IMAPConfig) -> tuple[bool, str]:
    use_ssl = cfg.security == "ssl_tls"
    cls = aioimaplib.IMAP4_SSL if use_ssl else aioimaplib.IMAP4
    client = cls(host=cfg.host, port=cfg.port, timeout=10)
    try:
        await client.wait_hello_from_server()
        if cfg.security == "starttls":
            await client.starttls()
        r = await client.login(cfg.username, cfg.password)
        return r.result == "OK", "ok" if r.result == "OK" else f"AUTH: {r.result}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:80]}"
    finally:
        try: await client.logout()
        except Exception: pass  # noqa: BLE001, S110


async def channel_patrol() -> dict:
    if watchdog_state.is_disabled():
        return {"skipped": "circuit_breaker_open"}
    st = watchdog_state.state()
    settings = get_settings()
    healthy_email = 0
    broken_email = 0
    alive_linkedin = 0
    stale_linkedin = 0
    issues: list[str] = []

    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Channel).where(Channel.status == "active")
        )).scalars().all()

    sem = asyncio.Semaphore(4)

    async def _check_email(channel: Channel) -> None:
        nonlocal healthy_email, broken_email
        async with sem:
            try:
                cfg = SMTPConfig.model_validate(decrypt_json(channel.config_encrypted)["smtp"])
            except Exception as e:  # noqa: BLE001
                broken_email += 1
                issues.append(f"channel#{channel.id} bad config: {e}")
                return
            ok, detail = await _probe_smtp(cfg)
            if ok:
                healthy_email += 1
            else:
                broken_email += 1
                issues.append(f"channel#{channel.id} SMTP: {detail}")

    coros = []
    for ch in rows:
        if ch.channel_type == "email":
            coros.append(_check_email(ch))
        elif ch.channel_type == "linkedin":
            hb = ch.ext_last_heartbeat_at
            if hb and (_utcnow() - hb) < timedelta(minutes=30):
                alive_linkedin += 1
            else:
                stale_linkedin += 1
                last = hb.isoformat() if hb else "never"
                issues.append(f"linkedin channel#{ch.id} heartbeat stale (last: {last})")
    if coros:
        await asyncio.gather(*coros)

    st.email_channels_healthy = healthy_email
    st.email_channels_broken = broken_email
    st.linkedin_channels_alive = alive_linkedin
    st.linkedin_channels_stale = stale_linkedin
    st.last_channel_patrol = _utcnow()

    if broken_email > 0 or stale_linkedin > 0:
        watchdog_state.log_event(
            "channel_patrol", "issue",
            f"email_ok={healthy_email} email_broken={broken_email} "
            f"linkedin_alive={alive_linkedin} linkedin_stale={stale_linkedin}",
            issues=issues[:10],
        )
        # broken email or stale linkedin shouldn't trip the breaker — they're
        # config issues for the user to fix, not platform malfunctions
        watchdog_state.record_success()
    else:
        watchdog_state.log_event(
            "channel_patrol", "healthy",
            f"all {healthy_email + alive_linkedin} channels OK",
        )
        watchdog_state.record_success()
    return {
        "email_healthy": healthy_email, "email_broken": broken_email,
        "linkedin_alive": alive_linkedin, "linkedin_stale": stale_linkedin,
        "issues": issues,
    }


# ──────────────────────────────────────────────────────────────────────────
# Tier 3 — Stuck-state sweep (every 1 hour)
# ──────────────────────────────────────────────────────────────────────────
async def stuck_state_sweep() -> dict:
    """Find things that are stuck, auto-heal what we safely can.

    Auto-heal actions:
      - li_commands stuck `claimed` > 10 min  -> reset to pending (this is the
        bug from M7 — extension claimed but content script didn't report back).
      - reply/auto_reply/bounce events without sentiment > 5 min old -> re-fire
        classification.
      - enrolments with status='active' and next_send_at < now - 24h -> log only,
        leave for human review.
    """
    if watchdog_state.is_disabled():
        return {"skipped": "circuit_breaker_open"}
    st = watchdog_state.state()
    actions: list[str] = []

    async with SessionLocal() as session:
        # Stuck step_runs (recovery worker handles 'reserving' > 10 min — just count)
        stuck_runs = int(await session.scalar(text(
            "SELECT COUNT(*) FROM outreach.step_runs "
            "WHERE status='reserving' AND created_at < NOW() - INTERVAL '10 minutes'"
        )) or 0)

        # Stuck li_commands — actively heal
        stuck_cmds = await session.execute(text(
            "SELECT id, claimed_at FROM outreach.li_commands "
            "WHERE status='claimed' AND claimed_at < NOW() - INTERVAL '10 minutes' "
            "FOR UPDATE SKIP LOCKED"
        ))
        cmd_rows = stuck_cmds.all()
        if cmd_rows:
            ids = [r[0] for r in cmd_rows]
            await session.execute(text(
                "UPDATE outreach.li_commands "
                "SET status='pending', claimed_at=NULL "
                "WHERE id = ANY(:ids)"
            ), {"ids": ids})
            actions.append(f"reset {len(ids)} stuck li_commands to pending")

        # Commands that sat PENDING for days (extension uninstalled / channel
        # replaced) park their enrolment forever — heartbeat-wake only helps
        # `deferred_offline` leads, not ones with an already-minted command.
        # Expire them, fail the queued_external run, and reschedule the
        # enrolment so it re-enters the normal flow (which will defer it
        # offline-style until an extension actually reconnects).
        expired_rows = (await session.execute(text(
            "UPDATE outreach.li_commands "
            "   SET status='expired', completed_at=NOW(), "
            "       error_message='expired: not claimed within 72h (extension offline?)' "
            " WHERE status='pending' AND created_at < NOW() - INTERVAL '72 hours' "
            "RETURNING id, enrolment_id, step_id"
        ))).all()
        for _cmd_id, enr_id, st_id in expired_rows:
            await session.execute(text(
                "UPDATE outreach.step_runs SET status='failed', "
                "       error_message='li_command expired (extension offline >72h)' "
                " WHERE enrolment_id=:e AND step_id=:s AND status='queued_external'"
            ), {"e": enr_id, "s": st_id})
            await session.execute(text(
                "UPDATE outreach.enrolments "
                "   SET next_send_at = NOW() + INTERVAL '1 hour', updated_at=NOW() "
                " WHERE id=:e AND status='active' AND next_send_at IS NULL"
            ), {"e": enr_id})
        if expired_rows:
            actions.append(f"expired {len(expired_rows)} li_commands pending >72h")

        # Unstick permanently-parked enrolments. Two legacy/edge classes:
        #  a) awaiting_acceptance with NO deadline (rows written before the
        #     deadline logic existed) — give them the standard accept window so
        #     the gate can eventually time out instead of parking forever.
        deadline_days = get_settings().li_accept_timeout_days
        rewoken_awaiting = (await session.execute(text(
            "UPDATE outreach.enrolments "
            "   SET next_send_at = NOW() + (:days * INTERVAL '1 day'), updated_at=NOW() "
            " WHERE status='active' AND next_send_at IS NULL "
            "   AND runtime_state->>'awaiting_acceptance' = 'true' "
            "RETURNING id"
        ), {"days": deadline_days})).all()
        #  b) active, parked (next_send_at NULL), NOT awaiting acceptance, and
        #     nothing in flight (no pending/claimed command, no reserving/
        #     queued_external run) — orphans from crashed flows. Nothing in the
        #     system would EVER wake these; requeue them.
        rewoken_orphans = (await session.execute(text(
            "UPDATE outreach.enrolments e "
            "   SET next_send_at = NOW() + INTERVAL '1 minute', updated_at=NOW() "
            " WHERE e.status='active' AND e.next_send_at IS NULL "
            "   AND COALESCE(e.runtime_state->>'awaiting_acceptance','false') <> 'true' "
            "   AND NOT EXISTS (SELECT 1 FROM outreach.li_commands lc "
            "                    WHERE lc.enrolment_id = e.id AND lc.status IN ('pending','claimed')) "
            "   AND NOT EXISTS (SELECT 1 FROM outreach.step_runs sr "
            "                    WHERE sr.enrolment_id = e.id AND sr.status IN ('reserving','queued_external')) "
            "RETURNING e.id"
        ))).all()
        if rewoken_awaiting:
            actions.append(
                f"gave {len(rewoken_awaiting)} awaiting-acceptance enrolment(s) a missing deadline"
            )
        if rewoken_orphans:
            actions.append(f"requeued {len(rewoken_orphans)} orphan-parked enrolment(s)")

        # Failed li_commands matching DOM-fragility patterns — auto-retry up
        # to 2 times. We tag the error_message with [wd-retry xN] so we can
        # tell apart "this is a fresh failure" from "we already retried twice".
        # Patterns matched are the explicit guardrails the content script
        # throws when LinkedIn changes its DOM out from under us — exactly
        # the cases where a re-poll has a real chance of succeeding.
        retry_cmds = await session.execute(text(
            "SELECT id, error_message FROM outreach.li_commands lc "
            "WHERE status='failed' "
            "  AND completed_at > NOW() - INTERVAL '6 hours' "
            # Never resurrect a command whose enrolment is no longer active —
            # observed: a stopped (self-DM) enrolment's command was re-pended
            # and executed as a zombie.
            "  AND EXISTS (SELECT 1 FROM outreach.enrolments e "
            "               WHERE e.id = lc.enrolment_id AND e.status = 'active') "
            "  AND ("
            "        error_message ILIKE '%_not_found_even_after_heal%' "
            "     OR error_message ILIKE '%composer_remained_empty_after_insertion%' "
            "     OR error_message ILIKE '%sendDmButton_not_enabled_text_didnt_register%' "
            "     OR error_message ILIKE '%send_click_was_noop_composer_still_has_text%' "
            "     OR error_message ILIKE '%sendInvitationButton_disabled_note_didnt_register%' "
            "     OR error_message ILIKE '%dm_target_window_not_found%' "
            "     OR error_message ILIKE '%dm_target_name_unresolved%' "
            "     OR error_message ILIKE '%wrong_profile_page%' "
            "     OR error_message ILIKE '%profile_dom_mismatch%' "
            "     OR error_message ILIKE '%Receiving end does not exist%' "
            "     OR error_message ILIKE '%no_linkedin_tab_open%' "
            "     OR error_message ILIKE '%all_tabs_unresponsive%' "
            "  ) "
            "  AND COALESCE(error_message,'') NOT ILIKE '%[wd-retry x2]%' "
            "ORDER BY id ASC LIMIT 50 "
            "FOR UPDATE SKIP LOCKED"
        ))
        retry_rows = retry_cmds.all()
        retried = 0
        if retry_rows:
            for rid, err in retry_rows:
                tag = "[wd-retry x2]" if (err or "").find("[wd-retry x1]") != -1 else "[wd-retry x1]"
                await session.execute(text(
                    "UPDATE outreach.li_commands "
                    "SET status='pending', claimed_at=NULL, completed_at=NULL, "
                    "    error_message = :tag || ' ' || COALESCE(error_message,'') "
                    "WHERE id = :id"
                ), {"tag": tag, "id": rid})
                retried += 1
            actions.append(f"retried {retried} DOM-fragility failed li_commands")

        # Pro-actively trigger AI selector heal for intents that have failed
        # repeatedly in the current window. The healer's fresh selectors get
        # cached in chrome.storage so the very next poll uses them. This is
        # the watchdog↔healer feedback loop: failures observed in real time
        # by routes_extension.note_li_command_outcome surface here.
        try:
            fragile = watchdog_state.fragile_intents(min_failures=2)
        except Exception:  # noqa: BLE001
            fragile = []
        heal_intents: list[str] = []
        if fragile:
            from outreach.schemas.selector_heal import HealRequest
            from outreach.services import selector_healer as _healer
            # Map fragility error keys to healer intents.
            INTENT_MAP = {
                "messageButton_not_found_even_after_heal": "messageButton",
                "dm_target_window_not_found": "messageButton",
                "dm_target_name_unresolved": "messageButton",
                "composeEditor_not_found_even_after_heal": "composeEditor",
                "composer_remained_empty_after_insertion": "composeEditor",
                "sendDmButton_not_enabled_text_didnt_register": "sendDmButton",
                "sendDmButton_not_found_even_after_heal": "sendDmButton",
                "send_click_was_noop_composer_still_has_text": "sendDmButton",
                "connectButton_not_found_even_after_heal": "connectButton",
                "addNoteButton_not_found_even_after_heal": "addNoteButton",
                "noteTextarea_not_found_even_after_heal": "noteTextarea",
                "sendInvitationButton_not_found_even_after_heal": "sendInvitationButton",
                # New connect-flow guardrails (shadow-DOM invite modal):
                "sendInvitationButton_disabled_note_didnt_register": "noteTextarea",
                "moreButton_not_found_even_after_heal": "moreButton",
            }
            seen: set[str] = set()
            for key in fragile:
                intent = INTENT_MAP.get(key)
                if intent and intent not in seen:
                    seen.add(intent)
            for intent in seen:
                try:
                    req = HealRequest(
                        intent=intent, page_type="profile", url="",
                        failed_selectors=[], dom_snapshot="",
                    )
                    resp = await _healer.heal_selector(req)
                    if resp.selectors:
                        heal_intents.append(f"{intent}({len(resp.selectors)})")
                except Exception as e:  # noqa: BLE001
                    log.warning("preemptive heal for %s failed: %s", intent, e)
            if heal_intents:
                actions.append(f"preemptive heal: {', '.join(heal_intents)}")

        # Stuck enrolments
        stuck_enrol = int(await session.scalar(text(
            "SELECT COUNT(*) FROM outreach.enrolments "
            "WHERE status='active' AND next_send_at < NOW() - INTERVAL '24 hours'"
        )) or 0)

        # Unclassified events (reply / auto_reply / bounce older than 5 min)
        unclassified = (await session.execute(text(
            "SELECT ev.id FROM outreach.events ev "
            "LEFT JOIN outreach.reply_sentiment rs ON rs.event_id = ev.id "
            "WHERE rs.event_id IS NULL "
            "  AND ev.event_type IN ('reply','auto_reply','bounce') "
            "  AND ev.created_at < NOW() - INTERVAL '5 minutes' "
            "ORDER BY ev.id DESC LIMIT 50"
        ))).all()
        unclassified_ids = [r[0] for r in unclassified]
        await session.commit()

    if unclassified_ids:
        # Re-fire sentiment via the same background pathway
        try:
            from outreach.services import reply_processor
            for eid in unclassified_ids:
                reply_processor._spawn(reply_processor._bg_classify(eid))  # noqa: SLF001
            actions.append(f"requeued {len(unclassified_ids)} unclassified events for sentiment")
        except Exception as e:  # noqa: BLE001
            actions.append(f"requeue failed: {e}")

    # ── Connect → acceptance → DM gate: visibility (B) + stall alarm (C) ──
    # Own session so a malformed-JSON edge case can't poison the sweep TX.
    # awaiting_since is stored as an ISO string; we compare on the fixed 19-char
    # prefix (YYYY-MM-DDTHH:MM:SS) to avoid a fragile ::timestamptz cast.
    awaiting = past_deadline = awaiting_long = accepts_24h = 0
    try:
        async with SessionLocal() as s2:
            row = (await s2.execute(text(
                "SELECT "
                " COUNT(*) FILTER (WHERE runtime_state->>'awaiting_acceptance'='true') AS awaiting, "
                " COUNT(*) FILTER (WHERE runtime_state->>'awaiting_acceptance'='true' "
                "                  AND next_send_at < NOW()) AS past_deadline, "
                " COUNT(*) FILTER (WHERE runtime_state->>'awaiting_acceptance'='true' "
                "                  AND left(runtime_state->>'awaiting_since', 19) "
                "                      < to_char(NOW() - INTERVAL '24 hours', 'YYYY-MM-DD\"T\"HH24:MI:SS')) AS awaiting_long "
                "FROM outreach.enrolments WHERE status='active'"
            ))).first()
            awaiting = int((row[0] or 0)) if row else 0
            past_deadline = int((row[1] or 0)) if row else 0
            awaiting_long = int((row[2] or 0)) if row else 0
            accepts_24h = int(await s2.scalar(text(
                "SELECT COUNT(*) FROM outreach.events "
                "WHERE event_type='connection_accepted' AND occurred_at >= NOW() - INTERVAL '24 hours'"
            )) or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("acceptance-gate metrics query failed: %s", e)

    st.awaiting_acceptance = awaiting
    st.awaiting_past_deadline = past_deadline
    # Stall: leads have waited >24h but zero acceptances were detected in 24h →
    # the connection scanner is likely broken, or the operator hasn't opened
    # LinkedIn My Network. Surface it instead of silently skipping DMs later.
    stalled = awaiting_long > 0 and accepts_24h == 0
    st.acceptance_detection_stalled = stalled
    if stalled:
        watchdog_state.log_event(
            "stuck_state_sweep", "issue",
            f"acceptance detection may be stalled — {awaiting} lead(s) awaiting "
            f"({awaiting_long} for >24h), 0 accepts detected in last 24h. Open LinkedIn "
            f"My Network / notifications, or check the connectionCard scanner.",
            awaiting=awaiting, awaiting_long=awaiting_long,
            awaiting_past_deadline=past_deadline, accepts_24h=accepts_24h,
        )

    st.stuck_step_runs = stuck_runs
    st.stuck_li_commands = len(cmd_rows)
    st.stuck_enrolments = stuck_enrol
    st.unclassified_events = len(unclassified_ids)
    st.last_stuck_sweep = _utcnow()

    if cmd_rows or unclassified_ids or retried or expired_rows or rewoken_awaiting or rewoken_orphans:
        watchdog_state.log_event(
            "stuck_state_sweep", "healed", "; ".join(actions),
            stuck_step_runs=stuck_runs, stuck_li_commands=len(cmd_rows),
            stuck_enrolments=stuck_enrol, unclassified=len(unclassified_ids),
            retried_failed_li_commands=retried, expired_li_commands=len(expired_rows),
            rewoken_awaiting=len(rewoken_awaiting), rewoken_orphans=len(rewoken_orphans),
        )
    elif stuck_enrol > 0 or stuck_runs > 0:
        watchdog_state.log_event(
            "stuck_state_sweep", "issue",
            f"stuck step_runs={stuck_runs} enrolments={stuck_enrol} — no auto-action available",
        )
    else:
        watchdog_state.log_event("stuck_state_sweep", "healthy", "no stuck state")
    watchdog_state.record_success()
    return {
        "stuck_step_runs": stuck_runs,
        "stuck_li_commands": len(cmd_rows),
        "retried_failed_li_commands": retried,
        "stuck_enrolments": stuck_enrol,
        "unclassified_events": len(unclassified_ids),
        "awaiting_acceptance": awaiting,
        "awaiting_past_deadline": past_deadline,
        "acceptance_detection_stalled": stalled,
        "actions": actions,
    }


# ──────────────────────────────────────────────────────────────────────────
# Tier 4 — Deep verify (every 6 hours)
# ──────────────────────────────────────────────────────────────────────────
async def _probe_llm() -> tuple[bool, str]:
    """Probe the LLM the app actually uses — the active provider (DeepSeek when
    its key is set, else Anthropic). Avoids false alarms from the usage-capped
    Anthropic key when generation/sentiment all run on DeepSeek."""
    from outreach.services import llm_client

    provider = llm_client.active_provider()
    if provider is None:
        return False, "no LLM API key configured"
    try:
        r = await llm_client.complete(
            system="Reply with the single word OK.", user="OK", max_tokens=8,
        )
        return bool(r.text), f"{provider}: ok"
    except Exception as e:  # noqa: BLE001
        return False, f"{provider}: {type(e).__name__}: {str(e)[:80]}"


async def _probe_openai() -> tuple[bool, str]:
    settings = get_settings()
    if not settings.openai_api_key:
        return False, "no OPENAI_API_KEY"
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        r = await client.embeddings.create(
            model="text-embedding-3-small", input="ok",
        )
        n = len(r.data[0].embedding) if r.data else 0
        return n > 0, f"dim {n}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:80]}"


async def _probe_qdrant() -> tuple[bool, str]:
    """Probe the vector store the app actually uses for sequence RAG
    (qdrant_store — embedded MiniLM, in-process), not the legacy vault path."""
    from outreach.services import qdrant_store

    try:
        ok = await asyncio.to_thread(qdrant_store.is_available)
        return (ok, "reachable" if ok else "get_collections failed")
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:80]}"


async def deep_verify() -> dict:
    if watchdog_state.is_disabled():
        return {"skipped": "circuit_breaker_open"}
    st = watchdog_state.state()
    a_ok, a_detail = await _probe_llm()
    o_ok, o_detail = await _probe_openai()
    q_ok, q_detail = await _probe_qdrant()
    st.anthropic_alive = a_ok
    st.openai_alive = o_ok
    st.qdrant_alive = q_ok
    st.last_deep_verify = _utcnow()
    issues = []
    if not a_ok: issues.append(f"Anthropic: {a_detail}")
    if not o_ok: issues.append(f"OpenAI: {o_detail}")
    if not q_ok: issues.append(f"Qdrant: {q_detail}")
    if issues:
        watchdog_state.log_event(
            "deep_verify", "issue", "; ".join(issues),
            anthropic=a_detail, openai=o_detail, qdrant=q_detail,
        )
        watchdog_state.record_failure()
    else:
        watchdog_state.log_event(
            "deep_verify", "healthy", "all external dependencies OK",
            anthropic=a_detail, openai=o_detail, qdrant=q_detail,
        )
        watchdog_state.record_success()
    return {
        "anthropic": {"ok": a_ok, "detail": a_detail},
        "openai":    {"ok": o_ok, "detail": o_detail},
        "qdrant":    {"ok": q_ok, "detail": q_detail},
    }


# ──────────────────────────────────────────────────────────────────────────
# Tier 5 — Daily reset (every 24 hours)
# ──────────────────────────────────────────────────────────────────────────
async def daily_reset() -> dict:
    if watchdog_state.is_disabled():
        return {"skipped": "circuit_breaker_open"}
    st = watchdog_state.state()

    async with SessionLocal() as session:
        # Roll cap windows that have expired
        rolled = await session.execute(text(
            "UPDATE outreach.channels "
            "  SET sent_today = 0, "
            "      sent_today_window_start = NOW() "
            "WHERE sent_today_window_start IS NULL "
            "   OR NOW() >= sent_today_window_start + INTERVAL '1 day'"
        ))
        rolled_n = rolled.rowcount or 0

        # Sweep old upload stash files (no DB equivalent — files only — caller
        # cron should handle this; we just report count)
        # Summary: last 24h sends + replies
        summary_row = (await session.execute(text(
            "SELECT "
            "  (SELECT COUNT(*) FROM outreach.step_runs WHERE status='sent' AND sent_at >= NOW() - INTERVAL '24 hours') AS sent, "
            "  (SELECT COUNT(*) FROM outreach.events WHERE event_type='reply' AND occurred_at >= NOW() - INTERVAL '24 hours') AS replies, "
            "  (SELECT COUNT(*) FROM outreach.events WHERE event_type='bounce' AND occurred_at >= NOW() - INTERVAL '24 hours') AS bounces, "
            "  (SELECT COUNT(*) FROM outreach.reply_sentiment rs JOIN outreach.events ev ON ev.id=rs.event_id "
            "    WHERE rs.label IN ('positive','interested') AND ev.occurred_at >= NOW() - INTERVAL '24 hours') AS positive"
        ))).first()
        await session.commit()

    sent, replies, bounces, positive = (
        int(summary_row[0] or 0), int(summary_row[1] or 0),
        int(summary_row[2] or 0), int(summary_row[3] or 0),
    )

    failed_li = st.linkedin_command_failures_today
    st.detections_today = 0
    st.last_daily_reset = _utcnow()
    # Roll the per-intent + success/failure counters so the rolling 24h
    # window is accurate (failed_li snapshot above preserves the value for
    # the summary message).
    try:
        watchdog_state.reset_li_failure_counters()
    except Exception:  # noqa: BLE001
        pass

    msg = (
        f"24h summary — sent={sent} replies={replies} positive={positive} "
        f"bounces={bounces} li_command_failures={failed_li}; rolled {rolled_n} channel cap window(s)"
    )
    watchdog_state.log_event("daily_reset", "healthy", msg)
    watchdog_state.record_success()
    return {
        "sent_24h": sent, "replies_24h": replies, "positive_24h": positive,
        "bounces_24h": bounces, "channels_rolled": rolled_n,
    }
