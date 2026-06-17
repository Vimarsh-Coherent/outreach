"""AI follow-up drafting agent.

Pipeline (per PLAN.md §6.3):
1. Load lead profile + last N prior events (sent + inbound) for this lead.
2. Open the lead's Qdrant vault, pull k similar prior snippets.
3. Send a structured prompt to Claude Haiku.
4. Parse JSON response: {subject, body, notes}.

Used in two modes:
  - Manual: POST /api/followups/draft from UI
  - Auto: inside dispatcher.process_one when sequence.ai_followups_enabled
          and we're about to send step >= 2.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx
from anthropic import AsyncAnthropic
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.config import get_settings
from outreach.models.event import Event, ReplySentiment
from outreach.models.lead import Lead
from outreach.models.sequence import Sequence
from outreach.models.step import SequenceStep
from outreach.models.step_run import StepRun
from outreach.services import vault
from outreach.services.template_render import render

log = logging.getLogger("outreach.followup")

DEFAULT_MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = (
    "You are a senior sales-development writer drafting personalised follow-up "
    "emails for a cold-outreach campaign. You write in a concise, human, "
    "professional tone. Never use buzzwords or salesy phrases like 'circling back' "
    "or 'just checking in'. Never use em-dashes."
)

USER_TEMPLATE = """
LEAD:
  Name: {first_name} {last_name}
  Title: {title}
  Company: {company}
  Email: {email}

PRIOR CONVERSATION (most recent last):
{prior_history}

SEMANTICALLY SIMILAR CONTEXT FROM THIS LEAD'S HISTORY:
{vault_snippets}

CURRENT STEP TEMPLATE (this is what would be sent if you didn't override):
Subject: {template_subject}
Body:
{template_body}

INSTRUCTIONS:
- Rewrite the template as a personalised follow-up.
- Max 150 words.
- Reference one concrete detail from the lead profile or prior conversation.
- If the lead has already replied with an objection, address it directly.
- If the lead is interested, propose a specific next step (a 15-min call,
  sending a doc, etc.) instead of vague language.
- Do NOT repeat lines verbatim from earlier sends.
- Keep the original signature style.

Return JSON only:
{{"subject": "<<follow-up subject>>", "body": "<<plain text body>>", "notes": "<<<=120 chars: why this draft will land>>"}}
"""


@dataclass(slots=True)
class FollowUpDraft:
    subject: str
    body: str
    notes: str
    template_subject: str
    template_body: str
    similar_snippets: list[str]
    prior_history_count: int
    model: str


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def _load_prior_history(session: AsyncSession, lead_id: int, n: int = 5) -> tuple[list[str], int]:
    """Return last `n` events on this lead, formatted as plain-text bullets."""
    # Fetch ENROL ids first
    from outreach.models.enrolment import Enrolment
    enrol_ids = list((await session.execute(
        select(Enrolment.id).where(Enrolment.lead_id == lead_id)
    )).scalars().all())
    if not enrol_ids:
        return [], 0

    # Sent runs
    sent = (await session.execute(
        select(StepRun, SequenceStep)
        .join(SequenceStep, SequenceStep.id == StepRun.step_id)
        .where(StepRun.enrolment_id.in_(enrol_ids), StepRun.status == "sent")
        .order_by(desc(StepRun.sent_at))
        .limit(n)
    )).all()

    # Inbound events
    events = (await session.execute(
        select(Event, ReplySentiment)
        .join(ReplySentiment, ReplySentiment.event_id == Event.id, isouter=True)
        .where(Event.enrolment_id.in_(enrol_ids),
               Event.event_type.in_(("reply", "auto_reply", "bounce")))
        .order_by(desc(Event.occurred_at))
        .limit(n)
    )).all()

    items: list[tuple[any, str]] = []
    for run, step in sent:
        when = run.sent_at
        snippet = f"[SENT step {step.step_order}, {when:%Y-%m-%d}] Subject: {step.subject!r}. Body: {(step.body or '')[:200]}"
        items.append((when, snippet))
    for event, sent_row in events:
        when = event.occurred_at
        payload = event.payload or {}
        senti = getattr(sent_row, "label", None)
        senti_tag = f" [sentiment: {senti} {sent_row.confidence:.2f}]" if sent_row else ""
        snippet = (
            f"[{event.event_type.upper()}, {when:%Y-%m-%d}{senti_tag}] "
            f"From: {payload.get('from')}. Subject: {payload.get('subject')!r}. "
            f"Body: {(payload.get('snippet') or '')[:300]}"
        )
        items.append((when, snippet))

    items.sort(key=lambda i: i[0])
    return [s for _, s in items[-n:]], len(items)


async def draft_for_lead(
    session: AsyncSession,
    lead_id: int,
    *,
    template_subject: str,
    template_body: str,
    contact_snapshot: dict | None = None,
    model: str = DEFAULT_MODEL,
) -> FollowUpDraft:
    settings = get_settings()

    lead = await session.scalar(select(Lead).where(Lead.id == lead_id))
    if lead is None:
        raise ValueError(f"lead {lead_id} not found")

    snap = contact_snapshot or {
        "email": lead.email, "first_name": lead.first_name,
        "last_name": lead.last_name, "company": lead.company, "title": lead.title,
    }

    prior_history, prior_count = await _load_prior_history(session, lead_id, n=5)

    # Build a retrieval query: pick a hot signal from history if possible.
    query = ""
    for h in reversed(prior_history):
        if "REPLY" in h or "AUTO_REPLY" in h:
            query = h
            break
    if not query and prior_history:
        query = prior_history[-1]
    if not query:
        query = f"{lead.company} {lead.title} {lead.first_name} {lead.last_name}".strip()
    similar = await vault.get_similar(lead_id, query, n=4)

    rendered_template_subject = render(template_subject, snap)
    rendered_template_body = render(template_body, snap)

    user = USER_TEMPLATE.format(
        first_name=lead.first_name or "",
        last_name=lead.last_name or "",
        title=lead.title or "",
        company=lead.company or "",
        email=lead.email or "",
        prior_history="\n".join(f"  - {h}" for h in prior_history) or "  (no prior conversation)",
        vault_snippets="\n".join(f"  - {s[:400]}" for s in similar) or "  (no similar context yet)",
        template_subject=rendered_template_subject,
        template_body=rendered_template_body,
    )

    if not settings.anthropic_api_key:
        return FollowUpDraft(
            subject=rendered_template_subject,
            body=rendered_template_body,
            notes="ANTHROPIC_API_KEY not set — returned template unchanged.",
            template_subject=rendered_template_subject,
            template_body=rendered_template_body,
            similar_snippets=similar,
            prior_history_count=prior_count,
            model="fallback",
        )

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=600,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
    except (httpx.HTTPError, Exception) as e:  # noqa: BLE001
        log.warning("followup draft call failed: %s", e)
        return FollowUpDraft(
            subject=rendered_template_subject,
            body=rendered_template_body,
            notes=f"LLM call failed: {type(e).__name__}",
            template_subject=rendered_template_subject,
            template_body=rendered_template_body,
            similar_snippets=similar,
            prior_history_count=prior_count,
            model="fallback",
        )
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    payload = _parse_json(text) or {}
    return FollowUpDraft(
        subject=str(payload.get("subject") or rendered_template_subject)[:250],
        body=str(payload.get("body") or rendered_template_body),
        notes=str(payload.get("notes") or "")[:300],
        template_subject=rendered_template_subject,
        template_body=rendered_template_body,
        similar_snippets=similar,
        prior_history_count=prior_count,
        model=model,
    )
