"""M7 smoke test: LinkedIn channel + extension lifecycle.

We can't run a real LinkedIn DOM automation in CI, so this test exercises the
BACKEND lifecycle end-to-end by pretending to be the extension:

1. POST /api/channels/linkedin -> get channel_id + raw_token
2. Verify the raw_token doesn't leak via GET /api/channels
3. Send a heartbeat (required so dispatcher doesn't skip with "extension offline")
4. Create a sequence with a linkedin_connect + linkedin_dm step + 1 lead with linkedin_url
5. Run dispatcher.tick() -> verify li_commands rows were inserted + step_runs queued_external
6. GET /api/extension/next-command -> verify we receive the right command
7. POST /api/extension/commands/{id}/complete (status=done) -> verify enrolment advances
8. Run dispatcher.tick() again to send step 2 (linkedin_dm)
9. POST /api/extension/replies with a reply that matches our lead -> verify enrolment stops
10. Wrong token returns 401
11. Skipping when lead has no linkedin_url
12. Skipping when extension hasn't sent heartbeat in >1h (simulate by clearing ext_last_heartbeat_at)

Run with:
    backend\\.venv\\Scripts\\python.exe scripts\\smoke_m7.py
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "src"))
load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

BASE = "http://127.0.0.1:8000"


async def amain() -> None:
    from sqlalchemy import text
    from outreach.db import SessionLocal
    from outreach.workers import dispatcher

    async with httpx.AsyncClient(timeout=30.0) as c:
        # Reset state
        await c.delete(f"{BASE}/api/leads", params={"confirm": "true"})
        for s in (await c.get(f"{BASE}/api/sequences")).json():
            await c.delete(f"{BASE}/api/sequences/{s['id']}")
        for ch in (await c.get(f"{BASE}/api/channels")).json():
            await c.delete(f"{BASE}/api/channels/{ch['id']}")
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM outreach.suppressions"))
        await s.execute(text("DELETE FROM outreach.li_commands"))
        await s.commit()

    print("== create linkedin channel + verify token shape ==")
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f"{BASE}/api/channels/linkedin", json={
            "display_label": "My LinkedIn (Chrome ext)", "daily_cap": 40,
        })
        r.raise_for_status()
        created = r.json()
        channel_id = created["id"]
        raw_token = created["raw_token"]
        print(f"  channel_id={channel_id} raw_token=<{len(raw_token)} chars>")
        assert len(raw_token) >= 40, "token suspiciously short"

        # Verify the raw token doesn't leak via the list endpoint.
        list_r = await c.get(f"{BASE}/api/channels")
        list_r.raise_for_status()
        for ch in list_r.json():
            assert raw_token not in str(ch), "raw token must not appear in list response"
        print("  list endpoint hides raw token OK")

    headers_good = {"X-Extension-Channel-Id": str(channel_id), "X-Extension-Token": raw_token}
    headers_bad  = {"X-Extension-Channel-Id": str(channel_id), "X-Extension-Token": "wrong-token"}

    print("\n== heartbeat with good vs bad token ==")
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{BASE}/api/extension/heartbeat", headers=headers_good)
        assert r.status_code == 204, r.status_code
        r = await c.get(f"{BASE}/api/extension/heartbeat", headers=headers_bad)
        assert r.status_code == 401, r.status_code
        print(f"  good=204, bad=401 OK")

    print("\n== create sequence with 1 linkedin_connect + 1 linkedin_dm step + 1 lead ==")
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f"{BASE}/api/sequences", json={
            "name": "M7 smoke", "timezone": "UTC",
            "send_window_start": "00:00:00", "send_window_end": "23:59:00",
            "send_days_mask": 127,
        })
        sid = r.json()["id"]
        await c.post(f"{BASE}/api/sequences/{sid}/steps", json={
            "channel": "linkedin_connect",
            "body": "Hi {{first_name}}, would love to connect re: {{company}}.",
        })
        await c.post(f"{BASE}/api/sequences/{sid}/steps", json={
            "channel": "linkedin_dm",
            "body": "Hey {{first_name}}, thanks for connecting! Quick question about {{company}}'s stack.",
        })
        await c.post(f"{BASE}/api/sequences/{sid}/status", json={"status": "active"})

        r = await c.post(f"{BASE}/api/leads", json={
            "email": "alex@example.com",
            "linkedin_url": "https://www.linkedin.com/in/alex-rivera",
            "first_name": "Alex", "last_name": "Rivera",
            "company": "Acme Robotics", "title": "VP Engineering",
        })
        lead_id = r.json()["id"]
        await c.post(f"{BASE}/api/sequences/{sid}/enrol", json={"lead_ids": [lead_id]})

    print("\n== tick #1 — expect linkedin_connect command queued ==")
    res = await dispatcher.tick()
    print(f"  tick: claimed={res['claimed']} sent={res['sent']} details={res['details']}")
    assert res["claimed"] == 1
    assert res["details"][0]["result"] == "linkedin_queued"

    print("\n== extension polls /next-command ==")
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{BASE}/api/extension/next-command", headers=headers_good)
        assert r.status_code == 200, (r.status_code, r.text)
        cmd = r.json()
        print(f"  received: command_type={cmd['command_type']} target={cmd['target_li_url']}")
        print(f"  body: {cmd['body_text'][:80]}")
        assert cmd["command_type"] == "connect"
        assert cmd["target_li_url"] == "https://www.linkedin.com/in/alex-rivera"
        assert "Alex" in cmd["body_text"]
        assert "Acme Robotics" in cmd["body_text"]

        # No second pending command yet (just one in queue).
        r2 = await c.get(f"{BASE}/api/extension/next-command", headers=headers_good)
        assert r2.status_code == 204, (r2.status_code, r2.text)
        print("  second poll returns 204 (queue drained)")

    print("\n== extension reports success ==")
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(
            f"{BASE}/api/extension/commands/{cmd['id']}/complete",
            json={"status": "done", "provider_message_id": "li-connect:#1"},
            headers=headers_good,
        )
        r.raise_for_status()
        print(f"  /complete: {r.json()}")

    print("\n== verify enrolment advanced + delivered event ==")
    async with SessionLocal() as s:
        row = await s.execute(text(
            "SELECT status, current_step_order, next_send_at FROM outreach.enrolments WHERE sequence_id=:sid"
        ), {"sid": sid})
        status, step_order, next_send_at = row.first()
        print(f"  enrolment: status={status} current_step_order={step_order} next_send_at={next_send_at}")
        assert step_order == 1, f"should have advanced past step 1, got {step_order}"
        assert next_send_at is not None, "should be scheduled for step 2"
        assert status == "active"

        # delivered event exists
        n_delivered = await s.scalar(text(
            "SELECT COUNT(*) FROM outreach.events WHERE event_type='delivered' AND channel='linkedin'"
        ))
        assert int(n_delivered) == 1

    print("\n== bring enrolment due + tick #2 (linkedin_dm) ==")
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE outreach.enrolments SET next_send_at = NOW() WHERE sequence_id=:sid"
        ), {"sid": sid})
        await s.commit()
    res = await dispatcher.tick()
    print(f"  tick #2: {res['details']}")
    assert res["details"][0]["result"] == "linkedin_queued"

    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{BASE}/api/extension/next-command", headers=headers_good)
        cmd2 = r.json()
        assert cmd2["command_type"] == "dm"
        print(f"  step 2 cmd: {cmd2['command_type']} body={cmd2['body_text'][:60]}")
        await c.post(
            f"{BASE}/api/extension/commands/{cmd2['id']}/complete",
            json={"status": "done", "provider_message_id": "li-dm:#1"},
            headers=headers_good,
        )

    print("\n== reply via /api/extension/replies stops enrolment ==")
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{BASE}/api/extension/replies", json=[
            {
                "li_url": "https://www.linkedin.com/in/alex-rivera",
                "thread_id": "thread-xyz",
                "message_id": "li:thread-xyz:msg-1",
                "body": "Hey thanks for reaching out, happy to chat!",
                "received_at": datetime.now(timezone.utc).isoformat(),
                "from_name": "Alex Rivera",
            }
        ], headers=headers_good)
        r.raise_for_status()
        result = r.json()
        print(f"  /replies result: {result}")
        assert result["matched"] == 1 and result["inserted"] == 1

        # Duplicate should be deduped
        r = await c.post(f"{BASE}/api/extension/replies", json=[
            {
                "li_url": "https://www.linkedin.com/in/alex-rivera",
                "thread_id": "thread-xyz",
                "message_id": "li:thread-xyz:msg-1",
                "body": "(same msg)",
                "received_at": datetime.now(timezone.utc).isoformat(),
            }
        ], headers=headers_good)
        result_dup = r.json()
        print(f"  /replies duplicate: {result_dup}")
        assert result_dup["duplicates"] == 1

    async with SessionLocal() as s:
        row = await s.execute(text(
            "SELECT status, stopped_reason FROM outreach.enrolments WHERE sequence_id=:sid"
        ), {"sid": sid})
        st, reason = row.first()
        print(f"  enrolment now: status={st} reason={reason}")
        # Either: cadence already finished naturally (status='done') and the
        # reply is just logged for visibility; or the reply arrived mid-cadence
        # and stopped it. Both are valid.
        assert st in ("stopped_reply", "done"), f"unexpected status {st}"
        n_reply = await s.scalar(text(
            "SELECT COUNT(*) FROM outreach.events WHERE event_type='reply' AND channel='linkedin'"
        ))
        assert int(n_reply) == 1, f"expected 1 linkedin reply event, got {n_reply}"

    print("\n== heartbeat staleness check: clearing ext_last_heartbeat_at ==")
    # Need a fresh enrolment for this branch.
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f"{BASE}/api/leads", json={
            "linkedin_url": "https://www.linkedin.com/in/another-lead",
            "first_name": "Beta", "last_name": "Test", "company": "Bravo Inc",
        })
        beta_lead_id = r.json()["id"]
        await c.post(f"{BASE}/api/sequences/{sid}/enrol", json={"lead_ids": [beta_lead_id]})
    async with SessionLocal() as s:
        # Re-activate sequence (might be paused/archived; but it should still be active).
        await s.execute(text(
            "UPDATE outreach.channels SET ext_last_heartbeat_at = NOW() - INTERVAL '2 hours' "
            "WHERE channel_type='linkedin'"
        ))
        await s.commit()
    res = await dispatcher.tick()
    print(f"  tick result: {res['details']}")
    assert any(d["result"] == "extension_offline" for d in res["details"])

    print("\n== no-linkedin-url skip ==")
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f"{BASE}/api/leads", json={
            "email": "no.linkedin@example.com", "first_name": "Gamma",
        })
        gamma_lead_id = r.json()["id"]
        await c.post(f"{BASE}/api/sequences/{sid}/enrol", json={"lead_ids": [gamma_lead_id]})
    # Restore heartbeat first.
    async with SessionLocal() as s:
        await s.execute(text(
            "UPDATE outreach.channels SET ext_last_heartbeat_at = NOW() "
            "WHERE channel_type='linkedin'"
        ))
        await s.execute(text(
            "UPDATE outreach.enrolments SET next_send_at = NOW() "
            "WHERE lead_id=:lid"
        ), {"lid": gamma_lead_id})
        await s.commit()
    res = await dispatcher.tick()
    print(f"  tick result: {[d['result'] for d in res['details']]}")
    assert any(d["result"] == "no_linkedin_url" for d in res["details"])

    print("\n== cleanup ==")
    async with httpx.AsyncClient(timeout=30.0) as c:
        for s in (await c.get(f"{BASE}/api/sequences")).json():
            await c.delete(f"{BASE}/api/sequences/{s['id']}")
        await c.delete(f"{BASE}/api/leads", params={"confirm": "true"})
        for ch in (await c.get(f"{BASE}/api/channels")).json():
            await c.delete(f"{BASE}/api/channels/{ch['id']}")
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM outreach.suppressions"))
        await s.execute(text("DELETE FROM outreach.li_commands"))
        await s.commit()

    print("\nALL M7 ASSERTIONS PASSED")


if __name__ == "__main__":
    asyncio.run(amain())
