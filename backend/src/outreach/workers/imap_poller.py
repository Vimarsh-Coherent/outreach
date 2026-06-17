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
from outreach.services.email_parse import parse_inbound
from outreach.utils.crypto import decrypt_json

log = logging.getLogger("outreach.imap")


def _imap_date_since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


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
        sel = await client.select(cfg.mailbox)
        if sel.result != "OK":
            return {**counts, "error": f"select:{sel.result}"}

        since = _imap_date_since(search_days)
        # We can't fully filter by header in standard IMAP SEARCH, so we use SINCE +
        # narrowing by HEADER on either In-Reply-To or Content-Type. Some servers
        # don't index Content-Type; we fall back to plain SINCE.
        trace_host = settings.email_trace_host
        candidate_queries = [
            f'(SINCE {since} HEADER "In-Reply-To" "{trace_host}")',
            f'(SINCE {since} HEADER "References" "{trace_host}")',
            f'(SINCE {since} HEADER "Content-Type" "delivery-status")',
            # Last-resort full sweep — bounded by SINCE.
            f'(SINCE {since})',
        ]
        uids: set[bytes] = set()
        for q in candidate_queries[:3]:  # try targeted first
            r = await client.search(q)
            if r.result == "OK" and r.lines:
                for u in (r.lines[0] or b"").split():
                    uids.add(u)
            if len(uids) >= 200:
                break
        if not uids:
            # Server didn't support targeted; do bounded sweep only when targeted gave 0.
            r = await client.search(candidate_queries[-1])
            if r.result == "OK" and r.lines:
                for u in (r.lines[0] or b"").split()[:200]:
                    uids.add(u)

        for uid in list(uids)[:200]:
            r = await client.uid("fetch", uid.decode(), "(RFC822)")
            if r.result != "OK" or not r.lines:
                counts["errors"] += 1
                continue
            raw = b""
            for line in r.lines:
                if isinstance(line, (bytes, bytearray)) and len(line) > 0:
                    raw += line
            if not raw:
                counts["errors"] += 1
                continue
            counts["fetched"] += 1
            parsed = parse_inbound(raw)
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
