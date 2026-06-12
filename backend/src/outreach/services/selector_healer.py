"""AI selector healer — ported from watchlink-main/server/src/tools/selector-healer.ts.

When the Chrome extension's content script fails to find an element using its
current CSS selectors, it sends the page DOM here. Claude Haiku reads it,
identifies the right element, and returns 3 ranked CSS selectors.

Resilience hierarchy (matches watchlink):
  1. aria-label / role / data-* attributes (most stable across LinkedIn refactors)
  2. Tag + position descendants (h1, main section:first-child)
  3. Class names ONLY when they look semantic (not random `_a550bd36` hashes)
"""
from __future__ import annotations

import json
import logging
import re
import time

from anthropic import AsyncAnthropic
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.config import get_settings
from outreach.schemas.selector_heal import HealRequest, HealResponse
from outreach.services import watchdog_state

log = logging.getLogger("outreach.selector_healer")

HEALER_SYSTEM = (
    "You are a LinkedIn DOM specialist. Given a DOM snapshot and an intent "
    "(what element to find), generate CSS selectors that will work even if "
    "LinkedIn changes class names.\n\n"
    "Important context about how selectors are used:\n"
    "- The extension runs every selector through a SHADOW-DOM-PIERCING query, so "
    "target the element directly. LinkedIn renders its invite/compose modals "
    "inside OPEN shadow roots; in the snapshot that markup appears under an "
    "`<!-- OPEN_DIALOG -->` comment. For modal intents, find the element THERE.\n"
    "- Interactive controls may be a <button>, an <a>, OR any element with "
    "role=\"button\". Do NOT assume <button>. In particular the profile 'Connect' "
    "control is usually an <a> whose href contains 'custom-invite' or "
    "'vanityName', with aria-label like 'Invite <Name> to connect'.\n\n"
    "Rules:\n"
    "1. Prefer aria-label, data-*, role, and stable href fragments (most stable).\n"
    "2. Use tag + position selectors as fallback (h1, main section:first-child).\n"
    "3. AVOID class names that look like random hashes (e.g. `_a550bd36`, "
    "`bf8fb201`). Only use class names that look semantic (`msg-form__send`).\n"
    "4. Do NOT scope to a tag you're unsure of (prefer `[aria-label=\"…\"]` over "
    "`button[aria-label=\"…\"]` when the element could be an <a>).\n"
    "5. Return 3 selectors ranked by stability (most stable first).\n"
    "6. Format as a JSON array of strings — nothing else."
)

# Hint for the model about what each intent targets in plain English.
INTENT_DESCRIPTIONS: dict[str, str] = {
    "connectButton":
        "The profile 'Connect' control that opens the connection-request flow. In "
        "LinkedIn's current UI this is usually an <a> whose href contains "
        "'custom-invite' / 'vanityName', with aria-label 'Invite <Name> to connect' "
        "— prefer matching that anchor or aria-label. IGNORE the many 'Connect' "
        "buttons in the 'People also viewed / People similar' rails. On some "
        "profiles Connect is hidden under a 'More' dropdown.",
    "moreButton":
        "The 'More' overflow/dropdown control on a LinkedIn profile (next to "
        "Message/Connect). May be a <button> or an element with role='button'.",
    "addNoteButton":
        "The 'Add a note' button inside the invitation modal (under OPEN_DIALOG; "
        "the modal lives in a shadow root). Usually <button aria-label='Add a note'>.",
    "noteTextarea":
        "The text input where the user types the custom note, inside the invitation "
        "modal (under OPEN_DIALOG). Usually <textarea name=\"message\"> or "
        "#custom-message.",
    "sendInvitationButton":
        "The button that submits the connection request from the invitation modal "
        "(under OPEN_DIALOG). Its label varies: 'Send without a note' (no-note "
        "flow), 'Send invitation', 'Send now', or 'Send'. Match the aria-label; do "
        "NOT return the 'Add a note' or 'Dismiss' buttons.",
    "messageButton":
        "The 'Message' button on a LinkedIn profile that opens the DM composer. "
        "May be a <button> or <a>.",
    "composeEditor":
        "The contenteditable rich-text area where you type a direct message, inside "
        "the 'New message' composer (often under OPEN_DIALOG / a shadow root). "
        "Usually <div role='textbox' contenteditable='true'> in a .msg-form.",
    "sendDmButton":
        "The 'Send' button that submits a typed direct message in the DM composer "
        "(under OPEN_DIALOG). Usually a <button> with text/aria 'Send'.",
    "connectionCard":
        "An <a> link to a person's profile (/in/<vanity>) inside the My Network "
        "connections LIST (page /mynetwork/invite-connect/connections/). Return a "
        "selector that matches the profile-name/avatar anchors of connection "
        "cards. Prefer something scoped to the connection list container so it "
        "does NOT also match 'People you may know' suggestion cards.",
}


def _haiku_cost_usd(input_tokens: int, output_tokens: int) -> float:
    # Claude Haiku 4.5 pricing (per 1M tokens): input $1, output $5.
    return (input_tokens / 1_000_000) * 1.0 + (output_tokens / 1_000_000) * 5.0


