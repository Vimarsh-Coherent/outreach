from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.enrolment import Enrolment
from outreach.models.event import Event, ReplySentiment
from outreach.models.lead import Lead
from outreach.models.sequence import Sequence
from outreach.models.step import SequenceStep
from outreach.models.step_run import StepRun
from outreach.models.user import User

router = APIRouter(prefix="/api/leads", tags=["timeline"])


TimelineKind = Literal["sent", "reply", "auto_reply", "bounce", "delivered", "error", "skipped"]


class TimelineItem(BaseModel):
    kind: TimelineKind
    at: datetime
    sequence_id: int | None = None
    sequence_name: str | None = None
    step_order: int | None = None
    channel: str | None = None
    subject: str | None = None
    body: str | None = None
    sentiment_label: str | None = None
    sentiment_confidence: float | None = None
    sentiment_reasoning: str | None = None
    error: str | None = None
    external_id: str | None = None


class LeadTimelineResponse(BaseModel):
    lead_id: int
    lead: dict
    enrolments: list[dict]
    items: list[TimelineItem]


@router.get("/{lead_id}/timeline", response_model=LeadTimelineResponse)
async def lead_timeline(
    lead_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LeadTimelineResponse:
    lead = await session.scalar(
        select(Lead).where(Lead.id == lead_id, Lead.user_id == user.id)
    )
    if lead is None:
        raise HTTPException(404, "lead not found")

    enrolments = (await session.execute(
        select(Enrolment).where(Enrolment.lead_id == lead_id, Enrolment.user_id == user.id)
    )).scalars().all()
    enrol_ids = [e.id for e in enrolments]
    if not enrol_ids:
        return LeadTimelineResponse(
            lead_id=lead_id,
            lead=_lead_dict(lead),
            enrolments=[],
            items=[],
        )

    seq_ids = list({e.sequence_id for e in enrolments})
    seq_rows = (await session.execute(
        select(Sequence).where(Sequence.id.in_(seq_ids))
    )).scalars().all()
    seq_name_by_id = {s.id: s.name for s in seq_rows}

    runs = (await session.execute(
        select(StepRun, SequenceStep)
        .join(SequenceStep, SequenceStep.id == StepRun.step_id)
        .where(StepRun.enrolment_id.in_(enrol_ids))
        .order_by(StepRun.created_at.asc())
    )).all()

    events = (await session.execute(
        select(Event, ReplySentiment)
        .join(ReplySentiment, ReplySentiment.event_id == Event.id, isouter=True)
        .where(Event.enrolment_id.in_(enrol_ids))
        .order_by(Event.occurred_at.asc())
    )).all()

    items: list[TimelineItem] = []
    enrol_seq_by_id = {e.id: e.sequence_id for e in enrolments}

    for run, step in runs:
        when = run.sent_at or run.created_at
        kind: TimelineKind = "sent" if run.status == "sent" else "error" if run.status == "failed" else "skipped" if run.status == "skipped" else "sent"
        sid = enrol_seq_by_id.get(run.enrolment_id)
        items.append(TimelineItem(
            kind=kind, at=when,
            sequence_id=sid, sequence_name=seq_name_by_id.get(sid) if sid else None,
            step_order=step.step_order, channel=step.channel,
            subject=step.subject, body=step.body,
            error=run.error_message,
            external_id=run.provider_message_id,
        ))

    for event, sent in events:
        payload = event.payload or {}
        sid = enrol_seq_by_id.get(event.enrolment_id)
        items.append(TimelineItem(
            kind=event.event_type,  # type: ignore[arg-type]
            at=event.occurred_at,
            sequence_id=sid, sequence_name=seq_name_by_id.get(sid) if sid else None,
            channel=event.channel,
            subject=payload.get("subject"),
            body=str(payload.get("snippet") or ""),
            sentiment_label=getattr(sent, "label", None),
            sentiment_confidence=getattr(sent, "confidence", None),
            sentiment_reasoning=getattr(sent, "reasoning", None),
            external_id=event.external_id,
        ))

    items.sort(key=lambda i: i.at)

    return LeadTimelineResponse(
        lead_id=lead_id,
        lead=_lead_dict(lead),
        enrolments=[_enrol_dict(e, seq_name_by_id) for e in enrolments],
        items=items,
    )


def _lead_dict(l: Lead) -> dict:
    return {
        "id": l.id, "email": l.email, "phone": l.phone, "linkedin_url": l.linkedin_url,
        "first_name": l.first_name, "last_name": l.last_name,
        "company": l.company, "title": l.title,
    }


def _enrol_dict(e: Enrolment, names: dict[int, str]) -> dict:
    return {
        "id": e.id, "sequence_id": e.sequence_id,
        "sequence_name": names.get(e.sequence_id),
        "status": e.status, "current_step_order": e.current_step_order,
        "next_send_at": e.next_send_at.isoformat() if e.next_send_at else None,
        "stopped_reason": e.stopped_reason,
    }
