from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from outreach.channels.email_channel import send_email
from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.channel import Channel
from outreach.models.enrolment import Enrolment
from outreach.models.lead import Lead
from outreach.models.li_command import LinkedInCommand
from outreach.models.sequence import Sequence
from outreach.models.step import SequenceStep
from outreach.models.user import User
from outreach.schemas.channels import SMTPConfig
from outreach.schemas.followup import (
    DraftRequest,
    DraftResponse,
    SendRequest,
    SendResponse,
)
from outreach.services.followup_agent import draft_for_lead
from outreach.services.threading_email import make_message_id
from outreach.utils.crypto import decrypt_json

router = APIRouter(prefix="/api/followups", tags=["followups"])


@router.post("/draft", response_model=DraftResponse)
async def draft(
    req: DraftRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    lead = await session.scalar(
        select(Lead).where(Lead.id == req.lead_id, Lead.user_id == user.id)
    )
    if lead is None:
        raise HTTPException(404, "lead not found")

    # If template_subject/body weren't passed, pick the highest step_order from
    # the chosen sequence (or any active sequence) as the source template.
    template_subject = req.template_subject
    template_body = req.template_body
    if template_subject is None or template_body is None:
        step: SequenceStep | None = None
        if req.sequence_id is not None:
            step = await session.scalar(
                select(SequenceStep)
                .where(SequenceStep.sequence_id == req.sequence_id, SequenceStep.channel == "email")
                .order_by(SequenceStep.step_order.desc())
                .limit(1)
            )
        else:
            # Any email step from any sequence owned by this user.
            step = await session.scalar(
                select(SequenceStep)
                .join(Sequence, Sequence.id == SequenceStep.sequence_id)
                .where(Sequence.user_id == user.id, SequenceStep.channel == "email")
                .order_by(SequenceStep.id.desc())
                .limit(1)
            )
        if step is not None:
            template_subject = template_subject or step.subject or "Following up"
            template_body = template_body or step.body
        else:
            template_subject = template_subject or "Following up"
            template_body = template_body or "Hi {{first_name}},\n\nWanted to follow up — let me know what you think.\n\nBest,"

    draft = await draft_for_lead(
        session, lead.id,
        template_subject=template_subject,
        template_body=template_body,
    )
    return DraftResponse(
        subject=draft.subject, body=draft.body, notes=draft.notes,
        template_subject=draft.template_subject, template_body=draft.template_body,
        similar_snippets=draft.similar_snippets,
        prior_history_count=draft.prior_history_count,
        model=draft.model,
    )


@router.post("/send", response_model=SendResponse)
async def send(
    req: SendRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SendResponse:
    # Pick channel
    if req.channel_id is not None:
        channel = await session.scalar(
            select(Channel).where(
                Channel.id == req.channel_id,
                Channel.user_id == user.id,
                Channel.channel_type == "email",
            )
        )
    else:
        channel = await session.scalar(
            select(Channel)
            .where(Channel.user_id == user.id, Channel.channel_type == "email", Channel.status == "active")
            .order_by(Channel.id.asc())
            .limit(1)
        )
    if channel is None:
        raise HTTPException(400, "no active email channel configured")

    try:
        cfg_dict = decrypt_json(channel.config_encrypted)
        cfg = SMTPConfig.model_validate(cfg_dict["smtp"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"channel config invalid: {e}") from e

    # We don't have a step_run id for one-off sends; mint a stable HMAC id from
    # a synthetic anchor (negative integer to distinguish from step_run ids).
    # If the same lead replies, reply_processor won't find a matching step_run
    # and the reply will be classified 'unrelated' — that's the right behaviour
    # for an ad-hoc send: it lives outside the cadence.
    message_id = make_message_id(-int(req.lead_id))

    result = await send_email(
        cfg=cfg, to_address=req.to_email,
        subject=req.subject, body=req.body, message_id=message_id,
    )
    return SendResponse(
        ok=result.ok, detail=result.error or "sent",
        provider_message_id=message_id if result.ok else None,
    )


class SendLinkedInRequest(BaseModel):
    lead_id: int
    body: str


@router.post("/send-linkedin")
async def send_linkedin(
    req: SendLinkedInRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    lead = await session.scalar(
        select(Lead).where(Lead.id == req.lead_id, Lead.user_id == user.id)
    )
    if lead is None:
        raise HTTPException(404, "lead not found")
    if not lead.linkedin_url:
        raise HTTPException(400, "lead has no LinkedIn URL")

    enrolment = await session.scalar(
        select(Enrolment)
        .where(Enrolment.lead_id == lead.id, Enrolment.user_id == user.id)
        .order_by(Enrolment.id.desc())
        .limit(1)
    )
    if enrolment is None:
        raise HTTPException(400, "no enrolment found for this lead")

    cmd = LinkedInCommand(
        user_id=user.id,
        enrolment_id=enrolment.id,
        step_id=None,
        command_type="dm",
        target_li_url=lead.linkedin_url,
        body_text=req.body,
        status="pending",
    )
    session.add(cmd)
    await session.commit()
    return {"ok": True, "command_id": cmd.id}