def _extract_json_array(text_block: str) -> list[str]:
    # Strip markdown code fences. Claude often wraps JSON in ```json ... ```.
    stripped = re.sub(r"```(?:json)?\s*", "", text_block)
    stripped = re.sub(r"```", "", stripped).strip()
    # Find the outermost JSON array. CSS selectors contain `[attr]` so the
    # inner brackets confuse a non-greedy match. Pull from first `[` to
    # the last `]`.
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    candidate = stripped[start : end + 1]
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, list):
            return [str(s) for s in parsed if isinstance(s, str)][:5]
    except json.JSONDecodeError:
        pass
    return []


# Fail-fast cooldown. While the Anthropic quota is exhausted (or the API is
# down) every heal attempt is a doomed network call that the content script
# blocks on for up to 20s — in the middle of a DM/connect flow. After the
# first API error we short-circuit instantly for a cooldown window: long for
# quota/billing errors (they won't fix themselves for weeks), short for
# transient ones. Self-resets, so healing resumes on its own when quota does.
_cooldown_until: float = 0.0
_QUOTA_COOLDOWN_S = 6 * 3600
_TRANSIENT_COOLDOWN_S = 15 * 60


async def heal_selector(req: HealRequest) -> HealResponse:
    global _cooldown_until
    settings = get_settings()
    if not settings.anthropic_api_key:
        return HealResponse(
            selectors=[], method="fallback_no_api_key",
            model="none", intent=req.intent,
        )
    if time.monotonic() < _cooldown_until:
        return HealResponse(
            selectors=[], method="cooldown_after_api_error",
            model="claude-haiku-4-5-20251001", intent=req.intent,
        )

    intent_hint = INTENT_DESCRIPTIONS.get(req.intent, req.intent)
    failed_list = ", ".join(req.failed_selectors) if req.failed_selectors else "none"
    user = (
        f"Page type: {req.page_type}\n"
        f"Element to find: {req.intent}\n"
        f"What it does: {intent_hint}\n"
        f"Previously working selectors that now fail: {failed_list}\n\n"
        f"DOM snapshot (cleaned of scripts/styles; an open modal, if any, appears "
        f"first under an OPEN_DIALOG comment and may come from a shadow root):\n"
        f"{req.dom_snapshot[:12000]}\n\n"
        f"Generate 3 CSS selectors for this element, ranked by stability. "
        f"Return ONLY a JSON array of strings."
    )

    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=HEALER_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:  # noqa: BLE001
        err = str(e).lower()
        is_quota = any(s in err for s in ("credit", "quota", "billing", "429", "rate_limit"))
        _cooldown_until = time.monotonic() + (
            _QUOTA_COOLDOWN_S if is_quota else _TRANSIENT_COOLDOWN_S
        )
        log.warning(
            "selector heal API call failed (%s) — healer on cooldown for %ds: %s",
            "quota" if is_quota else "transient",
            _QUOTA_COOLDOWN_S if is_quota else _TRANSIENT_COOLDOWN_S, e,
        )
        watchdog_state.log_event(
            "deep_verify", "issue",
            f"selector_healer API error for intent={req.intent}: {type(e).__name__} "
            f"— cooldown {'6h (quota)' if is_quota else '15m'}",
        )
        return HealResponse(
            selectors=[], method="api_error",
            model="claude-haiku-4-5-20251001", intent=req.intent,
        )

    text_out = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    selectors = _extract_json_array(text_out)
    cost = _haiku_cost_usd(resp.usage.input_tokens, resp.usage.output_tokens)

    if not selectors:
        log.warning("selector heal returned no parseable selectors: %s", text_out[:200])
        watchdog_state.log_event(
            "deep_verify", "issue",
            f"selector_healer returned no selectors for {req.intent}",
        )
    else:
        watchdog_state.log_event(
            "deep_verify", "healed",
            f"selector_healer produced {len(selectors)} candidates for {req.intent} "
            f"(${cost:.4f})",
            intent=req.intent, selectors=selectors, cost_usd=cost,
        )

    return HealResponse(
        selectors=selectors,
        method="ai_healer" if selectors else "ai_no_match",
        model="claude-haiku-4-5-20251001",
        intent=req.intent,
        cost_usd=round(cost, 6),
    )


async def log_heal_in_db(
    session: AsyncSession, user_id: int, intent: str, selectors: list[str], cost: float | None
) -> None:
    """Best-effort persistence to a heal_log table for audit/tracking. Created on demand."""
    try:
        await session.execute(text(
            "CREATE TABLE IF NOT EXISTS outreach.selector_heals ("
            " id BIGSERIAL PRIMARY KEY,"
            " user_id INT NOT NULL,"
            " intent VARCHAR(60) NOT NULL,"
            " selectors JSONB NOT NULL,"
            " cost_usd NUMERIC(10,6),"
            " created_at TIMESTAMPTZ DEFAULT NOW()"
            ")"
        ))
        await session.execute(text(
            "INSERT INTO outreach.selector_heals (user_id, intent, selectors, cost_usd) "
            "VALUES (:uid, :intent, CAST(:sel AS jsonb), :cost)"
        ), {"uid": user_id, "intent": intent, "sel": json.dumps(selectors), "cost": cost})
        await session.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("failed to log heal: %s", e)
