"""M5 smoke test — reply / bounce / OOO / unrelated classification.

We don't run a real IMAP server. Instead we exercise the same code path the
poller would: build raw rfc822 bytes for each case and call
`parse_inbound` + `reply_processor.process` directly.

Steps:
1. Create channel / sequence / lead / enrol.
2. Run dispatcher.tick() to send step 1 (we need a real step_run + Message-ID).
3. Build an inbound message with In-Reply-To pointing at step_run.id's HMAC ID.
4. Run reply_processor:
     - reply -> enrolment stopped, status='stopped_reply', event emitted.
5. Build a DSN bounce against the same enrolment (re-enrol another lead first).
     - bounce -> enrolment stopped, suppression added, event emitted.
6. Build an OOO message.
     - auto_reply -> event emitted, enrolment NOT stopped.
7. Build an unrelated message (no HMAC In-Reply-To).
     - unrelated -> ignored, no event.

Run with:
    backend\\.venv\\Scripts\\python.exe scripts\\smoke_m5.py
"""
import os as _os, sys as _sys
if _os.environ.get("OUTREACH_DESTRUCTIVE_SMOKE_OK") != "1":
    _sys.exit(
        "REFUSING TO RUN: this smoke test BULK-DELETES leads/sequences/channels "
        "in whatever database the backend is using (it wiped live data on "
        "2026-06-12). Run only against a disposable dev DB with "
        "OUTREACH_DESTRUCTIVE_SMOKE_OK=1.")
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
from aiosmtpd.controller import Controller
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "src"))
load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

BASE = "http://127.0.0.1:8000"


class CapturingHandler:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def handle_DATA(self, server, session, envelope):
        from email.parser import BytesParser
        from email.policy import default as email_default
        m = BytesParser(policy=email_default).parsebytes(envelope.content)
        self.messages.append({
            "to": list(envelope.rcpt_tos),
            "subject": str(m["Subject"]),
            "message_id": str(m["Message-ID"]),
        })
        return "250 OK"


def build_reply(thread_mid: str, *, from_addr: str, subject: str, body: str, mid: str) -> bytes:
    return (
        f"From: {from_addr}\r\n"
        f"To: sender@coherent.com\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: {mid}\r\n"
        f"In-Reply-To: {thread_mid}\r\n"
        f"References: {thread_mid}\r\n"
        f"Date: Wed, 29 May 2026 14:00:00 +0000\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"{body}\r\n"
    ).encode("utf-8")


def build_ooo(thread_mid: str, *, from_addr: str, mid: str) -> bytes:
    return (
        f"From: {from_addr}\r\n"
        f"To: sender@coherent.com\r\n"
        f"Subject: Out of office until June 5\r\n"
        f"Auto-Submitted: auto-replied\r\n"
        f"Message-ID: {mid}\r\n"
        f"In-Reply-To: {thread_mid}\r\n"
        f"Date: Wed, 29 May 2026 14:05:00 +0000\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"I am out of the office until June 5 with limited email access.\r\n"
    ).encode("utf-8")


def build_dsn(original_mid: str, *, original_recipient: str, mid: str) -> bytes:
    boundary = "===DSN-BOUNDARY==="
    body = (
        f"From: Mail Delivery Subsystem <mailer-daemon@example.com>\r\n"
        f"To: sender@coherent.com\r\n"
        f"Subject: Undeliverable: outreach send failed\r\n"
        f"Message-ID: {mid}\r\n"
        f"Date: Wed, 29 May 2026 14:10:00 +0000\r\n"
        f"Content-Type: multipart/report; report-type=delivery-status; boundary={boundary}\r\n"
        f"\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"Your message could not be delivered to {original_recipient}.\r\n"
        f"\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: message/delivery-status\r\n"
        f"\r\n"
        f"Reporting-MTA: dns; example.com\r\n"
        f"\r\n"
        f"Final-Recipient: rfc822; {original_recipient}\r\n"
        f"Action: failed\r\n"
        f"Status: 5.1.1\r\n"
        f"Original-Message-ID: {original_mid}\r\n"
        f"\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: message/rfc822\r\n"
        f"\r\n"
        f"From: sender@coherent.com\r\n"
        f"To: {original_recipient}\r\n"
        f"Subject: original\r\n"
        f"Message-ID: {original_mid}\r\n"
        f"In-Reply-To: {original_mid}\r\n"
        f"\r\n"
        f"original body\r\n"
        f"\r\n"
        f"--{boundary}--\r\n"
    )
    return body.encode("utf-8")


def build_unrelated() -> bytes:
    return (
        b"From: friend@example.com\r\n"
        b"To: sender@coherent.com\r\n"
        b"Subject: lunch?\r\n"
        b"Message-ID: <lunch-1@example.com>\r\n"
        b"Date: Wed, 29 May 2026 14:15:00 +0000\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Hi, want to grab lunch tomorrow?\r\n"
    )


