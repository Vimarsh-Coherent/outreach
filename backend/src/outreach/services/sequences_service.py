from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.models.enrolment import Enrolment
from outreach.models.sequence import Sequence
from outreach.models.step import SequenceStep
from outreach.schemas.sequences import (
    SequenceCreate,
    SequenceDetail,
    SequenceOut,
    SequenceStatus,
    SequenceUpdate,
    StepCreate,
    StepOut,
)
from outreach.services.step_reorder import remap_steps_with_transition_delays

# Allowed status transitions. State machine is documented in PLAN.md §21.
_ALLOWED: dict[str, set[str]] = {
    "draft": {"active", "archived"},
    "active": {"paused", "archived"},
    "paused": {"active", "archived"},
    "archived": set(),
}


async def _load_step_count(session: AsyncSession, sequence_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(SequenceStep).where(SequenceStep.sequence_id == sequence_id)
        )
        or 0
    )


async def _load_active_enrolments(session: AsyncSession, sequence_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Enrolment)
            .where(Enrolment.sequence_id == sequence_id, Enrolment.status.in_(("active", "paused")))
        )
        or 0
    )


def _to_out(s: Sequence, step_count: int, active_enrolments: int) -> SequenceOut:
    return SequenceOut(
        id=s.id, name=s.name, description=s.description, status=s.status,  # type: ignore[arg-type]
        timezone=s.timezone, send_window_start=s.send_window_start,
        send_window_end=s.send_window_end, send_days_mask=s.send_days_mask,
        ai_followups_enabled=s.ai_followups_enabled,
        track_opens=s.track_opens, track_clicks=s.track_clicks,
        ai_knowledge_id=s.ai_knowledge_id,
        step_count=step_count, active_enrolments=active_enrolments,
        created_at=s.created_at, updated_at=s.updated_at,
    )


def _step_to_out(s: SequenceStep) -> StepOut:
    return StepOut(
        id=s.id, sequence_id=s.sequence_id, step_order=s.step_order, channel=s.channel,
        delay_days=s.delay_days, delay_hours=s.delay_hours,
        subject=s.subject, body=s.body, config=s.config,
        transitions=s.transitions or [],
        created_at=s.created_at, updated_at=s.updated_at,
    )


async def list_sequences(session: AsyncSession, user_id: int) -> list[SequenceOut]:
    rows = (await session.execute(
        select(Sequence).where(Sequence.user_id == user_id).order_by(Sequence.id.desc())
    )).scalars().all()
    out: list[SequenceOut] = []
    for s in rows:
        sc = await _load_step_count(session, s.id)
        ae = await _load_active_enrolments(session, s.id)
        out.append(_to_out(s, sc, ae))
    return out


async def get_sequence_detail(session: AsyncSession, user_id: int, sequence_id: int) -> SequenceDetail:
    s = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if s is None:
        raise HTTPException(404, "sequence not found")
    steps = (await session.execute(
        select(SequenceStep).where(SequenceStep.sequence_id == s.id).order_by(SequenceStep.step_order.asc())
    )).scalars().all()
    sc = len(steps)
    ae = await _load_active_enrolments(session, s.id)
    return SequenceDetail(
        **_to_out(s, sc, ae).model_dump(),
        steps=[_step_to_out(st) for st in steps],
    )


async def create_sequence(session: AsyncSession, user_id: int, dto: SequenceCreate) -> SequenceOut:
    if dto.send_window_start >= dto.send_window_end:
        raise HTTPException(400, "send_window_start must be < send_window_end")
    s = Sequence(
        user_id=user_id, name=dto.name, description=dto.description,
        status="draft", timezone=dto.timezone,
        send_window_start=dto.send_window_start, send_window_end=dto.send_window_end,
        send_days_mask=dto.send_days_mask,
        ai_followups_enabled=dto.ai_followups_enabled,
        track_opens=dto.track_opens, track_clicks=dto.track_clicks,
    )
    session.add(s)
    await session.commit()
    await session.refresh(s)
    return _to_out(s, 0, 0)


