"""Sentiment classification of inbound replies via Claude Haiku.

We classify into the 7 buckets defined in PLAN.md §6.1. The label is stored in
`reply_sentiment` and is the input both to the dashboard (M8) and to the AI
follow-up agent (M9).
"""
from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.models.event import Event, ReplySentiment
from outreach.models.step_run import StepRun
from outreach.services import llm_client

log = logging.getLogger("outreach.sentiment")

SentimentLabel = Literal[
    "positive", "interested", "objection",
    "negative", "unsubscribe", "auto_reply", "neutral",
]
VALID_LABELS: set[str] = {
    "positive", "interested", "objection",
    "negative", "unsubscribe", "auto_reply", "neutral",
}

DEFAULT_MODEL = "claude-haiku-4-5"  # Anthropic model when DeepSeek isn't configured

SYSTEM_PROMPT = (
    "You classify cold-outreach reply messages (email, LinkedIn, WhatsApp). "
    "Read the reply (and the original outreach for context) and pick exactly one label. "
    "Be conservative: prefer 'neutral' over 'positive' unless intent is clear — but a reply "
    "that expresses willingness, enthusiasm, or agreement toward the outreach's ask "
    "(accepting a connection warmly, \"happy to\", \"would love to\", \"sounds great\", "
    "\"sure, let's do it\") IS clear positive intent, even if short. Reserve 'neutral' for "
    "replies with no expressed sentiment either way.\n\n"
    "Labels:\n"
    " - positive     : interested, asking for info / call / meeting, or a warm/willing "
    "response to the outreach's ask (e.g. gladly accepting a connection or invite)\n"
    " - interested   : lukewarm engagement (\"send details\", \"next quarter maybe\")\n"
    " - objection    : specific objection (price, timing, wrong person)\n"
    " - negative     : clearly not interested but not opt-out\n"
    " - unsubscribe  : asks to stop, remove, opt out\n"
    " - auto_reply   : OOO / vacation / automated bounce-like\n"
    " - neutral      : flat acknowledgement with no expressed sentiment (\"thanks\", \"got it\", \"ok\")"
)

USER_TEMPLATE = (
    "ORIGINAL OUTREACH:\n---\n{outreach}\n---\n\n"
    "INBOUND REPLY (from {from_addr}, subject: {subject!r}):\n---\n{reply}\n---\n\n"
    "Return JSON only: "
    '{{"label": "...", "confidence": 0.0-1.0, "reasoning": "<=200 chars"}}'
)


def _parse_json_response(text: str) -> dict | None:
    """Extract the classification JSON. Robust to markdown fences + surrounding
    prose (DeepSeek/Claude both add these intermittently)."""
    return llm_client.extract_json_object(text, prefer_keys=("label",))


