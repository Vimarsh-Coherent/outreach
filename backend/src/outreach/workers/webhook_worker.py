"""Webhook outbox worker.

fan_out()         — scan new events past a cursor, create a pending delivery for
                    each (event × subscribed endpoint). Transactional outbox.
deliver_pending() — POST due deliveries with an HMAC signature; retry with
                    backoff until delivered or exhausted.
tick()            — run both (wired into the scheduler).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, text

from outreach.db import SessionLocal
from outreach.models.webhook import WebhookCursor, WebhookDelivery, WebhookEndpoint
from outreach.services import webhooks

log = logging.getLogger("outreach.webhooks")


async def fan_out(batch: int = 500) -> dict:
    async with SessionLocal() as s:
        cur = await s.get(WebhookCursor, 1)
        if cur is None:
            cur = WebhookCursor(id=1, last_event_id=0)
            s.add(cur)
            await s.flush()

        rows = (await s.execute(text("""
            SELECT e.id, e.event_type, e.channel, e.payload, e.occurred_at,
                   e.enrolment_id, en.user_id, en.lead_id,
                   l.email, l.first_name, l.last_name, l.company
              FROM outreach.events e
              JOIN outreach.enrolments en ON en.id = e.enrolment_id
              LEFT JOIN outreach.leads l ON l.id = en.lead_id
             WHERE e.id > :cur
             ORDER BY e.id
             LIMIT :lim
        """), {"cur": cur.last_event_id, "lim": batch})).mappings().all()
        if not rows:
            return {"scanned": 0, "queued": 0}

        user_ids = {r["user_id"] for r in rows}
        endpoints = (await s.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.user_id.in_(user_ids),
                WebhookEndpoint.active.is_(True),
            )
        )).scalars().all()
        by_user: dict[int, list[WebhookEndpoint]] = {}
        for ep in endpoints:
            by_user.setdefault(ep.user_id, []).append(ep)

        now = datetime.now(timezone.utc)
        queued = 0
        last_id = cur.last_event_id
        for r in rows:
            last_id = r["id"]
            for ep in by_user.get(r["user_id"], []):
                if r["event_type"] not in (ep.event_types or []):
                    continue
                s.add(WebhookDelivery(
                    endpoint_id=ep.id,
                    event_id=r["id"],
                    event_type=r["event_type"],
                    payload=webhooks.build_payload(dict(r)),
                    status="pending",
                    next_attempt_at=now,
                ))
                queued += 1
        cur.last_event_id = last_id
        await s.commit()
        return {"scanned": len(rows), "queued": queued}


async def _post(ep: WebhookEndpoint, delivery: WebhookDelivery) -> tuple[bool, str | None]:
    body = json.dumps(delivery.payload, default=str, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "CoherentOutreach-Webhook/1",
        "X-Coherent-Event": delivery.event_type,
        "X-Coherent-Delivery": str(delivery.id),
        "X-Coherent-Signature": webhooks.signature_header(ep.secret, body),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(ep.url, content=body, headers=headers)
        if 200 <= resp.status_code < 300:
            return True, None
        return False, f"HTTP {resp.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:140]}"


async def deliver_pending(batch: int = 100) -> dict:
    async with SessionLocal() as s:
        due = (await s.execute(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status == "pending",
                WebhookDelivery.next_attempt_at <= datetime.now(timezone.utc),
            )
            .order_by(WebhookDelivery.id)
            .limit(batch)
        )).scalars().all()
        if not due:
            return {"sent": 0, "failed": 0}

        ep_ids = {d.endpoint_id for d in due}
        eps = {e.id: e for e in (await s.execute(
            select(WebhookEndpoint).where(WebhookEndpoint.id.in_(ep_ids))
        )).scalars().all()}

        sent = failed = 0
        for d in due:
            ep = eps.get(d.endpoint_id)
            d.attempts += 1
            if ep is None:
                d.status, d.last_error = "failed", "endpoint deleted"
                failed += 1
                continue
            ok, err = await _post(ep, d)
            if ok:
                d.status = "delivered"
                d.delivered_at = datetime.now(timezone.utc)
                d.last_error = None
                sent += 1
            else:
                d.last_error = err
                if d.attempts >= webhooks.MAX_ATTEMPTS:
                    d.status = "failed"
                    failed += 1
                else:
                    d.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                        seconds=webhooks.backoff_seconds(d.attempts)
                    )
        await s.commit()
        return {"sent": sent, "failed": failed, "due": len(due)}


async def tick() -> dict:
    try:
        f = await fan_out()
        d = await deliver_pending()
        return {"fan_out": f, "deliver": d}
    except Exception as e:  # noqa: BLE001
        log.exception("webhook worker tick failed")
        return {"error": str(e)}
