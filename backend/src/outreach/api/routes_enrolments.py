from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.user import User
from outreach.schemas.sequences import EnrolmentOut
from outreach.services import enrolments_service

router = APIRouter(prefix="/api", tags=["enrolments"])


@router.get("/sequences/{sequence_id}/enrolments")
async def list_enrolments(
    sequence_id: int,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows, total = await enrolments_service.list_enrolments(
        session, user.id, sequence_id, status=status, limit=limit, offset=offset,
    )
    items = [EnrolmentOut.model_validate(r, from_attributes=True) for r in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/enrolments/{enrolment_id}/pause")
async def pause(
    enrolment_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not await enrolments_service.pause_enrolment(session, user.id, enrolment_id):
        raise HTTPException(404, "enrolment not found or not pauseable")
    return {"ok": True}


@router.post("/enrolments/{enrolment_id}/resume")
async def resume(
    enrolment_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not await enrolments_service.resume_enrolment(session, user.id, enrolment_id):
        raise HTTPException(404, "enrolment not found or not resumeable")
    return {"ok": True}


@router.post("/enrolments/{enrolment_id}/stop")
async def stop(
    enrolment_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not await enrolments_service.stop_enrolment(session, user.id, enrolment_id):
        raise HTTPException(404, "enrolment not found")
    return {"ok": True}
