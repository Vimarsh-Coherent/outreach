"""End-to-end verification of the new agent system (read-only).

Runs the EXACT SQL the new extension endpoints use, dry-runs the startup agent's
checks, and confirms the HTTP routes are live.
"""
import io, sys, urllib.request, urllib.error
from pathlib import Path
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
env = {}
for line in io.open(ROOT / "backend" / ".env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k.strip()] = v.strip()
e = create_engine(env.get("SYNC_DATABASE_URL") or env["DATABASE_URL"].replace("+asyncpg", "+psycopg2"))


def hdr(t): print("\n" + "=" * 64 + "\n" + t + "\n" + "=" * 64)

with e.connect() as c:
    uid = c.execute(text("SELECT user_id FROM outreach.channels WHERE channel_type='linkedin' AND status='active' ORDER BY id DESC LIMIT 1")).scalar()
    print("active linkedin channel user_id =", uid)

    hdr("1. /api/extension/campaigns  (what the dashboard shows)")
    rows = c.execute(text("""
        SELECT s.id, s.name, s.status,
               COUNT(DISTINCT e.id) AS enrolled,
               COUNT(DISTINCT lc.id) FILTER (WHERE lc.command_type='connect' AND lc.status='done') AS connects,
               COUNT(DISTINCT ev.id) FILTER (WHERE ev.event_type='connection_accepted') AS accepted,
               COUNT(DISTINCT lc.id) FILTER (WHERE lc.command_type='dm' AND lc.status='done') AS dms,
               COUNT(DISTINCT ev.id) FILTER (WHERE ev.event_type='reply') AS replied
          FROM outreach.sequences s
          LEFT JOIN outreach.enrolments e ON e.sequence_id=s.id
          LEFT JOIN outreach.li_commands lc ON lc.enrolment_id=e.id
          LEFT JOIN outreach.events ev ON ev.enrolment_id=e.id
         WHERE s.user_id=:uid GROUP BY s.id,s.name,s.status ORDER BY s.id DESC
    """), {"uid": uid}).all()
    for r in rows:
        print(f"  [{r[2]:8}] {r[1][:22]:22} leads={r[3]} invited={r[4]} accepted={r[5]} dms={r[6]} replied={r[7]}")

    hdr("2. /api/extension/activity  (live log, last 8)")
    for r in c.execute(text("SELECT id,command_type,status,target_li_url,error_message FROM outreach.li_commands WHERE user_id=:uid ORDER BY id DESC LIMIT 8"), {"uid": uid}):
        who = (r[3] or "").replace("https://www.linkedin.com/in/", "")
        print(f"  cmd#{r[0]} {r[1]:7} {r[2]:8} {who[:30]:30} {r[4] or ''}")

    hdr("3. /api/extension/status  (queue counts)")
    r = c.execute(text("""
        SELECT COUNT(*) FILTER (WHERE status='pending'), COUNT(*) FILTER (WHERE status='claimed'),
               COUNT(*) FILTER (WHERE status='done' AND completed_at::date=NOW()::date),
               COUNT(*) FILTER (WHERE status='failed' AND completed_at::date=NOW()::date)
          FROM outreach.li_commands WHERE user_id=:uid
    """), {"uid": uid}).first()
    ch = c.execute(text("SELECT status,sent_today,daily_cap,ext_last_heartbeat_at FROM outreach.channels WHERE channel_type='linkedin' AND status='active' ORDER BY id DESC LIMIT 1")).first()
    print(f"  queue: pending={r[0]} claimed={r[1]} done_today={r[2]} failed_today={r[3]}")
    print(f"  channel: {ch[0]} cap={ch[1]}/{ch[2]} heartbeat={ch[3]}")

hdr("4. HTTP endpoints live? (expect 401 = registered + auth-protected)")
for p in ["/api/extension/campaigns", "/api/extension/activity", "/api/extension/status"]:
    try:
        urllib.request.urlopen("http://127.0.0.1:8000" + p, timeout=4); print(f"  {p} -> 200 (?)")
    except urllib.error.HTTPError as ex:
        print(f"  {p} -> {ex.code}", "OK" if ex.code == 401 else "")
    except Exception as ex:
        print(f"  {p} -> DOWN ({ex})")

hdr("5. Startup agent dry-run (no launch)")
sys.path.insert(0, str(ROOT / "agent"))
import coherent_agent as ag
cfg = ag.load_config()
print("  config repo_dir :", cfg["repo_dir"])
print("  chrome found    :", ag.find_chrome(cfg) or "NOT FOUND")
print("  backend venv py :", (Path(cfg['backend_dir'])/'.venv'/'Scripts'/'python.exe').exists())
print("  extension dir   :", Path(cfg["extension_dir"]).exists(), cfg["extension_dir"])
print("  internet now    :", ag.have_internet())
print("  backend port up :", ag.port_open(cfg["backend_host"], cfg["backend_port"]))
print("\nDone.")
