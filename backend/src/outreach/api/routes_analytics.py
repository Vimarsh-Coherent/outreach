"""Analytics — unified sent/received message log across all channels."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user

log = logging.getLogger("outreach.analytics")
router = APIRouter(prefix="/api/analytics", tags=["analytics"])

_MESSAGES_SQL = text("""
WITH outbound AS (
    SELECT
        sr.sent_at                                                      AS occurred_at,
        'sent'                                                          AS direction,
        sr.channel,
        TRIM(COALESCE(en.contact_snapshot->>'first_name','') || ' ' ||
             COALESCE(en.contact_snapshot->>'last_name',''))            AS contact_name,
        en.contact_snapshot->>'email'                                   AS contact_email,
        en.contact_snapshot->>'phone'                                   AS contact_phone,
        en.contact_snapshot->>'linkedin_url'                            AS contact_li_url,
        LEFT(ss.body, 300)                                              AS body,
        ss.subject,
        NULL::text                                                      AS sentiment_label,
        NULL::float                                                     AS sentiment_confidence,
        sr.id                                                           AS ref_id,
        'step_run'                                                      AS ref_type
    FROM outreach.step_runs sr
    JOIN outreach.sequence_steps ss ON ss.id = sr.step_id
    JOIN outreach.enrolments en     ON en.id = sr.enrolment_id
    WHERE sr.status = 'sent' AND en.user_id = :uid
      AND (:channel = 'all' OR sr.channel = :channel)
),
inbound AS (
    SELECT
        ev.occurred_at,
        'received'                                                      AS direction,
        ev.channel,
        TRIM(COALESCE(en.contact_snapshot->>'first_name','') || ' ' ||
             COALESCE(en.contact_snapshot->>'last_name',''))            AS contact_name,
        en.contact_snapshot->>'email'                                   AS contact_email,
        en.contact_snapshot->>'phone'                                   AS contact_phone,
        en.contact_snapshot->>'linkedin_url'                            AS contact_li_url,
        COALESCE(ev.payload->>'snippet', ev.payload->>'body', '')       AS body,
        ev.payload->>'subject'                                          AS subject,
        rs.label                                                        AS sentiment_label,
        rs.confidence                                                   AS sentiment_confidence,
        ev.id                                                           AS ref_id,
        'event'                                                         AS ref_type
    FROM outreach.events ev
    JOIN outreach.enrolments en       ON en.id = ev.enrolment_id
    LEFT JOIN outreach.reply_sentiment rs ON rs.event_id = ev.id
    WHERE ev.event_type = 'reply' AND en.user_id = :uid
      AND (:channel = 'all' OR ev.channel = :channel)
),
combined AS (
    SELECT * FROM outbound
    UNION ALL
    SELECT * FROM inbound
)
SELECT * FROM combined
WHERE (:direction = 'all' OR direction = :direction)
ORDER BY occurred_at DESC NULLS LAST
LIMIT :limit OFFSET :offset
""")

_STATS_SQL = text("""
WITH outbound AS (
    SELECT sr.channel, 'sent' AS direction
    FROM outreach.step_runs sr
    JOIN outreach.enrolments en ON en.id = sr.enrolment_id
    WHERE sr.status = 'sent' AND en.user_id = :uid
),
inbound AS (
    SELECT ev.channel, 'received' AS direction
    FROM outreach.events ev
    JOIN outreach.enrolments en ON en.id = ev.enrolment_id
    WHERE ev.event_type = 'reply' AND en.user_id = :uid
),
combined AS (SELECT * FROM outbound UNION ALL SELECT * FROM inbound)
SELECT
    channel,
    COUNT(*) FILTER (WHERE direction = 'sent')     AS sent,
    COUNT(*) FILTER (WHERE direction = 'received') AS received
FROM combined
GROUP BY channel
ORDER BY channel
""")

_COUNT_SQL = text("""
WITH outbound AS (
    SELECT sr.channel, sr.sent_at AS occurred_at, 'sent' AS direction
    FROM outreach.step_runs sr
    JOIN outreach.enrolments en ON en.id = sr.enrolment_id
    WHERE sr.status = 'sent' AND en.user_id = :uid
      AND (:channel = 'all' OR sr.channel = :channel)
),
inbound AS (
    SELECT ev.channel, ev.occurred_at, 'received' AS direction
    FROM outreach.events ev
    JOIN outreach.enrolments en ON en.id = ev.enrolment_id
    WHERE ev.event_type = 'reply' AND en.user_id = :uid
      AND (:channel = 'all' OR ev.channel = :channel)
)
SELECT COUNT(*) FROM (
    SELECT * FROM outbound UNION ALL SELECT * FROM inbound
) t WHERE (:direction = 'all' OR direction = :direction)
""")


@router.get("/messages")
async def list_messages(
    channel: str = Query(default="all"),
    direction: str = Query(default="all"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
    session: AsyncSession = Depends(get_session),
    user=Depends(get_current_user),
) -> dict:
    uid = user.id
    params = {"uid": uid, "channel": channel, "direction": direction,
              "limit": limit, "offset": offset}

    rows = (await session.execute(_MESSAGES_SQL, params)).all()
    total = (await session.execute(_COUNT_SQL, {
        "uid": uid, "channel": channel, "direction": direction
    })).scalar() or 0
    stats_rows = (await session.execute(_STATS_SQL, {"uid": uid})).all()

    items = []
    for r in rows:
        items.append({
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            "direction": r.direction,
            "channel": r.channel,
            "contact_name": r.contact_name or None,
            "contact_email": r.contact_email,
            "contact_phone": r.contact_phone,
            "contact_li_url": r.contact_li_url,
            "body": r.body or "",
            "subject": r.subject,
            "sentiment_label": r.sentiment_label,
            "sentiment_confidence": r.sentiment_confidence,
            "ref_id": r.ref_id,
            "ref_type": r.ref_type,
        })

    stats: dict[str, dict] = {}
    for row in stats_rows:
        stats[row.channel] = {"sent": int(row.sent), "received": int(row.received)}

    return {"items": items, "total": int(total), "stats": stats}
