"""AI-powered sequence generation from knowledge base + user prompt."""
from __future__ import annotations

import logging
from datetime import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.config import get_settings
from outreach.models.sequence import Sequence
from outreach.models.step import SequenceStep
from outreach.schemas.ai_sequences import (
    AIStepDraft,
    AISequenceDraft,
    AISequenceGenerateRequest,
)
from outreach.schemas.sequences import SequenceCreate, SequenceDetail
from outreach.services import llm_client, sequences_service, vault
from outreach.services.knowledge_ingest import extract_and_chunk

log = logging.getLogger("outreach.ai_sequence")

DEFAULT_MODEL = "claude-haiku-4-5"

BODY_LIMITS = {
    "email": 16000,
    "linkedin_dm": 8000,
    "linkedin_connect": 300,
    "call": 4000,
    "sms": 1600,
    "whatsapp": 4000,
}

SYSTEM_PROMPT = (
    "You are an expert B2B outreach strategist. Design multi-step cold outreach "
    "sequences that are concise, human, and conversion-focused. Follow the user's "
    "instructions exactly for step order, channels, and delays. Use merge tokens "
    "{{first_name}}, {{last_name}}, {{company}}, {{title}} in email/LinkedIn bodies. "
    "{{meeting_link}} is the sender's scheduling URL (Calendly/Cal.com/etc). "
    "Never put {{meeting_link}} in the first email step — use it from the second "
    "email onward when the step's goal is booking a meeting. AI follow-ups include "
    "the link only when reply sentiment is positive; negative replies get a normal "
    "follow-up without a calendar link. "
    "Never use em-dashes. Return JSON only."
)

GENERATE_TEMPLATE = """
USER CAMPAIGN PROMPT:
{prompt}

KNOWLEDGE BASE CONTEXT (from uploaded docs):
{knowledge_context}

RULES:
- Generate 6-10 steps unless the user specifies a different count or partial workflow.
- Supported channels: email, linkedin_connect, linkedin_connect note in body, linkedin_dm, call, sms, whatsapp.
- If the user specifies step order and channels, follow EXACTLY.
- If the user specifies delays (e.g. "wait 2 days"), set delay_days/delay_hours on the NEXT step after the wait.
- First step should have delay_days=0 and delay_hours=0 unless user says otherwise.
- email steps MUST include subject (max 120 chars) and body.
- linkedin_connect body is the connection note (max 300 chars).
- linkedin_dm, call, sms, whatsapp have subject=null.
- call/sms/whatsapp body = task script or message template for the rep.
- Include a final breakup/follow-up step if not specified.
- Use outreach best practices when user gives partial instructions.
- Mark each step config with {{"ai_generated": true, "step_label": "<short label>"}}.

Return JSON:
{{
  "name": "<sequence name max 120 chars>",
  "description": "<1-2 sentence description>",
  "timezone": "<IANA timezone e.g. Asia/Kolkata or America/New_York>",
  "send_window_start": "09:00",
  "send_window_end": "18:00",
  "send_days_mask": 31,
  "ai_followups_enabled": true,
  "steps": [
    {{
      "channel": "email|linkedin_dm|linkedin_connect|call|sms|whatsapp",
      "delay_days": 0,
      "delay_hours": 0,
      "subject": "<required for email, null otherwise>",
      "body": "<content>",
      "config": {{"ai_generated": true, "step_label": "Initial Email"}}
    }}
  ]
}}
"""

REGENERATE_TEMPLATE = """
Regenerate ONE outreach step. Keep channel unless user prompt says otherwise.

SEQUENCE: {sequence_name}
STEP ORDER: {step_order} of {total_steps}
CURRENT CHANNEL: {channel}
CURRENT SUBJECT: {subject}
CURRENT BODY:
{body}

OTHER STEPS (for context):
{other_steps}

KNOWLEDGE BASE:
{knowledge_context}

USER INSTRUCTION (optional):
{user_prompt}

Return JSON for this step only:
{{"channel": "...", "delay_days": N, "delay_hours": N, "subject": "...", "body": "...", "config": {{"ai_generated": true, "step_label": "..."}}}}
"""


def _parse_json(text: str) -> dict | None:
    # Full-sequence responses have "steps"; single-step regenerations have
    # "channel"/"body". prefer_keys=("steps",) grabs the sequence object when one
    # is present and otherwise falls back to the first valid object (the step).
    return llm_client.extract_json_object(text, prefer_keys=("steps",))


def _parse_time(value: str) -> time:
    parts = value.strip().split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    return time(h, m)