async def classify_reply(
    *, reply_body: str, original_subject: str | None, original_body: str | None,
    inbound_subject: str | None, inbound_from: str | None,
    model: str = DEFAULT_MODEL,
) -> tuple[SentimentLabel, float, str]:
    """Call Claude Haiku. Returns (label, confidence, reasoning).

    On any error returns ('neutral', 0.0, <error message>) — sentiment is
    advisory; we should never block the reply pipeline on a classifier hiccup.

    Runs on the active LLM provider (DeepSeek if configured, else Anthropic Haiku).
    """
    if llm_client.active_provider() is None:
        return "neutral", 0.0, "no LLM API key configured"

    outreach_str = f"Subject: {original_subject or '(none)'}\n\n{original_body or '(missing)'}"
    user = USER_TEMPLATE.format(
        outreach=outreach_str[:4000],
        from_addr=inbound_from or "(unknown)",
        subject=inbound_subject or "(none)",
        reply=(reply_body or "")[:4000],
    )

    try:
        # 200 was too tight — DeepSeek intermittently spends most/all of that
        # budget before finishing the JSON (observed: empty or truncated
        # mid-string output), which silently fell back to neutral/0.0 below.
        result = await llm_client.complete(
            system=SYSTEM_PROMPT, user=user, max_tokens=400, anthropic_model=model,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("sentiment call failed: %s", e)
        return "neutral", 0.0, f"api error: {type(e).__name__}"

    payload = _parse_json_response(result.text) or {}
    label = str(payload.get("label", "neutral")).strip().lower()
    if label not in VALID_LABELS:
        label = "neutral"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reasoning = str(payload.get("reasoning", ""))[:500]
    return label, confidence, reasoning  # type: ignore[return-value]


async def _load_original_for_event(session: AsyncSession, event: Event) -> tuple[str | None, str | None]:
    """Return (subject, body) of the outreach we sent that triggered this reply."""
    if not event.step_run_id:
        return None, None
    run = await session.scalar(
        select(StepRun).where(StepRun.id == event.step_run_id)
    )
    if run is None:
        return None, None
    # We don't store the rendered subject/body on step_run today (would bloat the
    # row). The step template is a decent proxy.
    from outreach.models.step import SequenceStep
    step = await session.scalar(select(SequenceStep).where(SequenceStep.id == run.step_id))
    if step is None:
        return None, None
    return step.subject, step.body


async def classify_and_store(session: AsyncSession, event_id: int) -> dict:
    """End-to-end: load event -> classify -> upsert reply_sentiment row.

    Also auto-suppresses the contact when the label is 'unsubscribe'.
    """
    event = await session.scalar(select(Event).where(Event.id == event_id))
    if event is None or event.event_type not in ("reply", "auto_reply", "bounce"):
        return {"event_id": event_id, "skipped": "not_classifiable_type"}

    # Skip if already classified.
    existing = await session.scalar(
        select(ReplySentiment.event_id).where(ReplySentiment.event_id == event_id)
    )
    if existing is not None:
        return {"event_id": event_id, "skipped": "already_classified"}

    payload = event.payload or {}
    reply_body = str(payload.get("snippet") or "")
    inbound_subject = payload.get("subject")
    inbound_from = payload.get("from")
    original_subject, original_body = await _load_original_for_event(session, event)

    if event.event_type == "auto_reply":
        # OOO is already classified by the parser; persist that without an LLM hop.
        label, confidence, reasoning = "auto_reply", 0.95, "classified by IMAP header heuristics"
        model = "rules-v1"
    elif event.event_type == "bounce":
        label, confidence, reasoning = "negative", 1.0, "delivery failure"
        model = "rules-v1"
    else:
        label, confidence, reasoning = await classify_reply(
            reply_body=reply_body,
            original_subject=original_subject,
            original_body=original_body,
            inbound_subject=inbound_subject,
            inbound_from=inbound_from,
        )
        model = "deepseek" if llm_client.active_provider() == "deepseek" else DEFAULT_MODEL

    stmt = insert(ReplySentiment).values(
        event_id=event_id, label=label, confidence=confidence,
        reasoning=reasoning, model=model,
    ).on_conflict_do_nothing(index_elements=[ReplySentiment.event_id])
    await session.execute(stmt)

    suppressed = False
    if label == "unsubscribe":
        from outreach.models.enrolment import Enrolment
        from outreach.services.identity import canonical_identity
        from outreach.services.suppressions_service import add_suppression
        enrolment = await session.scalar(
            select(Enrolment).where(Enrolment.id == event.enrolment_id)
        )
        if enrolment is not None:
            snap = enrolment.contact_snapshot or {}
            ident = canonical_identity(
                email=snap.get("email"), phone=snap.get("phone"),
                linkedin=snap.get("linkedin_url"),
            )
            if ident.hash is not None:
                # Suppress all channels with '*' wildcard for this contact.
                await add_suppression(session, enrolment.user_id, ident.hash, "*", "unsubscribe")
                suppressed = True
    await session.commit()
    return {
        "event_id": event_id, "label": label, "confidence": confidence,
        "reasoning": reasoning, "model": model, "auto_suppressed": suppressed,
    }
