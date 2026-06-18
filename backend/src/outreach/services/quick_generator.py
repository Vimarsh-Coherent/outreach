"""AI sequence generator — "describe your product → get a draft sequence".

Backs the Sequences page "Generate with AI" button. Given a free-text product
description and the channels to use, Claude designs a multi-step cold-outreach
cadence (connect note + emails + DM with realistic spacing and {{first_name}}
tokens). The result is persisted as a **draft** Sequence + Steps, each marked
`config.ai_personalize=True` so the send-time personalizer (Phase 3) tailors the
copy per lead.

Unlike the send-time personalizer, this is an interactive user action — so on AI
failure it RAISES (surfaced as a clear error in the UI) rather than failing open.
Uses the same cheap model + quota-cooldown discipline as the rest of the app.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from anthropic import AsyncAnthropic
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.config import get_settings
from outreach.schemas.sequences import (
    EmailStepCreate,
    LinkedInConnectStepCreate,
    LinkedInDmStepCreate,
    SequenceCreate,
    SequenceDetail,
)
from outreach.services import sequences_service

log = logging.getLogger("outreach.sequence_generator")

# Channels the UI may request → the step channels they unlock.
_EMAIL = "email"
_LINKEDIN = "linkedin"

# Hard guardrails enforced in code AFTER the model returns (never trust the LLM).
_CONNECT_MAX = 280
_SUBJECT_MAX = 200
_DM_MAX = 8000
_EMAIL_BODY_MAX = 16000

_EMIT_TOOL = {
    "name": "emit_sequence",
    "description": "Return the generated multi-step outreach sequence.",
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "string",
                            "enum": ["email", "linkedin_connect", "linkedin_dm"],
                        },
                        "delay_days": {"type": "integer", "minimum": 0, "maximum": 60},
                        "delay_hours": {"type": "integer", "minimum": 0, "maximum": 23},
                        "subject": {"type": "string", "description": "Email subject (email steps only)."},
                        "body": {"type": "string"},
                        "purpose": {"type": "string", "description": "One-line note on this step's goal."},
                    },
                    "required": ["channel", "delay_days", "body"],
                },
            }
        },
        "required": ["steps"],
    },
}

_SYSTEM = (
    "You are an expert B2B cold-outreach strategist. Given a product/service "
    "description and the available channels, design a concise, high-converting "
    "multi-step sequence. Call emit_sequence with the result.\n\n"
    "Rules:\n"
    "- Use ONLY the channels provided.\n"
    "- Write templates, not final copy: use {{first_name}} for the greeting, and "
    "{{company}} / {{title}} only where they read naturally. NEVER invent specific "
    "facts about the recipient.\n"
    "- Base every claim ONLY on the described product. Do not fabricate metrics, "
    "customers, or results the description does not state.\n"
    "- LinkedIn connect note: <=280 characters, warm, curiosity-led, NO hard pitch.\n"
    "- Email subjects: <=70 characters, specific and human; avoid spam triggers "
    "(free, guarantee, !!!, ALL CAPS).\n"
    "- Emails: 60-120 words, plain text, exactly one clear CTA, a real sign-off.\n"
    "- LinkedIn DM: <=120 words; assume the person just accepted the connection.\n"
    "- First touch is day 0. Space later steps with realistic delay_days "
    "(e.g. 2-4 days apart).\n"
    "- If BOTH email and LinkedIn are available: start with a linkedin_connect on "
    "day 0, then weave in emails; place any linkedin_dm AFTER the connect step so "
    "it naturally waits until the invite is accepted.\n"
    "- End an email-bearing sequence with a short, polite breakup email."
)


def _clamp(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip()


async def _generate_steps(
    *, description: str, channels: list[str], num_emails: int
) -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(503, "AI is not configured (no Anthropic API key on the backend).")

    channel_desc = []
    if _EMAIL in channels:
        channel_desc.append(f"email (aim for about {num_emails} email steps)")
    if _LINKEDIN in channels:
        channel_desc.append("linkedin_connect and linkedin_dm")
    user = (
        f"PRODUCT / SERVICE DESCRIPTION:\n{description.strip()}\n\n"
        f"AVAILABLE CHANNELS: {', '.join(channel_desc)}\n\n"
        f"Design the outreach sequence now and call emit_sequence."
    )

    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await asyncio.wait_for(
            client.messages.create(
                model=settings.personalize_model,
                max_tokens=2500,
                system=_SYSTEM,
                tools=[_EMIT_TOOL],
                tool_choice={"type": "tool", "name": "emit_sequence"},
                messages=[{"role": "user", "content": user}],
            ),
            timeout=settings.sequence_gen_timeout_seconds,
        )
    except asyncio.TimeoutError as e:
        raise HTTPException(504, "AI timed out generating the sequence — please try again.") from e
    except Exception as e:  # noqa: BLE001
        err = str(e).lower()
        if any(s in err for s in ("credit", "quota", "billing", "429", "rate_limit")):
            raise HTTPException(429, "AI quota exhausted — try again later or top up credits.") from e
        log.warning("sequence generation API call failed: %s", e)
        raise HTTPException(502, f"AI request failed: {e}") from e

    steps: list[dict[str, Any]] | None = None
    for block in resp.content:
        if getattr(block, "type", "") == "tool_use" and block.name == "emit_sequence":
            steps = (block.input or {}).get("steps")
            break
    if not isinstance(steps, list) or not steps:
        raise HTTPException(502, "AI returned an empty or malformed sequence.")
    return steps


def _to_step_models(
    raw_steps: list[dict[str, Any]], channels: list[str], zero_delays: bool = False
) -> list[Any]:
    """Validate, clamp, and convert raw AI steps into pydantic StepCreate models.

    Steps on channels the user didn't request are dropped. Each surviving step
    is tagged config.ai_personalize=True so the send-time personalizer runs.
    When ``zero_delays`` (test mode) every step's delay is forced to 0 so the
    whole cadence runs back-to-back.
    """
    allowed: set[str] = set()
    if _EMAIL in channels:
        allowed.add("email")
    if _LINKEDIN in channels:
        allowed.update({"linkedin_connect", "linkedin_dm"})

    out: list[Any] = []
    for raw in raw_steps:
        channel = str(raw.get("channel", "")).strip()
        if channel not in allowed:
            continue
        body = (raw.get("body") or "").strip()
        if not body:
            continue
        delay_days = 0 if zero_delays else max(0, min(60, int(raw.get("delay_days", 0) or 0)))
        delay_hours = 0 if zero_delays else max(0, min(23, int(raw.get("delay_hours", 0) or 0)))
        config = {"ai_personalize": True}

        if channel == "email":
            subject = _clamp(raw.get("subject") or "Quick question, {{first_name}}", _SUBJECT_MAX)
            out.append(EmailStepCreate(
                channel="email", delay_days=delay_days, delay_hours=delay_hours,
                subject=subject, body=_clamp(body, _EMAIL_BODY_MAX), config=config,
            ))
        elif channel == "linkedin_connect":
            out.append(LinkedInConnectStepCreate(
                channel="linkedin_connect", delay_days=delay_days, delay_hours=delay_hours,
                body=_clamp(body, _CONNECT_MAX), config=config,
            ))
        elif channel == "linkedin_dm":
            out.append(LinkedInDmStepCreate(
                channel="linkedin_dm", delay_days=delay_days, delay_hours=delay_hours,
                body=_clamp(body, _DM_MAX), config=config,
            ))
    return out


async def generate_sequence(
    session: AsyncSession,
    user_id: int,
    *,
    name: str,
    description: str,
    channels: list[str],
    num_emails: int,
    timezone: str = "Asia/Kolkata",
    test_mode: bool = False,
) -> SequenceDetail:
    """Generate + persist a draft sequence. Raises HTTPException on AI failure."""
    channels = [c for c in channels if c in (_EMAIL, _LINKEDIN)]
    if not channels:
        raise HTTPException(400, "Pick at least one channel (email and/or linkedin).")
    if not description.strip():
        raise HTTPException(400, "Describe your product/service so the AI has something to work with.")

    raw_steps = await _generate_steps(
        description=description, channels=channels, num_emails=max(1, min(6, num_emails)),
    )
    step_models = _to_step_models(raw_steps, channels, zero_delays=test_mode)
    if not step_models:
        raise HTTPException(502, "AI produced no usable steps for the selected channels — try again.")

    # In test mode, open the window to ~24/7 every day so steps aren't deferred
    # to business hours — combined with zeroed delays the cadence runs at once.
    from datetime import time as _time

    seq_kwargs: dict[str, Any] = {}
    if test_mode:
        seq_kwargs = {
            "send_window_start": _time(0, 0),
            "send_window_end": _time(23, 59),
            "send_days_mask": 127,
        }

    # Persist: draft sequence, then steps in order. ai_followups_enabled so the
    # existing Sonnet follow-up drafter also benefits from the cadence.
    seq = await sequences_service.create_sequence(
        session, user_id,
        SequenceCreate(
            name=name.strip()[:120] or "AI sequence",
            description=description.strip()[:4000],
            timezone=timezone,
            ai_followups_enabled=True,
            **seq_kwargs,
        ),
    )
    for step in step_models:
        await sequences_service.add_step(session, user_id, seq.id, step)

    return await sequences_service.get_sequence_detail(session, user_id, seq.id)
