"""IMAP poller — every IMAP_POLL_INTERVAL_SECONDS, walk every active email
channel with IMAP configured, search for likely-relevant inbound messages, and
hand each off to the reply processor.

Strategy (per PLAN §8a):
  - Use IMAP SEARCH SINCE <recent>:  bounded scan window.
  - Combine with header filter:    only messages whose In-Reply-To/References
                                    contain "@<email_trace_host>" OR DSN reports.
  - Idempotency:                    events.external_id unique-on-conflict
                                    (the reply_processor handles it).
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from datetime import datetime, timedelta, timezone

import aioimaplib
from sqlalchemy import select

from outreach.config import get_settings
from outreach.db import SessionLocal
from outreach.models.channel import Channel
from outreach.schemas.channels import IMAPConfig
from outreach.services import reply_processor
from outreach.services.email_parse import NOREPLY_FROM_RX, parse_inbound
from outreach.utils.crypto import decrypt_json

log = logging.getLogger("outreach.imap")


def _imap_date_since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


async def _match_reply_by_sender(from_email: str, received_at: datetime) -> int | None:
    """Fallback for Gmail which rewrites Message-IDs on send.
    Finds the latest sent email step_run for the lead with this email address
    that was sent BEFORE the inbound reply arrived (prevents stale replies from
    matching new enrolments). A 1-hour tolerance handles sender-clock skew where
    the Date header appears slightly earlier than our sent_at."""
    from outreach.models.enrolment import Enrolment
    from outreach.models.lead import Lead
    from outreach.models.step_run import StepRun
    from datetime import timedelta

    async with SessionLocal() as session:
        result = await session.execute(
            select(StepRun.id)
            .join(Enrolment, StepRun.enrolment_id == Enrolment.id)
            .join(Lead, Enrolment.lead_id == Lead.id)
            .where(Lead.email == from_email.lower())
            .where(StepRun.channel == "email")
            .where(StepRun.status == "sent")
            .where(StepRun.sent_at <= received_at + timedelta(hours=1))
            .order_by(StepRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def _poll_channel(channel: Channel, search_days: int = 7) -> dict:
    settings = get_settings()
    try:
        cfg_dict = decrypt_json(channel.config_encrypted)
        imap_data = cfg_dict.get("imap")
        if not imap_data:
            return {"channel_id": channel.id, "skipped": "no_imap_config"}
        cfg = IMAPConfig.model_validate(imap_data)
    except Exception as e:  # noqa: BLE001
        log.warning("imap channel %d: bad config: %s", channel.id, e)
        return {"channel_id": channel.id, "error": f"bad_config:{e}"}

    use_ssl = cfg.security == "ssl_tls"
    client_cls = aioimaplib.IMAP4_SSL if use_ssl else aioimaplib.IMAP4
    client = client_cls(host=cfg.host, port=cfg.port, timeout=cfg.timeout_seconds)
    counts = {"channel_id": channel.id, "fetched": 0, "reply": 0, "bounce": 0, "auto_reply": 0, "unrelated": 0, "errors": 0}

    try:
        await client.wait_hello_from_server()
        if cfg.security == "starttls":
            await client.starttls()
        login_resp = await client.login(cfg.username, cfg.password)
        if login_resp.result != "OK":
            return {**counts, "error": f"login:{login_resp.result}"}

        # Quote mailbox names that contain spaces (e.g. "[Gmail]/All Mail").
        def _select_mailbox(name: str):
            if " " in name and not name.startswith('"'):
                name = f'"{name}"'
            return client.select(name)

        # Gmail routes emails to category tabs (Promotions, Updates, Social)
        # which don't appear in standard INBOX via IMAP. All Mail is the only
        # folder guaranteed to contain every received email on Gmail.
        # Try All Mail first; fall back to the configured mailbox for non-Gmail servers.
        sel = await _select_mailbox("[Gmail]/All Mail")
        if sel.result != "OK":
            sel = await _select_mailbox(cfg.mailbox)
        if sel.result != "OK":
            return {**counts, "error": f"select:{sel.result}"}

        since = _imap_date_since(search_days)
        trace_host = settings.email_trace_host
        uids: set[bytes] = set()

        # Use UID SEARCH so results are UIDs (not sequence numbers).
        # Sequence numbers differ from UIDs in All Mail — mixing them causes
        # uid fetch to return wrong/empty emails.
        async def _uid_search(q: str) -> None:
            r = await client.search(q)
            if r.result == "OK" and r.lines:
                for u in (r.lines[0] or b"").split():
                    uids.add(u)

        # Targeted searches (works on servers with proper HEADER search).
        for q in [
            f'(SINCE {since} HEADER "In-Reply-To" "{trace_host}")',
            f'(SINCE {since} HEADER "References" "{trace_host}")',
            f'(SINCE {since} HEADER "Content-Type" "delivery-status")',
        ]:
            await _uid_search(q)

        # Always also sweep the last 2 days — Gmail rewrites Message-IDs on outbound
        # SMTP so In-Reply-To won't contain our outreach.local token. Also catches
        # replies that land in non-Primary Gmail tabs (not visible in INBOX).
        await _uid_search(f"(SINCE {_imap_date_since(2)})")

        for uid in list(uids)[:200]:
            r = await client.fetch(uid.decode(), "(RFC822)")
            if r.result != "OK" or not r.lines:
                counts["errors"] += 1
                continue
            # aioimaplib returns literal octets (the actual email) as bytearray,
            # and IMAP framing ("7560 FETCH (RFC822 {6987}", ")") as bytes.
            # Only use bytearray lines so the framing prefix doesn't break parsing.
            raw = b""
            for line in r.lines:
                if isinstance(line, bytearray) and len(line) > 0:
                    raw += line
            if not raw:
                counts["errors"] += 1
                continue
            counts["fetched"] += 1
            parsed = parse_inbound(raw)

            # Sender-based fallback: Gmail replaces our Message-ID with its own, so the
            # HMAC match in parse_inbound fails and it returns "unrelated". If the From
            # address belongs to a lead we've emailed, treat it as their reply.
            if (
                parsed.kind == "unrelated"
                and parsed.from_addr
                and not NOREPLY_FROM_RX.search(parsed.from_addr)
            ):
                step_run_id = await _match_reply_by_sender(parsed.from_addr, parsed.received_at)
                if step_run_id is not None:
                    parsed.kind = "reply"
                    parsed.matched_step_run_id = step_run_id
                    log.debug("sender-match: %s -> step_run %d", parsed.from_addr, step_run_id)

            counts[parsed.kind] = counts.get(parsed.kind, 0) + 1
            if parsed.kind == "unrelated" or parsed.matched_step_run_id is None:
                continue
            async with SessionLocal() as session:
                try:
                    await reply_processor.process(session, parsed)
                except Exception:  # noqa: BLE001
                    log.exception("reply_processor crashed for uid=%s", uid)
                    counts["errors"] += 1
    except Exception as e:  # noqa: BLE001
        log.warning("imap channel %d poll failed: %s", channel.id, e)
        counts["error"] = f"{type(e).__name__}:{e}"
    finally:
        try:
            await client.logout()
        except Exception:  # noqa: BLE001, S110
            pass
    return counts


async def poll_all() -> dict:
    async with SessionLocal() as session:
        channels = (await session.execute(
            select(Channel).where(Channel.channel_type == "email", Channel.status == "active")
        )).scalars().all()
    if not channels:
        return {"channels": 0}
    settings = get_settings()
    semaphore = asyncio.Semaphore(4)

    async def _one(ch: Channel) -> dict:
        async with semaphore:
            try:
                return await _poll_channel(ch)
            except Exception as e:  # noqa: BLE001
                log.exception("imap poll crashed for channel %s", ch.id)
                return {"channel_id": ch.id, "error": f"crash:{e}"}

    results = await asyncio.gather(*[_one(c) for c in channels])
    fetched = sum(r.get("fetched", 0) for r in results)
    replies = sum(r.get("reply", 0) for r in results)
    bounces = sum(r.get("bounce", 0) for r in results)
    auto_replies = sum(r.get("auto_reply", 0) for r in results)
    log.info(
        "imap_poll: channels=%d fetched=%d replies=%d bounces=%d auto_replies=%d",
        len(channels), fetched, replies, bounces, auto_replies,
    )
    return {
        "channels": len(channels),
        "fetched": fetched,
        "replies": replies,
        "bounces": bounces,
        "auto_replies": auto_replies,
        "details": results,
    }