async def update_sequence(
    session: AsyncSession, user_id: int, sequence_id: int, dto: SequenceUpdate,
) -> SequenceOut:
    s = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if s is None:
        raise HTTPException(404, "sequence not found")
    for field, value in dto.model_dump(exclude_unset=True).items():
        setattr(s, field, value)
    if s.send_window_start >= s.send_window_end:
        raise HTTPException(400, "send_window_start must be < send_window_end")
    await session.commit()
    await session.refresh(s)
    return _to_out(s, await _load_step_count(session, s.id), await _load_active_enrolments(session, s.id))


async def change_status(
    session: AsyncSession, user_id: int, sequence_id: int, target: SequenceStatus,
) -> SequenceOut:
    s = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if s is None:
        raise HTTPException(404, "sequence not found")
    if target == s.status:
        return _to_out(s, await _load_step_count(session, s.id), await _load_active_enrolments(session, s.id))
    if target not in _ALLOWED[s.status]:
        raise HTTPException(400, f"cannot transition {s.status} -> {target}")
    if target == "active":
        sc = await _load_step_count(session, s.id)
        if sc == 0:
            raise HTTPException(400, "sequence has no steps; add at least one before activating")
        # Channel-availability guard. Without this, activating a sequence with
        # no matching channel sends every enrolment into an hourly fail loop —
        # the user only finds out after their leads have been spinning for days.
        from outreach.models.channel import Channel

        step_channels = set((await session.execute(
            select(SequenceStep.channel).where(SequenceStep.sequence_id == s.id)
        )).scalars().all())

        async def _has_channel(channel_type: str) -> bool:
            return (await session.scalar(
                select(Channel.id).where(
                    Channel.user_id == user_id,
                    Channel.channel_type == channel_type,
                    Channel.status == "active",
                ).limit(1)
            )) is not None

        if "email" in step_channels and not await _has_channel("email"):
            raise HTTPException(
                400,
                "sequence has email steps but no active email channel — "
                "add an SMTP/IMAP channel in Channels first",
            )
        if step_channels & {"linkedin_dm", "linkedin_connect"} and not await _has_channel("linkedin"):
            raise HTTPException(
                400,
                "sequence has LinkedIn steps but no active LinkedIn channel — "
                "create one in Channels and connect the Chrome extension first",
            )
        if "whatsapp" in step_channels and not await _has_channel("whatsapp"):
            raise HTTPException(
                400,
                "sequence has WhatsApp steps but no active WhatsApp channel — "
                "connect WhatsApp in Channels (scan the QR) first",
            )
    s.status = target
    if target == "archived":
        # Auto-stop active/paused enrolments
        from outreach.models.enrolment import Enrolment
        await session.execute(
            (
                Enrolment.__table__.update()
                .where(Enrolment.sequence_id == s.id, Enrolment.status.in_(("active", "paused")))
                .values(
                    status="stopped_archived",
                    stopped_at=datetime.now(timezone.utc),
                    stopped_reason="sequence archived",
                    next_send_at=None,
                )
            )
        )
    await session.commit()
    await session.refresh(s)
    return _to_out(s, await _load_step_count(session, s.id), await _load_active_enrolments(session, s.id))


async def test_now(session: AsyncSession, user_id: int, sequence_id: int) -> dict:
    """Pull every pending enrolment forward to NOW and immediately run the
    dispatcher so the sequence fires without waiting for the next scheduler tick.

    Touches active enrolments whose next_send_at IS NOT NULL (skips ones parked
    with NULL — those are in-flight awaiting a LinkedIn command webhook, bumping
    them would mint a duplicate command).  Covers both future-scheduled AND
    already-due enrolments so clicking Test now always triggers a real send.
    """
    import asyncio as _asyncio
    from sqlalchemy import text as _text

    s = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if s is None:
        raise HTTPException(404, "sequence not found")
    if s.status == "archived":
        raise HTTPException(400, "cannot test an archived sequence")
    activated = False
    if s.status in ("draft", "paused"):
        if await _load_step_count(session, s.id) == 0:
            raise HTTPException(400, "sequence has no steps to test")
        s.status = "active"
        activated = True
    # Bump ALL pending enrolments (future OR already-due) to NOW so the
    # dispatcher claims them on the immediate tick below.
    result = await session.execute(_text(
        "UPDATE outreach.enrolments SET next_send_at = NOW() "
        "WHERE sequence_id = :sid AND user_id = :uid AND status = 'active' "
        "  AND next_send_at IS NOT NULL "
        "RETURNING id"
    ), {"sid": sequence_id, "uid": user_id})
    fired = [r[0] for r in result.all()]
    await session.commit()

    # Run the dispatcher immediately in the background so the user doesn't
    # have to wait for the next scheduled tick.
    if fired or activated:
        from outreach.workers.dispatcher import tick as _tick
        _asyncio.create_task(_tick())

    return {"fired": len(fired), "activated": activated, "enrolment_ids": fired}


