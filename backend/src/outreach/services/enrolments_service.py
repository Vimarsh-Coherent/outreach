from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.models.enrolment import Enrolment
from outreach.models.lead import Lead
from outreach.models.sequence import Sequence
from outreach.models.step import SequenceStep
from outreach.schemas.sequences import EnrolmentResult
from outreach.services.identity import canonical_identity
from outreach.services.send_window import next_valid_slot


def _lead_snapshot(lead: Lead) -> dict:
    return {
        "lead_id": lead.id,
        "email": lead.email,
        "phone": lead.phone,
        "linkedin_url": lead.linkedin_url,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "company": lead.company,
        "title": lead.title,
    }


async def enrol_leads(
    session: AsyncSession, user_id: int, sequence_id: int, lead_ids: list[int],
) -> EnrolmentResult:
    seq = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if seq is None:
        raise HTTPException(404, "sequence not found")
    if seq.status == "archived":
        raise HTTPException(400, "cannot enrol into an archived sequence")

    step1 = await session.scalar(
        select(SequenceStep).where(SequenceStep.sequence_id == sequence_id)
        .order_by(SequenceStep.step_order.asc()).limit(1)
    )
    if step1 is None:
        raise HTTPException(400, "sequence has no steps; add at least one before enrolling")

    now = datetime.now(timezone.utc)
    next_send_at = next_valid_slot(
        base=now,
        delay_days=step1.delay_days,
        delay_hours=step1.delay_hours,
        tz_name=seq.timezone,
        window_start=seq.send_window_start,
        window_end=seq.send_window_end,
        days_mask=seq.send_days_mask,
    )

    leads = (await session.execute(
        select(Lead).where(Lead.id.in_(lead_ids), Lead.user_id == user_id)
    )).scalars().all()

    enrolled = 0
    deduped = 0
    skipped_no_identity = 0
    rows: list[dict] = []
    for lead in leads:
        ident = canonical_identity(
            email=lead.email, phone=lead.phone, linkedin=lead.linkedin_url
        )
        if ident.hash is None:
            skipped_no_identity += 1
            continue
        rows.append({
            "sequence_id": sequence_id,
            "user_id": user_id,
            "lead_id": lead.id,
            "identity_hash": ident.hash,
            "contact_snapshot": _lead_snapshot(lead),
            "status": "active",
            "current_step_order": 0,
            "next_send_at": next_send_at,
            "runtime_state": {},
        })
    if not rows:
        return EnrolmentResult(enrolled=0, deduped=0, skipped_no_identity=skipped_no_identity)

    # Dedupe by identity_hash within this batch (rare but possible if user picks the
    # same lead twice).
    seen: set[bytes] = set()
    unique_rows: list[dict] = []
    for r in rows:
        if r["identity_hash"] in seen:
            deduped += 1
            continue
        seen.add(r["identity_hash"])
        unique_rows.append(r)

    # ON CONFLICT on the partial unique index `uq_enrol_active_identity` is not
    # directly supported by sqlalchemy.dialects.postgresql.insert.on_conflict_*
    # against partial indexes the same way; use the do_nothing path via
    # constraint inference.
    stmt = insert(Enrolment).values(unique_rows).on_conflict_do_nothing(
        index_elements=[Enrolment.sequence_id, Enrolment.identity_hash],
        index_where=(Enrolment.status.in_(("active", "paused"))),
    ).returning(Enrolment.id)
    result = await session.execute(stmt)
    inserted_ids = [row[0] for row in result.all()]
    enrolled = len(inserted_ids)
    deduped += len(unique_rows) - enrolled
    await session.commit()
    return EnrolmentResult(
        enrolled=enrolled, deduped=deduped, skipped_no_identity=skipped_no_identity,
    )


async def list_enrolments(
    session: AsyncSession, user_id: int, sequence_id: int, *,
    status: str | None = None, limit: int = 100, offset: int = 0,
) -> tuple[list[Enrolment], int]:
    from sqlalchemy import func
    base = select(Enrolment).where(Enrolment.sequence_id == sequence_id, Enrolment.user_id == user_id)
    if status:
        base = base.where(Enrolment.status == status)
    total = int(await session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = (await session.execute(
        base.order_by(Enrolment.id.desc()).limit(limit).offset(offset)
    )).scalars().all()
    return list(rows), total


async def stop_enrolment(
    session: AsyncSession, user_id: int, enrolment_id: int, reason: str = "manual",
) -> bool:
    e = await session.scalar(
        select(Enrolment).where(Enrolment.id == enrolment_id, Enrolment.user_id == user_id)
    )
    if e is None:
        return False
    e.status = "stopped_manual"
    e.stopped_at = datetime.now(timezone.utc)
    e.stopped_reason = reason
    e.next_send_at = None
    await session.commit()
    return True


async def pause_enrolment(
    session: AsyncSession, user_id: int, enrolment_id: int,
) -> bool:
    e = await session.scalar(
        select(Enrolment).where(Enrolment.id == enrolment_id, Enrolment.user_id == user_id)
    )
    if e is None or e.status != "active":
        return False
    e.status = "paused"
    e.next_send_at = None
    await session.commit()
    return True


async def resume_enrolment(
    session: AsyncSession, user_id: int, enrolment_id: int,
) -> bool:
    e = await session.scalar(
        select(Enrolment).where(Enrolment.id == enrolment_id, Enrolment.user_id == user_id)
    )
    if e is None or e.status != "paused":
        return False
    seq = await session.scalar(select(Sequence).where(Sequence.id == e.sequence_id))
    if seq is None or seq.status != "active":
        return False
    # Re-compute next_send_at from the next pending step.
    step = await session.scalar(
        select(SequenceStep).where(
            SequenceStep.sequence_id == e.sequence_id,
            SequenceStep.step_order > e.current_step_order,
        ).order_by(SequenceStep.step_order.asc()).limit(1)
    )
    e.status = "active"
    if step is not None:
        e.next_send_at = next_valid_slot(
            base=datetime.now(timezone.utc),
            delay_days=step.delay_days, delay_hours=step.delay_hours,
            tz_name=seq.timezone,
            window_start=seq.send_window_start, window_end=seq.send_window_end,
            days_mask=seq.send_days_mask,
        )
    else:
        e.status = "done"
        e.next_send_at = None
    await session.commit()
    return True