def _clamp_step(raw: dict) -> AIStepDraft:
    channel = str(raw.get("channel") or "email")
    if channel not in BODY_LIMITS:
        channel = "email"
    limit = BODY_LIMITS[channel]
    body = str(raw.get("body") or "").strip()[:limit]
    if not body:
        body = "Hi {{first_name}}, reaching out regarding {{company}}."
    subject = raw.get("subject")
    if channel == "email":
        subject = str(subject or "Quick question for {{first_name}}")[:250]
    else:
        subject = None
    cfg = dict(raw.get("config") or {})
    cfg["ai_generated"] = True
    return AIStepDraft(
        channel=channel,  # type: ignore[arg-type]
        delay_days=min(max(int(raw.get("delay_days") or 0), 0), 365),
        delay_hours=min(max(int(raw.get("delay_hours") or 0), 0), 23),
        subject=subject,
        body=body,
        config=cfg,
    )


def _draft_from_payload(payload: dict) -> AISequenceDraft:
    steps_raw = payload.get("steps") or []
    if not isinstance(steps_raw, list) or len(steps_raw) < 1:
        raise HTTPException(400, "AI returned no steps")
    steps = [_clamp_step(s) for s in steps_raw[:12]]
    if len(steps) < 6:
        # Pad minimally if AI returned too few — user can edit
        pass
    return AISequenceDraft(
        name=str(payload.get("name") or "AI Outreach Sequence")[:120],
        description=str(payload.get("description") or "")[:4000] or None,
        timezone=str(payload.get("timezone") or "Asia/Kolkata")[:64],
        send_window_start=str(payload.get("send_window_start") or "09:00"),
        send_window_end=str(payload.get("send_window_end") or "18:00"),
        send_days_mask=min(max(int(payload.get("send_days_mask") or 31), 1), 127),
        ai_followups_enabled=bool(payload.get("ai_followups_enabled", True)),
        steps=steps,
    )


async def upload_knowledge(
    user_id: int,
    files: list[tuple[str, bytes]],
) -> tuple[str, int, list[str]]:
    knowledge_id = uuid4().hex[:16]
    all_chunks: list[dict[str, str]] = []
    filenames: list[str] = []
    for filename, content in files:
        if not content:
            continue
        chunks = extract_and_chunk(filename, content)
        all_chunks.extend(chunks)
        filenames.append(filename)
    if not filenames:
        raise HTTPException(400, "no valid files uploaded")
    result = vault.index_knowledge(user_id, knowledge_id, all_chunks)
    indexed = int(result.get("indexed") or 0)
    return knowledge_id, indexed, filenames


