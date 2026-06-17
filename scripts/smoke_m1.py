"""Smoke test M1 (leads upload).

Run with:
    backend\.venv\Scripts\python.exe scripts\smoke_m1.py
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
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
SAMPLE = Path(__file__).parent / "sample_leads.csv"


def main() -> int:
    with httpx.Client(timeout=30.0) as c:
        print("== /health ==")
        r = c.get(f"{BASE}/health"); r.raise_for_status()
        print(json.dumps(r.json(), indent=2))

        print("\n== preview ==");
        with SAMPLE.open("rb") as f:
            r = c.post(f"{BASE}/api/leads/upload/preview", files={"file": (SAMPLE.name, f, "text/csv")})
        r.raise_for_status()
        prev = r.json()
        print(f"row_count: {prev['row_count']}")
        print(f"columns: {prev['columns']}")
        print(f"suggested_mapping: {json.dumps(prev['suggested_mapping'], indent=2)}")
        print(f"first row: {json.dumps(prev['sample_rows'][0], indent=2)}")

        print("\n== commit ==")
        r = c.post(f"{BASE}/api/leads/upload/commit", json={
            "token": prev["token"],
            "mapping": prev["suggested_mapping"],
            "source": "smoke_test",
        })
        r.raise_for_status()
        commit = r.json()
        print(json.dumps(commit, indent=2))

        print("\n== list ==")
        r = c.get(f"{BASE}/api/leads", params={"limit": 100})
        r.raise_for_status()
        data = r.json()
        print(f"total: {data['total']}")
        for lead in data["items"]:
            short = {k: lead.get(k) for k in ("id", "email", "phone", "linkedin_url", "first_name", "last_name", "company")}
            print(f"  {short}")

        print("\n== re-commit (idempotency check) ==")
        # Upload + commit the same file again. Expect: 0 inserted, 7 updated, 2 skipped.
        with SAMPLE.open("rb") as f:
            r = c.post(f"{BASE}/api/leads/upload/preview", files={"file": (SAMPLE.name, f, "text/csv")})
        prev2 = r.json()
        r = c.post(f"{BASE}/api/leads/upload/commit", json={
            "token": prev2["token"],
            "mapping": prev2["suggested_mapping"],
            "source": "smoke_test_2",
        })
        r.raise_for_status()
        print(json.dumps(r.json(), indent=2))

        print("\n== bulk delete ==")
        r = c.delete(f"{BASE}/api/leads", params={"confirm": "true"})
        r.raise_for_status()
        print(json.dumps(r.json(), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
