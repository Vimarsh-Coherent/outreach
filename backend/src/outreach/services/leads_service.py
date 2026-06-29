from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.models.enrolment import Enrolment
from outreach.models.event import Event, ReplySentiment
from outreach.models.lead import Lead
from outreach.services.identity import canonical_identity, linkedin_url_from_slug


_MERGEABLE_FIELDS = (
    "email", "phone", "linkedin_url",
    "first_name", "last_name", "company", "title",
)


class UploadStats:
    __slots__ = (
        "inserted", "updated", "merged_within_upload",
        "skipped_no_identity", "skipped_invalid",
    )

    def __init__(self) -> None:
        self.inserted = 0
        self.updated = 0
        self.merged_within_upload = 0
        self.skipped_no_identity = 0
        self.skipped_invalid = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "merged_within_upload": self.merged_within_upload,
            "skipped_no_identity": self.skipped_no_identity,
            "skipped_invalid": self.skipped_invalid,
        }


def _merge_payload(existing: dict, incoming: dict) -> None:
    """Fill blank fields on `existing` with values from `incoming`."""
    for k in _MERGEABLE_FIELDS:
        if existing.get(k) is None and incoming.get(k) is not None:
            existing[k] = incoming[k]


async def upsert_leads(
    session: AsyncSession, user_id: int, rows: Iterable[dict], source: str = "csv"
) -> UploadStats:
    stats = UploadStats()
    batch: dict[bytes, dict] = {}
    BATCH_SIZE = 500

    async def flush() -> None:
        if not batch:
            return
        stmt = insert(Lead).values(list(batch.values()))
        update_cols = {
            "email": func.coalesce(stmt.excluded.email, Lead.email),
            "phone": func.coalesce(stmt.excluded.phone, Lead.phone),
            "linkedin_url": func.coalesce(stmt.excluded.linkedin_url, Lead.linkedin_url),
            "first_name": func.coalesce(stmt.excluded.first_name, Lead.first_name),
            "last_name": func.coalesce(stmt.excluded.last_name, Lead.last_name),
            "company": func.coalesce(stmt.excluded.company, Lead.company),
            "title": func.coalesce(stmt.excluded.title, Lead.title),
            "custom": stmt.excluded.custom,
            "source": stmt.excluded.source,
            "updated_at": func.now(),
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_leads_user_identity",
            set_=update_cols,
        ).returning(Lead.id, Lead.created_at, Lead.updated_at)
        result = await session.execute(stmt)
        for _id, created, updated in result.all():
            if abs((updated - created).total_seconds()) < 0.05:
                stats.inserted += 1
            else:
                stats.updated += 1
        batch.clear()

    for raw in rows:
        ident = canonical_identity(
            email=raw.get("email"),
            phone=raw.get("phone"),
            linkedin=raw.get("linkedin_url"),
        )
        if ident.hash is None:
            stats.skipped_no_identity += 1
            continue
        payload = {
            "user_id": user_id,
            "identity_hash": ident.hash,
            "email": ident.email,
            "phone": ident.phone,
            "linkedin_url": linkedin_url_from_slug(ident.linkedin_slug),
            "first_name": raw.get("first_name") or None,
            "last_name": raw.get("last_name") or None,
            "company": raw.get("company") or None,
            "title": raw.get("title") or None,
            "custom": {},
            "source": source,
        }
        if ident.hash in batch:
            _merge_payload(batch[ident.hash], payload)
            stats.merged_within_upload += 1
        else:
            batch[ident.hash] = payload
        if len(batch) >= BATCH_SIZE:
            await flush()
    await flush()
    await session.commit()
    return stats


async def list_leads(
    session: AsyncSession, user_id: int, *,
    search: str | None = None, limit: int = 50, offset: int = 0,
) -> tuple[list[Lead], int]:
    base = select(Lead).where(Lead.user_id == user_id)
    if search:
        like = f"%{search}%"
        base = base.where(or_(
            Lead.email.ilike(like),
            Lead.first_name.ilike(like),
            Lead.last_name.ilike(like),
            Lead.company.ilike(like),
            Lead.title.ilike(like),
        ))
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    result = await session.execute(base.order_by(Lead.updated_at.desc()).limit(limit).offset(offset))
    return list(result.scalars().all()), int(total)


