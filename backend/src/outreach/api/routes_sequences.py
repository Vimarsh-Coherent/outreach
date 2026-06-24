from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.sequence import Sequence
from outreach.models.step import SequenceStep
from outreach.models.user import User
from outreach.schemas.grounding import SequenceGroundingResponse
from outreach.schemas.sequences import (
    AbPromoteRequest,
    EnrolmentCreate,
    EnrolmentResult,
    ReorderRequest,
    SequenceCreate,
    SequenceDetail,
    QuickGenerateRequest,
    SequenceGenerateRequest,
    SequenceGenerateResponse,
    SequenceOut,
    SequenceUpdate,
    StatusChange,
    StepCreate,
    StepOut,
    TransitionsUpdate,
)
from outreach.services import (
    enrolments_service,
    grounding_service,
    quick_generator,
    sequence_generator,
    sequences_service,
)

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


@router.post("/generate", response_model=SequenceGenerateResponse, status_code=201)
async def generate_sequence_from_prompt(
    dto: SequenceGenerateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SequenceGenerateResponse:
    """RAG: a prompt (+ optional linked documents) → a grounded AI sequence."""
    detail, warnings = await sequence_generator.generate_sequence_from_prompt(
        session, user.id, dto.prompt, dto.timezone, dto.document_ids or None,
    )
    return SequenceGenerateResponse(sequence=detail, warnings=warnings)


@router.post("/generate-quick", response_model=SequenceDetail, status_code=201)
async def generate_sequence_quick(
    dto: QuickGenerateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SequenceDetail:
    """Channels-based quick generator (Sequences page → ✨ Generate with AI panel)."""
    return await quick_generator.generate_sequence(
        session, user.id,
        name=dto.name, description=dto.description, channels=dto.channels,
        num_emails=dto.num_emails, timezone=dto.timezone, test_mode=dto.test_mode,
    )


@router.get("/{sequence_id}", response_model=SequenceDetail)
async def get_sequence(
    sequence_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SequenceDetail:
    return await sequences_service.get_sequence_detail(session, user.id, sequence_id)


@router.get("/{sequence_id}/grounding", response_model=SequenceGroundingResponse)
async def get_sequence_grounding(
    sequence_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SequenceGroundingResponse:
    return await grounding_service.compute_sequence_grounding(session, user.id, sequence_id)


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


@router.post("/{sequence_id}/test-now")
async def test_now(
    sequence_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Fire this sequence's scheduled enrolments immediately (bypass send window)."""
    return await sequences_service.test_now(session, user.id, sequence_id)


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


@router.put("/{sequence_id}/steps/{step_id}/transitions", response_model=StepOut)
async def set_step_transitions(
    sequence_id: int, step_id: int, dto: TransitionsUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StepOut:
    return await sequences_service.set_transitions(
        session, user.id, sequence_id, step_id, dto.transitions
    )


@router.post("/{sequence_id}/steps/reorder", response_model=list[StepOut])
async def reorder_steps(
    sequence_id: int,
    dto: ReorderRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[StepOut]:
    return await sequences_service.reorder_steps(session, user.id, sequence_id, dto.step_ids)


# ---------- A/B testing ----------


async def _load_owned_step(
    session: AsyncSession, user_id: int, sequence_id: int, step_id: int
) -> SequenceStep:
    seq = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if seq is None:
        raise HTTPException(404, "sequence not found")
    step = await session.scalar(
        select(SequenceStep).where(
            SequenceStep.id == step_id, SequenceStep.sequence_id == sequence_id
        )
    )
    if step is None:
        raise HTTPException(404, "step not found")
    return step


@router.get("/{sequence_id}/steps/{step_id}/ab-stats")
async def ab_stats(
    sequence_id: int, step_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from outreach.services import ab_testing

    step = await _load_owned_step(session, user.id, sequence_id, step_id)
    configured = ab_testing.normalize_variants((step.config or {}).get("variants"))
    stats = await ab_testing.variant_stats(session, step_id)
    return {"configured": configured, "stats": stats, "winner": ab_testing.pick_winner(stats)}


@router.post("/{sequence_id}/steps/{step_id}/ab-promote", response_model=StepOut)
async def ab_promote(
    sequence_id: int, step_id: int, dto: AbPromoteRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StepOut:
    from outreach.services import ab_testing

    step = await _load_owned_step(session, user.id, sequence_id, step_id)
    variants = ab_testing.normalize_variants((step.config or {}).get("variants"))
    chosen = next((v for v in variants if v["label"] == dto.label), None)
    if chosen is None:
        raise HTTPException(400, f"no variant labelled '{dto.label}' on this step")
    # Lock in the winner: it becomes the step's single message; drop the A/B test.
    if chosen["subject"] is not None:
        step.subject = chosen["subject"]
    step.body = chosen["body"]
    cfg = dict(step.config or {})  # fresh dict so SQLAlchemy detects the JSONB change
    cfg.pop("variants", None)
    step.config = cfg
    await session.commit()
    await session.refresh(step)
    return sequences_service._step_to_out(step)  # noqa: SLF001


# ---------- Enrolments ----------


@router.post("/{sequence_id}/enrol", response_model=EnrolmentResult)
async def enrol(
    sequence_id: int,
    dto: EnrolmentCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> EnrolmentResult:
    return await enrolments_service.enrol_leads(session, user.id, sequence_id, dto.lead_ids)
