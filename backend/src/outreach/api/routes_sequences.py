from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.user import User
from outreach.schemas.sequences import (
    EnrolmentCreate,
    EnrolmentResult,
    ReorderRequest,
    SequenceCreate,
    SequenceDetail,
    SequenceOut,
    SequenceUpdate,
    StatusChange,
    StepCreate,
    StepOut,
)
from outreach.services import enrolments_service, sequences_service

router = APIRouter(prefix="/api/sequences", tags=["sequences"])


@router.get("", response_model=list[SequenceOut])
async def list_sequences(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SequenceOut]:
    return await sequences_service.list_sequences(session, user.id)


@router.post("", response_model=SequenceOut, status_code=201)
async def create_sequence(
    dto: SequenceCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SequenceOut:
    return await sequences_service.create_sequence(session, user.id, dto)


@router.get("/{sequence_id}", response_model=SequenceDetail)
async def get_sequence(
    sequence_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SequenceDetail:
    return await sequences_service.get_sequence_detail(session, user.id, sequence_id)


@router.patch("/{sequence_id}", response_model=SequenceOut)
async def update_sequence(
    sequence_id: int,
    dto: SequenceUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SequenceOut:
    return await sequences_service.update_sequence(session, user.id, sequence_id, dto)


@router.post("/{sequence_id}/status", response_model=SequenceOut)
async def change_status(
    sequence_id: int,
    dto: StatusChange,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SequenceOut:
    return await sequences_service.change_status(session, user.id, sequence_id, dto.status)


@router.delete("/{sequence_id}", status_code=204)
async def delete_sequence(
    sequence_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    if not await sequences_service.delete_sequence(session, user.id, sequence_id):
        raise HTTPException(404, "sequence not found")


# ---------- Steps ----------


@router.post("/{sequence_id}/steps", response_model=StepOut, status_code=201)
async def add_step(
    sequence_id: int,
    dto: StepCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StepOut:
    return await sequences_service.add_step(session, user.id, sequence_id, dto)


@router.patch("/{sequence_id}/steps/{step_id}", response_model=StepOut)
async def update_step(
    sequence_id: int, step_id: int,
    dto: StepCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StepOut:
    return await sequences_service.update_step(session, user.id, sequence_id, step_id, dto)


@router.delete("/{sequence_id}/steps/{step_id}", status_code=204)
async def delete_step(
    sequence_id: int, step_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    if not await sequences_service.delete_step(session, user.id, sequence_id, step_id):
        raise HTTPException(404, "step not found")


@router.post("/{sequence_id}/steps/reorder", response_model=list[StepOut])
async def reorder_steps(
    sequence_id: int,
    dto: ReorderRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[StepOut]:
    return await sequences_service.reorder_steps(session, user.id, sequence_id, dto.step_ids)


# ---------- Enrolments ----------


@router.post("/{sequence_id}/enrol", response_model=EnrolmentResult)
async def enrol(
    sequence_id: int,
    dto: EnrolmentCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EnrolmentResult:
    return await enrolments_service.enrol_leads(session, user.id, sequence_id, dto.lead_ids)