async def generate_draft(
    user_id: int,
    dto: AISequenceGenerateRequest,
) -> tuple[AISequenceDraft, int]:
    get_settings.cache_clear()
    if llm_client.active_provider() is None:
        raise HTTPException(503, "No LLM API key configured (set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY)")

    snippets: list[str] = []
    if dto.knowledge_id:
        snippets = vault.get_knowledge_similar(user_id, dto.knowledge_id, dto.prompt, n=8)

    knowledge_context = "\n".join(f"- {s[:500]}" for s in snippets) or "(no knowledge base uploaded)"
    user_msg = GENERATE_TEMPLATE.format(
        prompt=dto.prompt.strip(),
        knowledge_context=knowledge_context,
    )

    try:
        result = await llm_client.complete(
            system=SYSTEM_PROMPT, user=user_msg, max_tokens=4096, anthropic_model=DEFAULT_MODEL,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("AI sequence generation failed")
        msg = str(e).strip() or type(e).__name__
        if "authentication" in msg.lower() or type(e).__name__ == "AuthenticationError":
            raise HTTPException(
                502,
                "AI generation failed: invalid API key. "
                "Update DEEPSEEK_API_KEY (or ANTHROPIC_API_KEY) in backend/.env and restart the backend.",
            ) from e
        raise HTTPException(502, f"AI generation failed: {msg}") from e

    payload = _parse_json(result.text)
    if not payload:
        raise HTTPException(502, "AI returned invalid JSON")
    return _draft_from_payload(payload), len(snippets)


async def save_draft(
    session: AsyncSession,
    user_id: int,
    draft: AISequenceDraft,
    knowledge_id: str | None,
) -> SequenceDetail:
    seq_dto = SequenceCreate(
        name=draft.name,
        description=draft.description,
        timezone=draft.timezone,
        send_window_start=_parse_time(draft.send_window_start),
        send_window_end=_parse_time(draft.send_window_end),
        send_days_mask=draft.send_days_mask,
        ai_followups_enabled=draft.ai_followups_enabled,
    )
    seq_out = await sequences_service.create_sequence(session, user_id, seq_dto)
    seq = await session.get(Sequence, seq_out.id)
    if seq and knowledge_id:
        seq.ai_knowledge_id = knowledge_id
        await session.commit()

    for i, step in enumerate(draft.steps, start=1):
        await _add_step_from_draft(session, user_id, seq_out.id, step, i)

    return await sequences_service.get_sequence_detail(session, user_id, seq_out.id)


async def _add_step_from_draft(
    session: AsyncSession,
    user_id: int,
    sequence_id: int,
    step: AIStepDraft,
    order: int,
) -> None:
    from outreach.schemas.sequences import (
        EmailStepCreate,
        LinkedInConnectStepCreate,
        LinkedInDmStepCreate,
        LinkedInLikeStepCreate,
        ManualTaskStepCreate,
    )

    common = dict(
        step_order=order,
        delay_days=step.delay_days,
        delay_hours=step.delay_hours,
        config=step.config,
    )
    if step.channel == "email":
        dto = EmailStepCreate(
            channel="email",
            subject=step.subject or "Quick question",
            body=step.body,
            **common,
        )
    elif step.channel == "linkedin_dm":
        dto = LinkedInDmStepCreate(channel="linkedin_dm", subject=None, body=step.body, **common)
    elif step.channel == "linkedin_connect":
        dto = LinkedInConnectStepCreate(
            channel="linkedin_connect", subject=None, body=step.body, **common
        )
    elif step.channel == "linkedin_like":
        # No message content — body must be empty per LinkedInLikeStepCreate.
        dto = LinkedInLikeStepCreate(channel="linkedin_like", subject=None, body="", **common)
    else:
        dto = ManualTaskStepCreate(
            channel=step.channel,  # type: ignore[arg-type]
            subject=step.subject,
            body=step.body,
            **common,
        )
    await sequences_service.add_step(session, user_id, sequence_id, dto)


async def regenerate_step(
    session: AsyncSession,
    user_id: int,
    sequence_id: int,
    step_id: int,
    user_prompt: str | None,
) -> SequenceDetail:
    get_settings.cache_clear()
    if llm_client.active_provider() is None:
        raise HTTPException(503, "No LLM API key configured (set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY)")

    detail = await sequences_service.get_sequence_detail(session, user_id, sequence_id)
    step = next((s for s in detail.steps if s.id == step_id), None)
    if step is None:
        raise HTTPException(404, "step not found")

    seq_row = await session.get(Sequence, sequence_id)
    knowledge_id = seq_row.ai_knowledge_id if seq_row else None
    snippets: list[str] = []
    if knowledge_id:
        q = user_prompt or step.body[:200]
        snippets = vault.get_knowledge_similar(user_id, knowledge_id, q, n=6)

    other = "\n".join(
        f"  Step {s.step_order} ({s.channel}): {(s.body or '')[:120]}..."
        for s in detail.steps if s.id != step_id
    )
    user_msg = REGENERATE_TEMPLATE.format(
        sequence_name=detail.name,
        step_order=step.step_order,
        total_steps=len(detail.steps),
        channel=step.channel,
        subject=step.subject or "(none)",
        body=step.body,
        other_steps=other or "(none)",
        knowledge_context="\n".join(f"- {s[:400]}" for s in snippets) or "(none)",
        user_prompt=user_prompt or "(regenerate with best-practice improvements)",
    )

    try:
        result = await llm_client.complete(
            system=SYSTEM_PROMPT, user=user_msg, max_tokens=1200, anthropic_model=DEFAULT_MODEL,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("AI step regeneration failed")
        raise HTTPException(502, f"AI regeneration failed: {str(e).strip() or type(e).__name__}") from e
    payload = _parse_json(result.text)
    if not payload:
        raise HTTPException(502, "AI returned invalid JSON for step regeneration")

    new_step = _clamp_step(payload)
    from outreach.schemas.sequences import StepCreate

    # Build StepCreate via service update - reuse add logic types
    step_row = await session.get(SequenceStep, step_id)
    if step_row is None:
        raise HTTPException(404, "step not found")

    step_row.channel = new_step.channel
    step_row.delay_days = new_step.delay_days
    step_row.delay_hours = new_step.delay_hours
    step_row.subject = new_step.subject
    step_row.body = new_step.body
    step_row.config = {**(step.config or {}), **new_step.config}
    await session.commit()

    return await sequences_service.get_sequence_detail(session, user_id, sequence_id)
