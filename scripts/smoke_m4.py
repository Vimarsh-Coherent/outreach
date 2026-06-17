"""M4 smoke test: end-to-end email send via in-process fake SMTP.

Steps:
1. Start aiosmtpd on 127.0.0.1:2525 (captures messages into a list).
2. POST a channel pointing at 127.0.0.1:2525 (security=none, no AUTH).
3. POST a sequence + add 2 email steps with delay=0.
4. POST 1 lead, enrol -> next_send_at is now.
5. Run dispatcher.tick() -> verify step 1 sent + Message-ID HMAC verifies.
6. Nudge next_send_at + run tick again -> verify step 2 threads off step 1.
7. Another tick -> enrolment status='done'.
8. Suppression check: enrolling a suppressed lead must not actually send.

Run with:
    backend\\.venv\\Scripts\\python.exe scripts\\smoke_m4.py
"""
import os as _os, sys as _sys
if _os.environ.get("OUTREACH_DESTRUCTIVE_SMOKE_OK") != "1":
    _sys.exit(
        "REFUSING TO RUN: this smoke test BULK-DELETES leads/sequences/channels "
        "in whatever database the backend is using (it wiped live data on "
        "2026-06-12). Run only against a disposable dev DB with "
        "OUTREACH_DESTRUCTIVE_SMOKE_OK=1.")
import asyncio
import json
import sys
from email.parser import BytesParser
from email.policy import default as email_default
from pathlib import Path

import httpx
from aiosmtpd.controller import Controller
from dotenv import load_dotenv

# Allow direct imports from outreach.*
sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "src"))
load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

BASE = "http://127.0.0.1:8000"


class CapturingHandler:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def handle_DATA(self, server, session, envelope):
        msg = BytesParser(policy=email_default).parsebytes(envelope.content)
        self.messages.append({
            "from": envelope.mail_from,
            "to": list(envelope.rcpt_tos),
            "subject": str(msg["Subject"]),
            "body": msg.get_body(preferencelist=("plain",)).get_content(),
            "message_id": str(msg["Message-ID"]) if msg["Message-ID"] else None,
            "in_reply_to": str(msg["In-Reply-To"]) if msg["In-Reply-To"] else None,
            "references": str(msg["References"]) if msg["References"] else None,
        })
        return "250 OK"


