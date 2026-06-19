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
    result = await session.execute(base.order_by(Lead.id.desc()).limit(limit).offset(offset))
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
    result = await session.execute(base.order_by(Lead.id.desc()).limit(limit).offset(offset))
    leads = list(result.scalars().all())
    if not leads:
        return [], int(total)

    lead_ids = [lead.id for lead in leads]

    # Subquery: latest reply event id per lead — pick by occurred_at so the
    # most-recent inbound timestamp wins, not the highest auto-increment id.
    # Also exclude events where the "from" payload matches the lead's own email
    # (naman's reply-to-reply would otherwise show up as the lead's latest reply).
    latest_reply_sq = (
        select(
            Enrolment.lead_id.label("lead_id"),
            func.max(Event.occurred_at).label("max_occurred_at"),
        )
        .join(Event, Event.enrolment_id == Enrolment.id)
        .join(Lead, Lead.id == Enrolment.lead_id)
        .where(
            Event.event_type == "reply",
            Enrolment.lead_id.in_(lead_ids),
            # Only count events where the sender is the lead (not the outreach user)
            Event.payload["from"].as_string() == Lead.email,
        )
        .group_by(Enrolment.lead_id)
        .subquery()
    )

    # Join back to get the event id for that max timestamp
    latest_event_sq = (
        select(
            Enrolment.lead_id.label("lead_id"),
            func.max(Event.id).label("event_id"),
        )
        .join(Event, Event.enrolment_id == Enrolment.id)
        .join(Lead, Lead.id == Enrolment.lead_id)
        .join(
            latest_reply_sq,
            (latest_reply_sq.c.lead_id == Enrolment.lead_id)
            & (Event.occurred_at == latest_reply_sq.c.max_occurred_at),
        )
        .where(
            Event.event_type == "reply",
            Enrolment.lead_id.in_(lead_ids),
            Event.payload["from"].as_string() == Lead.email,
        )
        .group_by(Enrolment.lead_id)
        .subquery()
    )

    reply_rows = (await session.execute(
        select(
            latest_event_sq.c.lead_id,
            Event.id,
            Event.occurred_at,
            Event.payload,
            ReplySentiment.label,
            ReplySentiment.confidence,
            ReplySentiment.reasoning,
        )
        .join(Event, Event.id == latest_event_sq.c.event_id)
        .outerjoin(ReplySentiment, ReplySentiment.event_id == Event.id)
    )).all()

    reply_by_lead: dict[int, dict] = {}
    for lead_id_val, ev_id, occurred_at, payload, label, confidence, reasoning in reply_rows:
        body_text = None
        if payload:
            body_text = payload.get("body") or payload.get("snippet") or None
        reply_by_lead[int(lead_id_val)] = {
            "event_id": ev_id,
            "occurred_at": occurred_at,
            "body": body_text,
            "sentiment_label": label,
            "sentiment_confidence": confidence,
            "sentiment_reasoning": reasoning,
        }

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
            "latest_reply": reply_by_lead.get(lead.id),
        })
    return enriched, int(total)


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
