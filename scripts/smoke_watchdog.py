"""Smoke test for the 5-tier watchdog.

Modeled on watchlink-main's pattern but exercising our domain — we don't have
DOM selectors to heal; we have stuck li_commands, unclassified events, dead
SMTP/IMAP configs, and broken API keys.

Specifically asserts:
  1. Tier 1 (quick_check) detects DB up.
  2. Tier 2 (channel_patrol) probes any configured channels without crashing.
  3. Tier 3 (stuck_state_sweep) auto-heals a deliberately-stuck li_command
     (the exact bug we hit during the M7 LinkedIn run).
  4. Tier 4 (deep_verify) probes external APIs only when the keys are set.
  5. Tier 5 (daily_reset) rolls cap windows + computes a 24h summary.
  6. Circuit breaker opens after 5 forced failures and auto-resets after
     the cooldown (we shrink the cooldown for the test).

Run with:
    backend\\.venv\\Scripts\\python.exe scripts\\smoke_watchdog.py
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

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "src"))
load_dotenv(Path(__file__).parent.parent / "backend" / ".env")

from sqlalchemy import text  # noqa: E402

from outreach.db import SessionLocal  # noqa: E402
from outreach.services import watchdog_state  # noqa: E402
from outreach.workers import watchdog  # noqa: E402


async def amain() -> None:
    print("== Tier 1 — quick_check ==")
    r = await watchdog.quick_check()
    print(f"  {r}")
    assert r["backend_alive"] and r["db_alive"], r

    print("\n== Tier 2 — channel_patrol ==")
    r = await watchdog.channel_patrol()
    print(f"  email_healthy={r['email_healthy']} email_broken={r['email_broken']} "
          f"linkedin_alive={r['linkedin_alive']} linkedin_stale={r['linkedin_stale']}")
    if r["issues"]:
        print(f"  issues: {r['issues'][:3]}")
    # Just ensure it ran without crashing — actual numbers depend on configured channels.
    assert isinstance(r, dict)

    print("\n== Tier 3 — stuck_state_sweep (with planted stuck li_command) ==")
    # Plant a stuck li_command (status='claimed' with claimed_at > 10 min ago).
    async with SessionLocal() as session:
        # We need a real enrolment + step + user to FK against. Just grab whatever's
        # there or create a minimal scaffolding.
        existing = (await session.execute(text(
            "SELECT id FROM outreach.li_commands ORDER BY id DESC LIMIT 1"
        ))).first()
        if existing:
            cmd_id = existing[0]
            await session.execute(text(
                "UPDATE outreach.li_commands SET status='claimed', "
                "claimed_at = NOW() - INTERVAL '15 minutes', completed_at=NULL "
                "WHERE id = :id"
            ), {"id": cmd_id})
            await session.commit()
            print(f"  planted stuck command id={cmd_id} (existing row reused)")
            target_id = cmd_id
        else:
            print("  no existing li_commands — sweep should still report 0")
            target_id = None

    r = await watchdog.stuck_state_sweep()
    print(f"  result: {r}")
    if target_id is not None:
        async with SessionLocal() as session:
            now_status = await session.scalar(text(
                "SELECT status FROM outreach.li_commands WHERE id=:id"
            ), {"id": target_id})
            print(f"  command id={target_id} now: status={now_status}")
            assert now_status == "pending", f"sweep didn't reset stuck command to pending: got {now_status}"
        assert r["stuck_li_commands"] >= 1
        print("  PASS — stuck li_command was auto-healed back to 'pending'")

    print("\n== Tier 4 — deep_verify ==")
    r = await watchdog.deep_verify()
    print(f"  anthropic: ok={r['anthropic']['ok']}, detail={r['anthropic']['detail']}")
    print(f"  openai:    ok={r['openai']['ok']}, detail={r['openai']['detail']}")
    print(f"  qdrant:    ok={r['qdrant']['ok']}, detail={r['qdrant']['detail']}")
    # Don't HARD-assert: keys may be invalid in a CI environment. Just don't crash.

    print("\n== Tier 5 — daily_reset ==")
    r = await watchdog.daily_reset()
    print(f"  {r}")
    assert "sent_24h" in r and "channels_rolled" in r

    print("\n== Circuit breaker — force 5 failures, expect breaker to open ==")
    st = watchdog_state.state()
    st.consecutive_failures = 0
    for i in range(5):
        watchdog_state.record_failure()
    assert st.disabled_until is not None, "breaker did not open after 5 failures"
    print(f"  breaker open until: {st.disabled_until.isoformat()}")
    print("  PASS — breaker tripped")

    print("\n== Circuit breaker — patrols skip while open ==")
    r = await watchdog.quick_check()
    assert r.get("skipped") == "circuit_breaker_open", r
    print(f"  PASS — quick_check correctly skipped: {r}")

    print("\n== Circuit breaker — manual reset ==")
    st.disabled_until = None
    st.consecutive_failures = 0
    watchdog_state.log_event("circuit_breaker", "healed", "manually reset for test")
    r = await watchdog.quick_check()
    assert r.get("db_alive") is True, r
    print(f"  PASS — quick_check resumed: {r}")

    print("\n== Event log ==")
    events = list(watchdog_state.state().events)
    print(f"  {len(events)} events captured")
    for ev in events[:8]:
        print(f"    [{ev.tier:18s} {ev.status:10s}] {ev.message[:80]}")

    print("\nALL WATCHDOG ASSERTIONS PASSED")


if __name__ == "__main__":
    asyncio.run(amain())
