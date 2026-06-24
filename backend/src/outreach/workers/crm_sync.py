"""Outbound CRM sync — pushes new reply events (with sentiment) to connected
CRMs as a contact upsert + a logged note. Best-effort; the cursor on the
integration advances per event."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, text

from outreach.db import SessionLocal
from outreach.models.integration import CrmIntegration
from outreach.services import hubspot

log = logging.getLogger("outreach.crm_sync")


async def _sync_hubspot(session, integ: CrmIntegration) -> int:
    rows = (await session.execute(text("""
        SELECT e.id, e.payload, l.email, l.first_name, l.last_name, l.company, l.title, l.phone,
               rs.label AS sentiment
          FROM outreach.events e
          JOIN outreach.enrolments en ON en.id = e.enrolment_id
          LEFT JOIN outreach.leads l ON l.id = en.lead_id
          LEFT JOIN outreach.reply_sentiment rs ON rs.event_id = e.id
         WHERE en.user_id = :uid AND e.id > :cur AND e.event_type = 'reply'
         ORDER BY e.id
         LIMIT 50
    """), {"uid": integ.user_id, "cur": integ.last_event_id})).mappings().all()
    if not rows:
        return 0

    token = await hubspot.get_access_token(session, integ)
    mapping = integ.field_mapping or None
    synced = 0
    last = integ.last_event_id
    for r in rows:
        last = r["id"]
        if not (r["email"] or r["phone"]):
            continue
        lead = {
            "email": r["email"], "first_name": r["first_name"], "last_name": r["last_name"],
            "company": r["company"], "title": r["title"], "phone": r["phone"],
        }
        props = hubspot.lead_to_props(lead, mapping)
        if not props:
            continue
        try:
            cid = await hubspot.upsert_contact(token, props)
            if cid:
                snippet = (r["payload"] or {}).get("snippet", "")
                note = (
                    f"Coherent Outreach — lead replied "
                    f"(sentiment: {r['sentiment'] or 'unclassified'}).\n\n{snippet}"
                )
                await hubspot.log_note(token, cid, note)
                synced += 1
        except Exception:  # noqa: BLE001
            log.exception("hubspot sync failed for event %s", r["id"])
    integ.last_event_id = last
    integ.last_synced_at = datetime.now(timezone.utc)
    await session.commit()
    return synced


async def tick() -> dict:
    try:
        async with SessionLocal() as session:
            integs = (await session.execute(
                select(CrmIntegration).where(
                    CrmIntegration.provider == "hubspot",
                    CrmIntegration.status == "connected",
                )
            )).scalars().all()
            total = 0
            for integ in integs:
                total += await _sync_hubspot(session, integ)
            return {"synced": total, "integrations": len(integs)}
    except Exception as e:  # noqa: BLE001
        log.exception("crm_sync tick failed")
        return {"error": str(e)}
