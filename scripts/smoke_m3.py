"""M3 smoke test:
1. Unit-test send_window.next_valid_slot against the worked-examples table from PLAN.md.
2. End-to-end: create sequence -> add 3 steps -> upload+enrol a few leads -> list -> archive.

Run with:
    backend\.venv\Scripts\python.exe scripts\smoke_m3.py
"""
import os as _os, sys as _sys
if _os.environ.get("OUTREACH_DESTRUCTIVE_SMOKE_OK") != "1":
    _sys.exit(
        "REFUSING TO RUN: this smoke test BULK-DELETES leads/sequences/channels "
        "in whatever database the backend is using (it wiped live data on "
        "2026-06-12). Run only against a disposable dev DB with "
        "OUTREACH_DESTRUCTIVE_SMOKE_OK=1.")
import json
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "src"))
from outreach.services.send_window import next_valid_slot

BASE = "http://127.0.0.1:8000"
SAMPLE = Path(__file__).parent / "sample_leads.csv"
TZ = "Asia/Kolkata"
WS, WE = time(9, 0), time(18, 0)
MASK_WEEKDAYS = 31  # Mon-Fri


def to_local(dt_utc: datetime) -> str:
    return dt_utc.astimezone(ZoneInfo(TZ)).strftime("%a %Y-%m-%d %H:%M")


def make_local_utc(y: int, mo: int, d: int, h: int, m: int = 0) -> datetime:
    return datetime(y, mo, d, h, m, tzinfo=ZoneInfo(TZ))


def test_send_window() -> bool:
    # 2026-06-01 is a Monday. We use it as our reference Monday for the table.
    Mon = lambda h, m=0: make_local_utc(2026, 6, 1, h, m)
    Tue = lambda h, m=0: make_local_utc(2026, 6, 2, h, m)
    Fri = lambda h, m=0: make_local_utc(2026, 6, 5, h, m)
    Sat = lambda h, m=0: make_local_utc(2026, 6, 6, h, m)
    NextMon = lambda h, m=0: make_local_utc(2026, 6, 8, h, m)

    cases = [
        # (label, base, dd, dh, expected_local)
        ("Mon 10:00 + 0d 0h",  Mon(10),  0, 0, Mon(10)),
        ("Mon 17:00 + 0d 2h -> next morning", Mon(17), 0, 2, Tue(9)),
        ("Fri 17:00 + 0d 2h -> next Mon morning", Fri(17), 0, 2, NextMon(9)),
        ("Sat 10:00 + 0d 0h -> next Mon morning", Sat(10), 0, 0, NextMon(9)),
        ("Mon 10:00 + 1d 0h", Mon(10), 1, 0, Tue(10)),
        ("Mon 10:00 + 0d 6h -> Mon 16:00", Mon(10), 0, 6, Mon(16)),
    ]
    ok = True
    for label, base, dd, dh, expected in cases:
        result = next_valid_slot(
            base=base, delay_days=dd, delay_hours=dh,
            tz_name=TZ, window_start=WS, window_end=WE, days_mask=MASK_WEEKDAYS,
        )
        match = result == expected
        flag = "OK" if match else "FAIL"
        print(f"  [{flag}] {label}: got {to_local(result)} expected {to_local(expected)}")
        ok = ok and match
    return ok


def main() -> int:
    print("== send_window unit tests ==")
    if not test_send_window():
        print("send_window tests failed")
        return 1

    with httpx.Client(timeout=30.0) as c:
        print("\n== /health ==")
        r = c.get(f"{BASE}/health"); r.raise_for_status()

        print("\n== create sequence ==")
        r = c.post(f"{BASE}/api/sequences", json={
            "name": "Smoke test cadence",
            "description": "Created by scripts/smoke_m3.py",
            "timezone": TZ,
        })
        r.raise_for_status()
        seq = r.json()
        sid = seq["id"]
        print(json.dumps(seq, indent=2, default=str))

        print("\n== add 3 steps ==")
        for step in [
            {"channel": "email", "subject": "Quick intro from Coherent", "body": "Hi {{first_name}}, ..."},
            {"channel": "linkedin_connect", "body": "Hi {{first_name}}, would love to connect re: {{company}}."},
            {"channel": "linkedin_dm", "body": "Hey {{first_name}}, did you see the earlier note?", "delay_days": 2},
        ]:
            r = c.post(f"{BASE}/api/sequences/{sid}/steps", json=step)
            r.raise_for_status()
            s = r.json()
            print(f"  added step {s['step_order']} ({s['channel']}) - delay {s['delay_days']}d {s['delay_hours']}h")

        print("\n== add step with oversize body (expect 422) ==")
        r = c.post(f"{BASE}/api/sequences/{sid}/steps", json={
            "channel": "linkedin_connect", "body": "x" * 400,
        })
        print(f"  status={r.status_code} (expected 422): {'OK' if r.status_code == 422 else 'FAIL'}")

        print("\n== activate without leads (should succeed) ==")
        r = c.post(f"{BASE}/api/sequences/{sid}/status", json={"status": "active"})
        r.raise_for_status()
        print(f"  status now: {r.json()['status']}")

        print("\n== upload leads ==")
        with SAMPLE.open("rb") as f:
            r = c.post(f"{BASE}/api/leads/upload/preview", files={"file": (SAMPLE.name, f, "text/csv")})
        prev = r.json()
        r = c.post(f"{BASE}/api/leads/upload/commit", json={
            "token": prev["token"], "mapping": prev["suggested_mapping"], "source": "smoke_m3",
        }); r.raise_for_status()

        r = c.get(f"{BASE}/api/leads", params={"limit": 100}); r.raise_for_status()
        leads = r.json()["items"]
        print(f"  have {len(leads)} leads")
        lead_ids = [l["id"] for l in leads]

        print("\n== enrol all leads ==")
        r = c.post(f"{BASE}/api/sequences/{sid}/enrol", json={"lead_ids": lead_ids})
        r.raise_for_status()
        print(json.dumps(r.json(), indent=2))

        print("\n== re-enrol same leads (expect deduped) ==")
        r = c.post(f"{BASE}/api/sequences/{sid}/enrol", json={"lead_ids": lead_ids})
        r.raise_for_status()
        print(json.dumps(r.json(), indent=2))

        print("\n== list enrolments ==")
        r = c.get(f"{BASE}/api/sequences/{sid}/enrolments", params={"limit": 5})
        r.raise_for_status()
        data = r.json()
        print(f"  total: {data['total']}")
        for e in data["items"]:
            print(f"  enrol#{e['id']} step={e['current_step_order']} next_send_at={e['next_send_at']} status={e['status']}")

        print("\n== sequence detail (with steps) ==")
        r = c.get(f"{BASE}/api/sequences/{sid}"); r.raise_for_status()
        det = r.json()
        print(f"  steps={det['step_count']} active_enrolments={det['active_enrolments']} status={det['status']}")

        print("\n== archive (auto-stops enrolments) ==")
        r = c.post(f"{BASE}/api/sequences/{sid}/status", json={"status": "archived"})
        r.raise_for_status()
        print(f"  status now: {r.json()['status']} active_enrolments={r.json()['active_enrolments']}")

        print("\n== cleanup ==")
        c.delete(f"{BASE}/api/sequences/{sid}")
        c.delete(f"{BASE}/api/leads", params={"confirm": "true"})

    return 0


if __name__ == "__main__":
    sys.exit(main())
