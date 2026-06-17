"""M9 smoke test: AI follow-up drafter.

1. Set up: send step 1 + receive a "send me pricing" interested reply so the
   lead has real conversation history and vault content.
2. Call followup_agent.draft_for_lead() directly. Assert:
     - LLM returned a subject + body that personalises (mentions company name)
     - vault retrieval returned at least one snippet
     - notes string is populated
3. Call the /api/followups/draft endpoint and verify the same.
4. Call /api/followups/send to actually send the drafted email via fake SMTP
   and verify the recipient receives it.

Run with:
    backend\\.venv\\Scripts\\python.exe scripts\\smoke_m9.py
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
            "body": m.get_body(preferencelist=("plain",)).get_content() if m.get_body(preferencelist=("plain",)) else "",
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


async def amain(handler: CapturingHandler) -> None:
    from sqlalchemy import text
    from outreach.db import SessionLocal
    from outreach.services import reply_processor
    from outreach.services.email_parse import parse_inbound
    from outreach.services.followup_agent import draft_for_lead
    from outreach.workers import dispatcher

    async with httpx.AsyncClient(timeout=30.0) as c:
        await c.delete(f"{BASE}/api/leads", params={"confirm": "true"})
        for s in (await c.get(f"{BASE}/api/sequences")).json():
            await c.delete(f"{BASE}/api/sequences/{s['id']}")
        for ch in (await c.get(f"{BASE}/api/channels")).json():
            await c.delete(f"{BASE}/api/channels/{ch['id']}")
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM outreach.suppressions"))
        await s.execute(text("DELETE FROM outreach.reply_sentiment"))
        await s.execute(text("DELETE FROM outreach.vault_index"))
        await s.commit()

    async with httpx.AsyncClient(timeout=30.0) as c:
        await c.post(f"{BASE}/api/channels/email", json={
            "display_label": "Fake SMTP",
            "daily_cap": 100,
            "smtp": {
                "host": "127.0.0.1", "port": 2525, "security": "none",
                "username": "", "password": "",
                "from_email": "vimarsh@coherent.com",
                "from_name": "Vimarsh @ Coherent",
            },
            "imap": None,
        })

        r = await c.post(f"{BASE}/api/sequences", json={
            "name": "M9 smoke", "timezone": "UTC",
            "send_window_start": "00:00:00", "send_window_end": "23:59:00",
            "send_days_mask": 127,
        })
        sid = r.json()["id"]
        # Two steps: step 1 cold opener, step 2 follow-up.
        await c.post(f"{BASE}/api/sequences/{sid}/steps", json={
            "channel": "email", "subject": "Quick chat about {{company}}?",
            "body": "Hi {{first_name}},\n\nWe help teams at {{company}} ship faster. Open to a 15-min chat?\n\nBest,\nVimarsh",
        })
        await c.post(f"{BASE}/api/sequences/{sid}/steps", json={
            "channel": "email", "subject": "Quick follow-up", "delay_days": 2,
            "body": "Hi {{first_name}},\n\nWanted to make sure my note didn't get lost. Happy to send a 1-pager.\n\nBest,\nVimarsh",
        })
        await c.post(f"{BASE}/api/sequences/{sid}/status", json={"status": "active"})

        r = await c.post(f"{BASE}/api/leads", json={
            "email": "alex@acme-robotics.com",
            "first_name": "Alex", "last_name": "Rivera",
            "company": "Acme Robotics", "title": "VP Engineering",
        })
        lead_id = r.json()["id"]
        await c.post(f"{BASE}/api/sequences/{sid}/enrol", json={"lead_ids": [lead_id]})

    print("== send step 1 ==")
    res = await dispatcher.tick()
    assert res["sent"] == 1, res
    sent_mid = handler.messages[0]["message_id"]
    print(f"  sent: {handler.messages[0]['subject']!r}")

    print("\n== process inbound interested reply ==")
    parsed = parse_inbound(build_reply(
        sent_mid,
        from_addr="Alex Rivera <alex@acme-robotics.com>",
        subject="Re: Quick chat about Acme Robotics?",
        body="Thanks for reaching out. Could you send pricing? We're evaluating tools like this for our Q3 planning.",
        mid="<interested-reply-1@example.com>",
    ))
    async with SessionLocal() as s:
        await reply_processor.process(s, parsed)
    await reply_processor.wait_for_background()
    print("  sentiment + vault background tasks done")

    print("\n== A. direct draft_for_lead() call ==")
    async with SessionLocal() as s:
        draft = await draft_for_lead(
            s, lead_id,
            template_subject="Quick follow-up", template_body=(
                "Hi {{first_name}},\n\nWanted to make sure my note didn't get lost. "
                "Happy to send a 1-pager.\n\nBest,\nVimarsh"
            ),
        )
    print(f"  model: {draft.model}")
    print(f"  prior_history_count: {draft.prior_history_count}")
    print(f"  similar_snippets retrieved: {len(draft.similar_snippets)}")
    print(f"  notes: {draft.notes}")
    print(f"  --- SUBJECT ---\n  {draft.subject}")
    print(f"  --- BODY ---")
    for line in draft.body.splitlines()[:15]:
        print(f"  {line}")
    assert draft.prior_history_count >= 1, "should have at least the SENT step and the inbound reply"
    assert len(draft.similar_snippets) >= 1, "vault should have surfaced at least one snippet"
    assert draft.notes, "agent should provide notes"
    assert draft.model != "fallback", f"agent fell back: {draft.notes}"
    # The draft should reference the lead in some way.
    text_blob = (draft.subject + "\n" + draft.body).lower()
    mentions_company = "acme" in text_blob or "robotics" in text_blob
    mentions_pricing = "pricing" in text_blob or "1-pager" in text_blob or "one pager" in text_blob or "1 pager" in text_blob
    assert mentions_company or mentions_pricing, "draft should ground in the lead/conversation"

    print("\n== B. /api/followups/draft endpoint ==")
    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.post(f"{BASE}/api/followups/draft", json={
            "lead_id": lead_id, "sequence_id": sid,
        })
        r.raise_for_status()
        data = r.json()
        print(f"  endpoint model: {data['model']}")
        print(f"  subject: {data['subject']}")
        print(f"  notes: {data['notes']}")
        assert data["model"] != "fallback"

    print("\n== C. /api/followups/send actually delivers ==")
    before = len(handler.messages)
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f"{BASE}/api/followups/send", json={
            "lead_id": lead_id,
            "to_email": "alex@acme-robotics.com",
            "subject": data["subject"],
            "body": data["body"],
        })
        r.raise_for_status()
        send_result = r.json()
    print(f"  send result: ok={send_result['ok']} detail={send_result['detail']} mid={send_result.get('provider_message_id')}")
    assert send_result["ok"], send_result
    assert len(handler.messages) == before + 1
    delivered = handler.messages[-1]
    print(f"  delivered subject: {delivered['subject']!r}")
    print(f"  delivered to: {delivered['to']}")
    assert delivered["to"] == ["alex@acme-robotics.com"]
    assert delivered["subject"] == data["subject"]

    print("\n== cleanup ==")
    async with httpx.AsyncClient() as c:
        for s in (await c.get(f"{BASE}/api/sequences")).json():
            await c.delete(f"{BASE}/api/sequences/{s['id']}")
        await c.delete(f"{BASE}/api/leads", params={"confirm": "true"})
        for ch in (await c.get(f"{BASE}/api/channels")).json():
            await c.delete(f"{BASE}/api/channels/{ch['id']}")
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM outreach.suppressions"))
        await s.commit()

    print("\nALL M9 ASSERTIONS PASSED")


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
