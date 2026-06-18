"""Send-time per-lead personalization (Phase 3).

Rewrites a token-rendered template into a warm, human, per-lead message using a
cheap model (Claude Haiku). This is a *best-effort enhancement*: it must NEVER
block or fail a send. Every error path — no API key, cooldown, timeout, bad
output — returns the original template with ``used_ai=False``. No exception ever
escapes the ``personalize_*`` functions.

Design mirrors ``selector_healer.py``:
  - AsyncAnthropic + claude-haiku-4-5-20251001 (via settings.personalize_model)
  - module-level fail-fast cooldown after an API error, self-resetting
  - quota errors get a long cooldown, transient errors a short one

The template defines the intent / offer / CTA. The model only *personalizes*
from the lead's known snapshot fields and (optionally) the offering's extracted
facts — it is instructed to NEVER invent facts about the person or the product.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from outreach.config import get_settings
from outreach.services import llm_client

log = logging.getLogger("outreach.personalizer")


@dataclass
class PersonalizedCopy:
    subject: str | None
    body: str
    used_ai: bool  # False on any fallback to the rendered template


# Hard length ceilings enforced in code AFTER the model call. The connect note
# limit is LinkedIn's invite-note maximum; the DM limit keeps messages tight.
_LIMITS: dict[str, int] = {"connect": 280, "dm": 1200}

# Fail-fast cooldown (see selector_healer for the rationale). After an API error
# every subsequent call would be a doomed network round-trip inside a tick
# worker, adding up to `personalize_timeout_seconds` of latency per send. So we
# short-circuit to the template for a window: long for quota/billing (won't fix
# itself for a while), short for transient. Self-resets when the window passes.
_cooldown_until: float = 0.0
_QUOTA_COOLDOWN_S = 6 * 3600
_TRANSIENT_COOLDOWN_S = 15 * 60

_SYSTEM = (
    "You rewrite outreach copy to feel personal and human for one specific "
    "recipient. You are given a TEMPLATE that defines the intent, offer and "
    "call-to-action, plus the recipient's known details (and optionally facts "
    "about the sender's product). Produce the final message.\n"
    "Hard rules:\n"
    "- Keep the template's intent, offer and call-to-action. Do NOT change what "
    "is being pitched or asked.\n"
    "- Personalize ONLY from the details provided. NEVER invent facts about the "
    "recipient (role, company, achievements) or the product. If only a first "
    "name is known, keep it light — a natural greeting, no fabricated context.\n"
    "- Similar length to the template. Plain text only: no markdown, no hashtags, "
    "no emojis unless the template has them, no surrounding quotes.\n"
    "- Resolve any leftover placeholders like {{first_name}}; never output them.\n"
    "- Output ONLY the final message text — no preamble, no explanation."
)


def _lead_details(snapshot: dict[str, Any] | None) -> str:
    snap = snapshot or {}
    fields = {
        "first name": snap.get("first_name"),
        "last name": snap.get("last_name"),
        "title": snap.get("title"),
        "company": snap.get("company"),
        "location": snap.get("location"),
        "industry": snap.get("industry"),
    }
    lines = [f"- {k}: {v}" for k, v in fields.items() if v]
    return "\n".join(lines) if lines else "- (only a first name is known)"


def _offering_block(offering: Any | None) -> str:
    """Render product context from an Offering, if one is attached.

    Tolerant of `None` (manual sequences) and of partial `extracted` data — the
    Offering model may not exist yet (Phase 1), so this stays duck-typed.
    """
    if offering is None:
        return ""
    extracted = getattr(offering, "extracted", None) or {}
    parts: list[str] = []
    one_liner = extracted.get("one_liner") or getattr(offering, "description", None)
    if one_liner:
        parts.append(f"What it is: {one_liner}")
    value_props = extracted.get("value_props") or []
    if value_props:
        top = "; ".join(str(v) for v in value_props[:3])
        parts.append(f"Value props: {top}")
    cta = extracted.get("cta")
    if cta:
        parts.append(f"Preferred CTA: {cta}")
    if not parts:
        return ""
    return "PRODUCT CONTEXT (sender's offering — use only these facts):\n" + "\n".join(parts)


def _truncate_at_sentence(text: str, limit: int) -> str | None:
    """Trim to <= limit at the last sentence boundary. None if no clean cut."""
    text = text.strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    best = -1
    for sep in (". ", "! ", "? ", ".\n", "!\n", "?\n", "\n"):
        idx = window.rfind(sep)
        if idx > best:
            best = idx + len(sep.rstrip("\n")) if sep.endswith("\n") else idx + 1
    # Only accept a cut that keeps a reasonable amount of the message.
    if best > int(limit * 0.5):
        return window[:best].strip()
    return None


def _trip_cooldown(exc: Exception) -> None:
    global _cooldown_until
    err = str(exc).lower()
    is_quota = any(s in err for s in ("credit", "quota", "billing", "429", "rate_limit"))
    _cooldown_until = time.monotonic() + (_QUOTA_COOLDOWN_S if is_quota else _TRANSIENT_COOLDOWN_S)
    log.warning(
        "personalizer API call failed (%s) — cooldown %ds, using template: %s",
        "quota" if is_quota else "transient",
        _QUOTA_COOLDOWN_S if is_quota else _TRANSIENT_COOLDOWN_S,
        exc,
    )


async def _generate(*, user_prompt: str, max_tokens: int) -> str | None:
    """Single guarded LLM call (DeepSeek if configured, else Haiku). Returns
    text, or None on any failure."""
    settings = get_settings()
    if llm_client.active_provider() is None:
        return None
    if time.monotonic() < _cooldown_until:
        return None
    try:
        result = await llm_client.complete(
            system=_SYSTEM,
            user=user_prompt,
            max_tokens=max_tokens,
            timeout=settings.personalize_timeout_seconds,
            anthropic_model=settings.personalize_model,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open is the whole point
        _trip_cooldown(exc)
        return None
    return result.text or None


def _clean(body: str) -> str:
    body = body.strip()
    # Models sometimes wrap the whole message in quotes despite instructions.
    if len(body) >= 2 and body[0] in "\"'" and body[-1] == body[0]:
        body = body[1:-1].strip()
    return body


async def personalize_linkedin(
    *,
    kind: Literal["connect", "dm"],
    rendered_body: str,
    snapshot: dict[str, Any] | None,
    offering: Any | None = None,
) -> PersonalizedCopy:
    """Personalize a LinkedIn connect note or DM. Never raises; fail-open."""
    template = (rendered_body or "").strip()
    fallback = PersonalizedCopy(subject=None, body=template, used_ai=False)
    if not template:
        return fallback

    limit = _LIMITS.get(kind, 1200)
    label = "connection request note" if kind == "connect" else "direct message"
    offering_block = _offering_block(offering)
    user_prompt = (
        f"Message type: LinkedIn {label}\n"
        f"Maximum length: {limit} characters — stay comfortably under it.\n\n"
        f"TEMPLATE (defines intent + call-to-action — adapt, do not change the offer):\n"
        f"{template}\n\n"
        f"RECIPIENT DETAILS (personalize only from these):\n{_lead_details(snapshot)}\n"
        + (f"\n{offering_block}\n" if offering_block else "")
        + "\nWrite the final personalized message now. Output ONLY the message text."
    )

    text = await _generate(user_prompt=user_prompt, max_tokens=400)
    if not text:
        return fallback

    body = _clean(text)
    # Reject anything that smells wrong: empty, unresolved tokens.
    if not body or "{{" in body or "}}" in body:
        return fallback

    if len(body) > limit:
        trimmed = _truncate_at_sentence(body, limit)
        if not trimmed or "{{" in trimmed:
            return fallback
        body = trimmed

    return PersonalizedCopy(subject=None, body=body, used_ai=True)


async def personalize_email(
    *,
    rendered_subject: str,
    rendered_body: str,
    snapshot: dict[str, Any] | None,
    offering: Any | None = None,
) -> PersonalizedCopy:
    """Personalize a cold email subject + body. Never raises; fail-open."""
    subj_template = (rendered_subject or "").strip()
    body_template = (rendered_body or "").strip()
    fallback = PersonalizedCopy(subject=subj_template, body=body_template, used_ai=False)
    if not body_template:
        return fallback

    offering_block = _offering_block(offering)
    user_prompt = (
        "Message type: cold outreach email\n"
        "Return the subject on the first line prefixed exactly with 'SUBJECT: ', "
        "then a blank line, then the body.\n"
        "Subject <= 200 characters.\n\n"
        f"TEMPLATE SUBJECT: {subj_template}\n"
        f"TEMPLATE BODY (defines intent + call-to-action — adapt, do not change the offer):\n"
        f"{body_template}\n\n"
        f"RECIPIENT DETAILS (personalize only from these):\n{_lead_details(snapshot)}\n"
        + (f"\n{offering_block}\n" if offering_block else "")
        + "\nWrite the final personalized email now."
    )

    text = await _generate(user_prompt=user_prompt, max_tokens=700)
    if not text:
        return fallback

    subject = subj_template
    body = text
    # Parse the optional 'SUBJECT: ...' first line.
    lines = text.splitlines()
    if lines and lines[0].strip().upper().startswith("SUBJECT:"):
        subject = lines[0].split(":", 1)[1].strip() or subj_template
        body = "\n".join(lines[1:]).lstrip("\n")

    body = _clean(body)
    if not body or "{{" in body or "}}" in body or "{{" in subject:
        return fallback
    if len(subject) > 200:
        subject = subj_template

    return PersonalizedCopy(subject=subject, body=body, used_ai=True)