async def amain(handler: CapturingHandler) -> None:
    from sqlalchemy import select, text
    from outreach.db import SessionLocal
    from outreach.models.event import Event
    from outreach.models.suppression import Suppression
    from outreach.services import reply_processor
    from outreach.services.email_parse import parse_inbound
    from outreach.services.identity import canonical_identity
    from outreach.workers import dispatcher

    async with httpx.AsyncClient(timeout=30.0) as c:
        # Reset state
        await c.delete(f"{BASE}/api/leads", params={"confirm": "true"})
        for s in (await c.get(f"{BASE}/api/sequences")).json():
            await c.delete(f"{BASE}/api/sequences/{s['id']}")
        for ch in (await c.get(f"{BASE}/api/channels")).json():
            await c.delete(f"{BASE}/api/channels/{ch['id']}")

    # Suppressions don't have an API endpoint yet — clear directly.
    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM outreach.suppressions"))
        await session.commit()

    async with httpx.AsyncClient(timeout=30.0) as c:

        r = await c.post(f"{BASE}/api/channels/email", json={
            "display_label": "Fake SMTP",
            "daily_cap": 100,
            "smtp": {
                "host": "127.0.0.1", "port": 2525, "security": "none",
                "username": "", "password": "",
                "from_email": "sender@coherent.com", "from_name": "Coherent",
            },
            "imap": None,
        })
        r.raise_for_status()

        r = await c.post(f"{BASE}/api/sequences", json={
            "name": "M5 smoke", "timezone": "UTC",
            "send_window_start": "00:00:00", "send_window_end": "23:59:00",
            "send_days_mask": 127,
        })
        sid = r.json()["id"]
        await c.post(f"{BASE}/api/sequences/{sid}/steps", json={
            "channel": "email", "subject": "Hi {{first_name}}",
            "body": "Hello {{first_name}},\nQuick note about {{company}}.",
        })
        # Add a second step so we can verify a stopped enrolment really stops.
        await c.post(f"{BASE}/api/sequences/{sid}/steps", json={
            "channel": "email", "subject": "Follow-up",
            "body": "Bumping this in case you missed it.",
            "delay_days": 0, "delay_hours": 0,
        })
        await c.post(f"{BASE}/api/sequences/{sid}/status", json={"status": "active"})

        # Three leads: one we'll reply to, one we'll bounce on, one we'll OOO.
        ids: dict[str, int] = {}
        for label, payload in [
            ("reply", {"email": "alex@example.com", "first_name": "Alex", "company": "Acme"}),
            ("bounce", {"email": "blocked@example.com", "first_name": "Beta", "company": "Bravo"}),
            ("ooo", {"email": "vacation@example.com", "first_name": "Yuki", "company": "DreamCloud"}),
        ]:
            r = await c.post(f"{BASE}/api/leads", json=payload)
            ids[label] = r.json()["id"]
        r = await c.post(f"{BASE}/api/sequences/{sid}/enrol",
                         json={"lead_ids": list(ids.values())})
        print(f"enrolled: {r.json()}")

    # Fire 3 ticks to send step 1 to all three leads.
    print("\n== tick (send step 1 to all 3) ==")
    res = await dispatcher.tick()
    print(f"  {res['sent']} sent")
    assert res["sent"] == 3, res
    assert len(handler.messages) == 3
    msgs = {m["to"][0]: m["message_id"] for m in handler.messages}
    print(f"  message-ids: {msgs}")

    # Look up step_runs by their generated message_id.
    async def _check(query_email: str) -> tuple[int, int, bytes]:
        async with SessionLocal() as session:
            r = await session.execute(text(
                "SELECT e.id, sr.id, e.identity_hash FROM outreach.step_runs sr "
                "JOIN outreach.enrolments e ON e.id = sr.enrolment_id "
                "WHERE sr.provider_message_id = :mid"
            ), {"mid": msgs[query_email]})
            row = r.first()
            return int(row[0]), int(row[1]), row[2]
    reply_enrol_id, reply_run_id, _ = await _check("alex@example.com")
    bounce_enrol_id, _bounce_run_id, _ = await _check("blocked@example.com")
    ooo_enrol_id, _ooo_run_id, _ = await _check("vacation@example.com")

    print("\n== process REPLY ==")
    reply_bytes = build_reply(
        msgs["alex@example.com"],
        from_addr="Alex Rivera <alex@example.com>",
        subject="Re: Hi Alex",
        body="Sure, let's chat next week. — Alex",
        mid="<reply-alex-1@example.com>",
    )
    parsed = parse_inbound(reply_bytes)
    print(f"  parsed kind={parsed.kind} matched_run={parsed.matched_step_run_id}")
    assert parsed.kind == "reply" and parsed.matched_step_run_id == reply_run_id
    async with SessionLocal() as session:
        result = await reply_processor.process(session, parsed)
    print(f"  processor result: {result}")
    assert result["action"] == "stopped_reply"
    # Confirm enrolment status.
    async with SessionLocal() as session:
        s_r = await session.execute(text("SELECT status FROM outreach.enrolments WHERE id=:id"), {"id": reply_enrol_id})
        assert s_r.scalar_one() == "stopped_reply"

    print("\n== process BOUNCE ==")
    dsn = build_dsn(
        msgs["blocked@example.com"],
        original_recipient="blocked@example.com",
        mid="<bounce-blocked-1@example.com>",
    )
    parsed = parse_inbound(dsn)
    print(f"  parsed kind={parsed.kind} matched_run={parsed.matched_step_run_id} final_recipient={parsed.bounce_detail}")
    assert parsed.kind == "bounce"
    async with SessionLocal() as session:
        result = await reply_processor.process(session, parsed)
    print(f"  processor result: {result}")
    assert result["action"] == "stopped_bounce"
    async with SessionLocal() as session:
        s_r = await session.execute(text("SELECT status FROM outreach.enrolments WHERE id=:id"), {"id": bounce_enrol_id})
        assert s_r.scalar_one() == "stopped_bounce"
        # Suppression added
        ident = canonical_identity(email="blocked@example.com")
        sup = await session.execute(
            select(Suppression).where(Suppression.identity_hash == ident.hash, Suppression.channel == "email")
        )
        assert sup.scalar_one_or_none() is not None
    print("  suppression added OK")

    print("\n== process OOO ==")
    ooo = build_ooo(
        msgs["vacation@example.com"],
        from_addr="Yuki Tanaka <vacation@example.com>",
        mid="<ooo-yuki-1@example.com>",
    )
    parsed = parse_inbound(ooo)
    print(f"  parsed kind={parsed.kind}")
    assert parsed.kind == "auto_reply", parsed.kind
    async with SessionLocal() as session:
        result = await reply_processor.process(session, parsed)
    print(f"  processor result: {result}")
    assert result["action"] == "logged"
    async with SessionLocal() as session:
        s_r = await session.execute(text("SELECT status FROM outreach.enrolments WHERE id=:id"), {"id": ooo_enrol_id})
        st = s_r.scalar_one()
        print(f"  OOO enrolment status remains: {st}")
        assert st == "active"

    print("\n== process UNRELATED ==")
    parsed = parse_inbound(build_unrelated())
    assert parsed.kind == "unrelated"
    print("  classified unrelated, no DB writes")

    print("\n== process duplicate REPLY (idempotency) ==")
    parsed = parse_inbound(reply_bytes)
    async with SessionLocal() as session:
        result = await reply_processor.process(session, parsed)
    print(f"  processor result: {result}")
    assert result["action"] == "duplicate_event"

    print("\n== confirm OOO enrolment was NOT advanced (still due) ==")
    # OOO step 2 should still be pending; force_due + tick should send step 2 to Yuki only.
    async with SessionLocal() as session:
        await session.execute(text(
            "UPDATE outreach.enrolments SET next_send_at = NOW() WHERE id=:id"
        ), {"id": ooo_enrol_id})
        await session.commit()
    before = len(handler.messages)
    res = await dispatcher.tick()
    print(f"  tick result: {res}")
    print(f"  captured: before={before} after={len(handler.messages)}")
    assert len(handler.messages) == before + 1
    assert handler.messages[-1]["to"] == ["vacation@example.com"]

    print("\n== cleanup ==")
    async with httpx.AsyncClient() as c:
        for s in (await c.get(f"{BASE}/api/sequences")).json():
            await c.delete(f"{BASE}/api/sequences/{s['id']}")
        await c.delete(f"{BASE}/api/leads", params={"confirm": "true"})
        for ch in (await c.get(f"{BASE}/api/channels")).json():
            await c.delete(f"{BASE}/api/channels/{ch['id']}")
        # Sweep our suppressions too
        async with SessionLocal() as session:
            await session.execute(text("DELETE FROM outreach.suppressions"))
            await session.commit()

    print("\nALL M5 ASSERTIONS PASSED")


def main() -> int:
    handler = CapturingHandler()
    controller = Controller(handler, hostname="127.0.0.1", port=2525)
    controller.start()
    print("== fake SMTP listening on 127.0.0.1:2525 ==")
    try:
        asyncio.run(amain(handler))
        return 0
    finally:
        controller.stop()


if __name__ == "__main__":
    sys.exit(main())