async def amain(handler: CapturingHandler) -> None:
    # Imports happen inside the async main so all async resources
    # bind to this event loop.
    from sqlalchemy import text
    from outreach.db import SessionLocal
    from outreach.services.identity import canonical_identity
    from outreach.services.suppressions_service import add_suppression
    from outreach.services.threading_email import parse_message_id
    from outreach.workers import dispatcher

    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{BASE}/health"); r.raise_for_status()
        assert r.json()["db"]

        # Wipe state from prior runs
        await c.delete(f"{BASE}/api/leads", params={"confirm": "true"})
        for s in (await c.get(f"{BASE}/api/sequences")).json():
            await c.delete(f"{BASE}/api/sequences/{s['id']}")
        for ch in (await c.get(f"{BASE}/api/channels")).json():
            await c.delete(f"{BASE}/api/channels/{ch['id']}")

        print("\n== create channel pointing at fake SMTP ==")
        r = await c.post(f"{BASE}/api/channels/email", json={
            "display_label": "Fake SMTP",
            "daily_cap": 100,
            "smtp": {
                "host": "127.0.0.1", "port": 2525, "security": "none",
                "username": "", "password": "",
                "from_email": "sender@coherent.com", "from_name": "Coherent Smoke",
            },
            "imap": None,
        })
        r.raise_for_status()
        channel_id = r.json()["id"]
        print(f"  channel_id={channel_id}")

        print("\n== create sequence + 2 email steps ==")
        r = await c.post(f"{BASE}/api/sequences", json={
            "name": "M4 smoke", "timezone": "UTC",
            "send_window_start": "00:00:00", "send_window_end": "23:59:00",
            "send_days_mask": 127,
        })
        sid = r.json()["id"]
        for step in [
            {"channel": "email", "subject": "Hi {{first_name}} from {{company|Coherent}}",
             "body": "Hello {{first_name}},\n\nQuick note about {{company}}."},
            {"channel": "email", "subject": "Re: Hi {{first_name}}",
             "body": "Following up — any thoughts?"},
        ]:
            (await c.post(f"{BASE}/api/sequences/{sid}/steps", json=step)).raise_for_status()
        await c.post(f"{BASE}/api/sequences/{sid}/status", json={"status": "active"})

        print("\n== add lead + enrol ==")
        r = await c.post(f"{BASE}/api/leads", json={
            "email": "target@example.com",
            "first_name": "Alex", "last_name": "Rivera",
            "company": "Sirius Robotics", "title": "VP Eng",
        })
        lead_id = r.json()["id"]
        r = await c.post(f"{BASE}/api/sequences/{sid}/enrol", json={"lead_ids": [lead_id]})
        print(f"  enrolled: {r.json()}")

    print("\n== run tick (step 1) ==")
    result = await dispatcher.tick()
    print(json.dumps(result, indent=2, default=str))
    assert result["sent"] == 1, f"expected 1 sent, got {result}"
    assert len(handler.messages) == 1
    m1 = handler.messages[0]
    print(f"  to={m1['to']} subject={m1['subject']!r}")
    print(f"  message-id={m1['message_id']}")
    assert m1["to"] == ["target@example.com"]
    assert m1["subject"] == "Hi Alex from Sirius Robotics"
    assert "Hello Alex" in m1["body"]
    assert "Sirius Robotics" in m1["body"]
    parsed_run = parse_message_id(m1["message_id"])
    assert parsed_run is not None, "Message-ID HMAC did not verify"
    print(f"  Message-ID HMAC OK -> step_run_id={parsed_run}")
    assert m1["in_reply_to"] is None
    assert m1["references"] is None

    async def force_due(seq_id: int) -> None:
        async with SessionLocal() as session:
            await session.execute(text(
                "UPDATE outreach.enrolments SET next_send_at = NOW() "
                "WHERE sequence_id=:sid AND status='active'"
            ), {"sid": seq_id})
            await session.commit()

    await force_due(sid)
    print("\n== run tick (step 2) ==")
    result = await dispatcher.tick()
    print(json.dumps(result, indent=2, default=str))
    assert result["sent"] == 1, result
    assert len(handler.messages) == 2
    m2 = handler.messages[1]
    print(f"  subject={m2['subject']!r}")
    print(f"  in-reply-to={m2['in_reply_to']}")
    print(f"  references={m2['references']}")
    assert m2["in_reply_to"] == m1["message_id"], "Step 2 should thread off step 1"
    assert m1["message_id"] in (m2["references"] or "")

    print("\n== one more tick (no more steps -> done) ==")
    await force_due(sid)
    result = await dispatcher.tick()
    print(json.dumps(result, indent=2, default=str))
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/api/sequences/{sid}/enrolments")
        e = r.json()["items"][0]
        print(f"  enrolment status: {e['status']}, current_step_order: {e['current_step_order']}")
        assert e["status"] == "done"

    print("\n== suppression check ==")
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BASE}/api/leads", json={"email": "blocked@example.com", "first_name": "Bob"})
        blocked_id = r.json()["id"]
    ident = canonical_identity(email="blocked@example.com")
    async with SessionLocal() as session:
        await add_suppression(session, 1, ident.hash, "email", "unsubscribe")
        await session.commit()
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BASE}/api/sequences", json={
            "name": "Suppression check", "timezone": "UTC",
            "send_window_start": "00:00:00", "send_window_end": "23:59:00",
            "send_days_mask": 127,
        })
        sid2 = r.json()["id"]
        await c.post(f"{BASE}/api/sequences/{sid2}/steps", json={
            "channel": "email", "subject": "x", "body": "x",
        })
        await c.post(f"{BASE}/api/sequences/{sid2}/status", json={"status": "active"})
        await c.post(f"{BASE}/api/sequences/{sid2}/enrol", json={"lead_ids": [blocked_id]})

    before = len(handler.messages)
    result = await dispatcher.tick()
    print(f"  tick result: {result['sent']} sent, captured before={before} after={len(handler.messages)}")
    assert len(handler.messages) == before, "suppressed lead should NOT receive email"
    async with SessionLocal() as s:
        r = await s.execute(text(
            "SELECT status, error_message FROM outreach.step_runs ORDER BY id DESC LIMIT 1"
        ))
        row = r.first()
        print(f"  last step_run: status={row[0]} error={row[1]}")
        assert row[0] == "skipped"

    print("\n== cleanup ==")
    async with httpx.AsyncClient() as c:
        for s in (await c.get(f"{BASE}/api/sequences")).json():
            await c.delete(f"{BASE}/api/sequences/{s['id']}")
        await c.delete(f"{BASE}/api/leads", params={"confirm": "true"})
        for ch in (await c.get(f"{BASE}/api/channels")).json():
            await c.delete(f"{BASE}/api/channels/{ch['id']}")

    print("\nALL M4 ASSERTIONS PASSED")


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
