"""Integration tests for sequences_service.reorder_steps (MissingGreenlet regression)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from outreach.db import SessionLocal
from outreach.models.step import SequenceStep
from outreach.services import sequences_service
from outreach.services.seed_user import get_or_create_default_user

pytestmark = pytest.mark.asyncio(loop_scope="function")


async def _sequence_with_steps(session, user_id: int) -> tuple[int, list[int]] | None:
    from outreach.models.sequence import Sequence

    row = await session.scalar(
        select(Sequence.id)
        .where(Sequence.user_id == user_id)
        .order_by(Sequence.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    sequence_id = int(row)
    steps = (
        await session.execute(
            select(SequenceStep.id)
            .where(SequenceStep.sequence_id == sequence_id)
            .order_by(SequenceStep.step_order.asc())
        )
    ).scalars().all()
    if len(steps) < 2:
        return None
    return sequence_id, list(steps)


@pytest.mark.asyncio
async def test_reorder_steps_returns_valid_step_out_with_timestamps():
    async with SessionLocal() as session:
        user = await get_or_create_default_user(session)
        found = await _sequence_with_steps(session, user.id)
        if found is None:
            pytest.skip("no sequence with 2+ steps in database")
        sequence_id, original_ids = found

        swapped = [original_ids[1], original_ids[0], *original_ids[2:]]
        result = await sequences_service.reorder_steps(
            session, user.id, sequence_id, swapped
        )

        assert len(result) == len(original_ids)
        assert [s.step_order for s in result] == list(range(1, len(result) + 1))
        assert result[0].id == swapped[0]
        for step in result:
            assert step.created_at is not None
            assert step.updated_at is not None

        await sequences_service.reorder_steps(
            session, user.id, sequence_id, original_ids
        )
