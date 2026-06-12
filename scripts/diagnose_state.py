"""Read-only diagnostic: why aren't LinkedIn connect requests being sent?

Queries the live DB and prints the state of every link in the chain:
channels → sequences → steps → enrolments → li_commands.
"""
import os
import re
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

# Load DATABASE creds from backend/.env
env_path = os.path.join(os.path.dirname(__file__), "..", "backend", ".env")
env = {}
with open(env_path, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

url = env.get("SYNC_DATABASE_URL") or env.get("DATABASE_URL", "").replace("+asyncpg", "+psycopg2")
engine = create_engine(url)
now = datetime.now(timezone.utc)


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


with engine.connect() as c:
    section("LINKEDIN CHANNELS (auth + heartbeat)")
    rows = c.execute(text(
        "SELECT id, status, ext_last_heartbeat_at, sent_today, daily_cap "
        "FROM outreach.channels WHERE channel_type='linkedin' ORDER BY id"
    )).all()
    if not rows:
        print("  [X] NO linkedin channel — extension has nothing to auth against.")
    for r in rows:
        hb = r[2]
        age = f"{(now - hb).total_seconds()/60:.1f} min ago" if hb else "NEVER"
        fresh = hb and (now - hb).total_seconds() < 3600
        print(f"  channel#{r[0]} status={r[1]} heartbeat={age} "
              f"{'(FRESH [OK])' if fresh else '(STALE [X])'} sent_today={r[3]}/{r[4]}")

    section("SEQUENCES")
    rows = c.execute(text(
        "SELECT id, name, status FROM outreach.sequences ORDER BY id"
    )).all()
    for r in rows:
        flag = "[OK]" if r[2] == "active" else "[X] NOT ACTIVE"
        print(f"  seq#{r[0]} '{r[1]}' status={r[2]} {flag}")
        steps = c.execute(text(
            "SELECT step_order, channel, delay_days, delay_hours FROM outreach.sequence_steps "
            "WHERE sequence_id=:s ORDER BY step_order"
        ), {"s": r[0]}).all()
        for s in steps:
            print(f"       step {s[0]}: {s[1]} (delay {s[2]}d {s[3]}h)")

    section("ENROLMENTS")
    rows = c.execute(text(
        "SELECT id, sequence_id, status, current_step_order, next_send_at, "
        "       contact_snapshot->>'linkedin_url', runtime_state "
        "FROM outreach.enrolments ORDER BY id"
    )).all()
    if not rows:
        print("  [X] NO enrolments — the leads were never added to a sequence.")
    for r in rows:
        nsa = r[4]
        due = "DUE NOW [OK]" if (nsa and nsa <= now) else (f"due {nsa}" if nsa else "next_send_at=NULL (parked)")
        liurl = r[5] or "[X] NO linkedin_url"
        print(f"  enrol#{r[0]} seq={r[1]} status={r[2]} step={r[3]} {due}")
        print(f"        linkedin_url={liurl}")
        print(f"        runtime_state={r[6]}")

    section("LI_COMMANDS (the queue the extension polls)")
    rows = c.execute(text(
        "SELECT id, status, command_type, created_at, error_message "
        "FROM outreach.li_commands ORDER BY id DESC LIMIT 20"
    )).all()
    if not rows:
        print("  (none) — backend has not queued any connect commands yet.")
    for r in rows:
        print(f"  cmd#{r[0]} {r[2]} status={r[1]} created={r[3]} err={r[4]}")

print("\nDone.")