async def delete_sequence(session: AsyncSession, user_id: int, sequence_id: int) -> bool:
    # step_runs reference sequence_steps with NO cascade — deleting a sequence
    # that has send history raises a raw FK violation (unhandled 500 to the
    # UI). Surface it as a clear 400 instead; history-bearing sequences should
    # be archived, not deleted.
    from sqlalchemy import text as _text
    has_runs = await session.scalar(_text(
        "SELECT 1 FROM outreach.step_runs sr "
        "  JOIN outreach.sequence_steps st ON st.id = sr.step_id "
        " WHERE st.sequence_id = :sid LIMIT 1"
    ), {"sid": sequence_id})
    if has_runs:
        raise HTTPException(
            400,
            "sequence has send history (step runs) — archive it instead of deleting",
        )
    r = await session.execute(
        delete(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    await session.commit()
    return (r.rowcount or 0) > 0


# ---------- Steps ----------


async def _next_step_order(session: AsyncSession, sequence_id: int) -> int:
    cur = await session.scalar(
        select(func.coalesce(func.max(SequenceStep.step_order), 0)).where(
            SequenceStep.sequence_id == sequence_id
        )
    )
    return int(cur or 0) + 1


async def add_step(
    session: AsyncSession, user_id: int, sequence_id: int, dto: StepCreate,
) -> StepOut:
    s = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if s is None:
        raise HTTPException(404, "sequence not found")
    if s.status == "archived":
        raise HTTPException(400, "cannot edit an archived sequence")

    # If caller passed step_order, check uniqueness; else auto-assign next.
    step_order = getattr(dto, "step_order", None) or await _next_step_order(session, sequence_id)
    existing = await session.scalar(
        select(SequenceStep.id).where(
            SequenceStep.sequence_id == sequence_id,
            SequenceStep.step_order == step_order,
        )
    )
    if existing is not None:
        step_order = await _next_step_order(session, sequence_id)

    step = SequenceStep(
        sequence_id=sequence_id,
        step_order=step_order,
        channel=dto.channel,
        delay_days=dto.delay_days,
        delay_hours=dto.delay_hours,
        subject=getattr(dto, "subject", None),
        body=dto.body,
        config=dto.config,
        transitions=getattr(dto, "transitions", None) or [],
    )
    session.add(step)
    await session.commit()
    await session.refresh(step)
    return _step_to_out(step)


async def set_transitions(
    session: AsyncSession, user_id: int, sequence_id: int, step_id: int,
    transitions: list[dict],
) -> StepOut:
    """Set a step's branching transitions (managed separately from content edits
    so A/B / spam / content updates never clobber the branch graph)."""
    from outreach.services import branching

    s = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if s is None:
        raise HTTPException(404, "sequence not found")
    step = await session.scalar(
        select(SequenceStep).where(SequenceStep.id == step_id, SequenceStep.sequence_id == sequence_id)
    )
    if step is None:
        raise HTTPException(404, "step not found")
    cleaned = branching.normalize_transitions(transitions)
    # Targets must be real steps in THIS sequence (or null = stop).
    valid_ids = set((await session.execute(
        select(SequenceStep.id).where(SequenceStep.sequence_id == sequence_id)
    )).scalars().all())
    for t in cleaned:
        if t["to_step_id"] is not None and t["to_step_id"] not in valid_ids:
            raise HTTPException(400, f"to_step_id {t['to_step_id']} is not a step in this sequence")
    step.transitions = cleaned
    await session.commit()
    await session.refresh(step)
    return _step_to_out(step)


async def update_step(
    session: AsyncSession, user_id: int, sequence_id: int, step_id: int, dto: StepCreate,
) -> StepOut:
    s = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if s is None:
        raise HTTPException(404, "sequence not found")
    if s.status == "archived":
        raise HTTPException(400, "cannot edit an archived sequence")
    step = await session.scalar(
        select(SequenceStep).where(SequenceStep.id == step_id, SequenceStep.sequence_id == sequence_id)
    )
    if step is None:
        raise HTTPException(404, "step not found")
    step.channel = dto.channel
    step.delay_days = dto.delay_days
    step.delay_hours = dto.delay_hours
    step.subject = getattr(dto, "subject", None)
    step.body = dto.body
    step.config = dto.config
    if getattr(dto, "step_order", None) and dto.step_order != step.step_order:
        clash = await session.scalar(
            select(SequenceStep.id).where(
                SequenceStep.sequence_id == sequence_id,
                SequenceStep.step_order == dto.step_order,
                SequenceStep.id != step.id,
            )
        )
        if clash:
            raise HTTPException(409, f"step_order {dto.step_order} already in use")
        step.step_order = dto.step_order
    await session.commit()
    await session.refresh(step)
    return _step_to_out(step)


async def delete_step(session: AsyncSession, user_id: int, sequence_id: int, step_id: int) -> bool:
    from sqlalchemy import text as _text
    s = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if s is None:
        raise HTTPException(404, "sequence not found")
    # Remove dependent rows before deleting the step (no CASCADE on these FKs).
    await session.execute(_text(
        "DELETE FROM outreach.events WHERE step_run_id IN "
        "  (SELECT id FROM outreach.step_runs WHERE step_id = :sid)"
    ), {"sid": step_id})
    await session.execute(_text(
        "DELETE FROM outreach.step_runs WHERE step_id = :sid"
    ), {"sid": step_id})
    await session.execute(_text(
        "DELETE FROM outreach.li_commands WHERE step_id = :sid"
    ), {"sid": step_id})
    r = await session.execute(
        delete(SequenceStep).where(SequenceStep.id == step_id, SequenceStep.sequence_id == sequence_id)
    )
    await session.commit()
    return (r.rowcount or 0) > 0


async def reorder_steps(
    session: AsyncSession, user_id: int, sequence_id: int, ordered_step_ids: list[int],
) -> list[StepOut]:
    s = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if s is None:
        raise HTTPException(404, "sequence not found")
    rows = (await session.execute(
        select(SequenceStep).where(SequenceStep.sequence_id == sequence_id)
    )).scalars().all()
    rows_by_id = {r.id: r for r in rows}
    if set(rows_by_id.keys()) != set(ordered_step_ids):
        raise HTTPException(400, "reorder list must contain every step exactly once")
    # Preserve wait times per transition (delay before steps 2..N in the old flow).
    old_ordered = sorted(rows, key=lambda r: r.step_order)
    remapped = remap_steps_with_transition_delays(
        [(r.id, r.delay_days, r.delay_hours) for r in old_ordered],
        ordered_step_ids,
    )
    remapped_by_id = {sid: (order, d, h) for sid, order, d, h in remapped}
    # Phase 1: move to negative orders to avoid colliding with UNIQUE constraint
    for r in rows:
        r.step_order = -r.step_order
    await session.flush()
    # Phase 2: assign orders 1..N and remapped transition delays
    for sid, (new_order, delay_days, delay_hours) in remapped_by_id.items():
        row = rows_by_id[sid]
        row.step_order = new_order
        row.delay_days = delay_days
        row.delay_hours = delay_hours
    await session.commit()
    refreshed = (await session.execute(
        select(SequenceStep).where(SequenceStep.sequence_id == sequence_id)
    )).scalars().all()
    return [
        _step_to_out(r) for r in sorted(refreshed, key=lambda r: r.step_order)
    ]