async def list_leads_enriched(
    session: AsyncSession, user_id: int, *,
    search: str | None = None, limit: int = 50, offset: int = 0,
) -> tuple[list[dict], int]:
    """list_leads + latest inbound reply + sentiment per lead, returned as dicts."""
    base = select(Lead).where(Lead.user_id == user_id)
    if search:
        like = f"%{search}%"
        base = base.where(or_(
            Lead.email.ilike(like),
            Lead.first_name.ilike(like),
            Lead.last_name.ilike(like),
            Lead.company.ilike(like),
            Lead.title.ilike(like),
        ))
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    result = await session.execute(base.order_by(Lead.updated_at.desc()).limit(limit).offset(offset))
    leads = list(result.scalars().all())
    if not leads:
        return [], int(total)

    lead_ids = [lead.id for lead in leads]

    from sqlalchemy import text as _text

    # Fetch the latest reply per (lead, channel) in one query using DISTINCT ON.
    # Email replies are filtered to only count messages FROM the lead (not echoes
    # of the outreach user's own replies). LinkedIn and WhatsApp replies are always
    # from the lead side so no from-filter is needed.
    reply_rows = (await session.execute(_text("""
        SELECT DISTINCT ON (en.lead_id, ev.channel)
            en.lead_id,
            ev.channel,
            ev.id          AS event_id,
            ev.occurred_at,
            ev.payload,
            rs.label,
            rs.confidence,
            rs.reasoning
        FROM outreach.enrolments en
        JOIN outreach.events ev  ON ev.enrolment_id = en.id
        JOIN outreach.leads  l   ON l.id = en.lead_id
        LEFT JOIN outreach.reply_sentiment rs ON rs.event_id = ev.id
        WHERE ev.event_type = 'reply'
          AND en.lead_id = ANY(:lead_ids)
          AND (
              (ev.channel = 'email' AND ev.payload->>'from' = l.email)
              OR ev.channel IN ('linkedin', 'whatsapp')
          )
        ORDER BY en.lead_id, ev.channel, ev.occurred_at DESC
    """), {"lead_ids": lead_ids})).all()

    channel_replies_by_lead: dict[int, dict[str, dict]] = {}
    latest_by_lead: dict[int, dict] = {}

    for lead_id_val, channel, ev_id, occurred_at, payload, label, confidence, reasoning in reply_rows:
        lid = int(lead_id_val)
        body_text = (payload.get("body") or payload.get("snippet")) if payload else None
        entry = {
            "event_id": ev_id,
            "occurred_at": occurred_at,
            "body": body_text,
            "channel": channel,
            "sentiment_label": label,
            "sentiment_confidence": confidence,
            "sentiment_reasoning": reasoning,
        }
        channel_replies_by_lead.setdefault(lid, {})[channel] = entry
        if lid not in latest_by_lead or occurred_at > latest_by_lead[lid]["occurred_at"]:
            latest_by_lead[lid] = entry

    enriched = []
    for lead in leads:
        enriched.append({
            "id": lead.id,
            "email": lead.email,
            "phone": lead.phone,
            "linkedin_url": lead.linkedin_url,
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "company": lead.company,
            "title": lead.title,
            "source": lead.source,
            "created_at": lead.created_at,
            "updated_at": lead.updated_at,
            "latest_reply": latest_by_lead.get(lead.id),
            "channel_replies": channel_replies_by_lead.get(lead.id, {}),
        })
    return enriched, int(total)


async def update_lead(session: AsyncSession, user_id: int, lead_id: int, dto) -> Lead | None:
    lead = await session.scalar(select(Lead).where(Lead.id == lead_id, Lead.user_id == user_id))
    if lead is None:
        return None
    for field in ("email", "first_name", "last_name", "phone", "linkedin_url", "company", "title"):
        val = getattr(dto, field, None)
        if val is not None:
            setattr(lead, field, val)
        elif getattr(dto, field) is None and field not in ("email",):
            setattr(lead, field, None)
    lead.identity_hash = canonical_identity(
        email=lead.email, phone=lead.phone, linkedin=lead.linkedin_url
    ).hash
    await session.commit()
    await session.refresh(lead)
    return lead


async def delete_lead(session: AsyncSession, user_id: int, lead_id: int) -> bool:
    r = await session.execute(
        delete(Lead).where(Lead.id == lead_id, Lead.user_id == user_id)
    )
    await session.commit()
    return (r.rowcount or 0) > 0


async def delete_all_leads(session: AsyncSession, user_id: int) -> int:
    r = await session.execute(delete(Lead).where(Lead.user_id == user_id))
    await session.commit()
    return r.rowcount or 0
