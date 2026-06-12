"""M6 smoke test: sentiment classifier + VectorVault indexer.

Two phases:
A. Unit-test `classify_reply()` against 5 sample bodies of known sentiment.
   Real Anthropic calls — flake-tolerant: we assert the label falls within an
   accepted set rather than a single value, since LLMs vary at the boundary.
B. Integration: build a reply via reply_processor, await background tasks,
   verify reply_sentiment row exists, verify vault_index watermark advanced.

Costs: ~5 Haiku calls + ~6 OpenAI embeddings (~10 cents total).

Run with:
    backend\\.venv\\Scripts\\python.exe scripts\\smoke_m6.py
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

SAMPLES: list[tuple[str, str, set[str]]] = [
    # (label_id, reply_body, set_of_acceptable_labels)
    ("positive_meeting",
     "Hi - this is great timing. Can we set up a 30-min call next week? "
     "I have Tuesday or Thursday afternoon open. Looking forward to it.",
     {"positive", "interested"}),
    ("interested_send_info",
     "Thanks for reaching out. Could you send me a brief deck or pricing? "
     "We might revisit this next quarter.",
     {"interested", "positive"}),
    ("objection_price",
     "Honestly the pricing looks way above our budget for tooling like this. "
     "Maybe in a year if we 3x our team.",
     {"objection", "negative"}),
    ("unsubscribe_clear",
     "Please remove me from your mailing list and don't contact me again. Thanks.",
     {"unsubscribe"}),
    ("neutral_thanks",
     "Got it, thanks.",
     {"neutral", "interested"}),
]


def build_reply_bytes(
    thread_mid: str, *, from_addr: str, subject: str, body: str, mid: str,
) -> bytes:
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


async def amain(handler: CapturingHandler) -> None:
    from sqlalchemy import select, text
    from outreach.db import SessionLocal
    from outreach.models.event import ReplySentiment
    from outreach.models.vault_index import VaultIndex
    from outreach.services import reply_processor, sentiment
    from outreach.services.email_parse import parse_inbound
    from outreach.workers import dispatcher

    print("== A. Unit-test sentiment classifier ==")
    pass_count = 0
    for label_id, body, accepted in SAMPLES:
        label, confidence, reasoning = await sentiment.classify_reply(
            reply_body=body,
            original_subject="Quick chat about your team?",
            original_body="Hi Alex, I'm reaching out because Acme is hiring...",
            inbound_subject=f"Re: Quick chat",
            inbound_from="alex@example.com",
        )
        ok = label in accepted
        flag = "PASS" if ok else "FAIL"
        print(f"  [{flag}] {label_id}: got '{label}' ({confidence:.2f}) expected in {accepted}")
        print(f"        reasoning: {reasoning[:120]}")
        if ok:
            pass_count += 1
    print(f"  {pass_count}/{len(SAMPLES)} samples classified correctly")
    assert pass_count >= 4, f"sentiment classifier too unreliable: {pass_count}/{len(SAMPLES)} passed"

    print("\n== B. Integration: reply -> background sentiment + vault ==")
    async with httpx.AsyncClient(timeout=30.0) as c:
        # Wipe state
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
                "from_email": "sender@coherent.com", "from_name": "Coherent",
            },
            "imap": None,
        })

        r = await c.post(f"{BASE}/api/sequences", json={
            "name": "M6 smoke", "timezone": "UTC",
            "send_window_start": "00:00:00", "send_window_end": "23:59:00",
            "send_days_mask": 127,
        })
        sid = r.json()["id"]
        await c.post(f"{BASE}/api/sequences/{sid}/steps", json={
            "channel": "email", "subject": "Quick chat about {{company}}?",
            "body": "Hi {{first_name}},\nA quick note about {{company}}.",
        })
        await c.post(f"{BASE}/api/sequences/{sid}/status", json={"status": "active"})

        r = await c.post(f"{BASE}/api/leads", json={
            "email": "alex@example.com", "first_name": "Alex",
            "company": "Acme", "title": "VP",
        })
        lead_id = r.json()["id"]
        await c.post(f"{BASE}/api/sequences/{sid}/enrol", json={"lead_ids": [lead_id]})

    print("\n  send step 1 ...")
    res = await dispatcher.tick()
    assert res["sent"] == 1
    sent_mid = handler.messages[0]["message_id"]
    print(f"  sent message-id: {sent_mid}")

    print("\n  process inbound positive reply ...")
    reply_bytes = build_reply_bytes(
        sent_mid,
        from_addr="Alex Rivera <alex@example.com>",
        subject="Re: Quick chat about Acme?",
        body="Yes - let's set up a call next Tuesday at 2pm. Looking forward to it.",
        mid="<positive-reply-1@example.com>",
    )
    parsed = parse_inbound(reply_bytes)
    assert parsed.kind == "reply"
    async with SessionLocal() as s:
        result = await reply_processor.process(s, parsed)
    print(f"  reply_processor: {result}")
    event_id = result["event_id"]

    print("\n  wait for background sentiment + vault tasks ...")
    await reply_processor.wait_for_background()

    print("\n  verify reply_sentiment row exists ...")
    async with SessionLocal() as s:
        row = await s.scalar(select(ReplySentiment).where(ReplySentiment.event_id == event_id))
        assert row is not None, "background sentiment did not write a row"
        print(f"  label={row.label} confidence={row.confidence:.2f} model={row.model}")
        print(f"  reasoning: {row.reasoning}")
        assert row.label in {"positive", "interested"}, f"unexpected label {row.label}"

    print("\n  verify vault_index watermark advanced ...")
    async with SessionLocal() as s:
        idx = await s.scalar(select(VaultIndex).where(VaultIndex.lead_id == lead_id))
        if idx is None:
            print("  WARN: no vault_index row (OPENAI key missing or vault unavailable)")
        else:
            print(f"  vault_index: vault_name={idx.vault_name} watermark={idx.indexed_event_count} last_indexed_at={idx.last_indexed_at}")
            assert idx.indexed_event_count > 0

    print("\n  /api/leads/{id}/timeline ...")
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/api/leads/{lead_id}/timeline")
        r.raise_for_status()
        data = r.json()
        kinds = [i["kind"] for i in data["items"]]
        sent_items = [i for i in data["items"] if i["kind"] == "sent"]
        reply_items = [i for i in data["items"] if i["kind"] == "reply"]
        assert sent_items and reply_items
        assert reply_items[0]["sentiment_label"] in {"positive", "interested"}
        print(f"  timeline kinds: {kinds}")
        print(f"  reply sentiment in timeline: {reply_items[0]['sentiment_label']} ({reply_items[0]['sentiment_confidence']:.2f})")

    print("\n  unsubscribe path -> auto-suppression ...")
    async with httpx.AsyncClient() as c:
        # New lead + new enrolment so existing reply doesn't get in the way.
        r = await c.post(f"{BASE}/api/leads", json={"email": "noreply.now@example.com", "first_name": "Una"})
        unsub_lead_id = r.json()["id"]
        await c.post(f"{BASE}/api/sequences/{sid}/enrol", json={"lead_ids": [unsub_lead_id]})
    res = await dispatcher.tick()
    assert res["sent"] == 1
    sent_mid_2 = handler.messages[-1]["message_id"]
    unsub_bytes = build_reply_bytes(
        sent_mid_2,
        from_addr="Una <noreply.now@example.com>",
        subject="Re: Quick chat",
        body="Please remove me from this list and never email me again.",
        mid="<unsub-1@example.com>",
    )
    parsed = parse_inbound(unsub_bytes)
    async with SessionLocal() as s:
        await reply_processor.process(s, parsed)
    await reply_processor.wait_for_background()
    async with SessionLocal() as s:
        n = await s.scalar(text(
            "SELECT COUNT(*) FROM outreach.suppressions WHERE reason='unsubscribe'"
        ))
        print(f"  suppressions with reason='unsubscribe': {n}")
        assert int(n) >= 1, "unsubscribe should have created a suppression"

    print("\n== cleanup ==")
    async with httpx.AsyncClient() as c:
        for s in (await c.get(f"{BASE}/api/sequences")).json():
            await c.delete(f"{BASE}/api/sequences/{s['id']}")
        await c.delete(f"{BASE}/api/leads", params={"confirm": "true"})
        for ch in (await c.get(f"{BASE}/api/channels")).json():
            await c.delete(f"{BASE}/api/channels/{ch['id']}")
        async with SessionLocal() as session:
            await session.execute(text("DELETE FROM outreach.suppressions"))
            await session.commit()

    print("\nALL M6 ASSERTIONS PASSED")


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
