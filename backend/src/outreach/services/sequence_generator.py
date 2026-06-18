"""Generate a draft outreach sequence from a natural-language prompt via Claude."""
from __future__ import annotations

import logging
from typing import Any

import httpx
from anthropic import (
    AsyncAnthropic,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.config import get_settings
from outreach.models.channel import Channel
from outreach.models.sequence_rag_source import SequenceRagSource
from outreach.models.step import SequenceStep
from outreach.schemas.sequences import (
    EmailStepCreate,
    LinkedInConnectStepCreate,
    LinkedInDmStepCreate,
    SequenceCreate,
    SequenceDetail,
    StepCreate,
    WhatsAppStepCreate,
)
from outreach.services import document_ingest, llm_client, rag_retriever, sequences_service
from outreach.services.qdrant_store import RetrievedChunk
from outreach.services.template_render import SUPPORTED_TOKENS, _TOKEN_RE

log = logging.getLogger("outreach.sequence_generator")

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
ALL_CHANNELS = ("email", "linkedin_dm", "linkedin_connect", "whatsapp")
MAX_STEPS = 8

SYSTEM_PROMPT = (
    "You are an expert sales-development sequence designer. You create multi-step "
    "cold-outreach cadences that are concise, human, and professional. Never use "
    "buzzwords or salesy phrases. Use template tokens for personalization.\n\n"
    "GROUNDING IS YOUR HIGHEST PRIORITY. You receive two inputs with DIFFERENT roles:\n"
    "1. USER REQUEST — defines ONLY the strategy: who to target, the tone, how many "
    "steps, which channels, the cadence/spacing, and the overall goal. It is NOT a "
    "source of product facts or of the words you use to describe the product.\n"
    "2. DOCUMENT EXCERPTS — the SINGLE source of substance: every product fact, value "
    "proposition, benefit, feature, metric, proof point, use case, and the vocabulary "
    "you use to describe the product MUST come from these excerpts.\n\n"
    "When excerpts are provided, write each email/LinkedIn body by drawing its specific "
    "claims and phrasing directly from the excerpts. Reuse the document's own "
    "terminology, product names, numbers, and concrete details verbatim instead of "
    "paraphrasing them into generic marketing language — staying close to the source "
    "wording is what keeps the message accurate and grounded. Do NOT introduce any "
    "product capability, statistic, price, or claim that is absent from the excerpts, "
    "even if the user request implies or asks for it. If the request wants substance "
    "the excerpts do not support, keep the requested audience/tone/cadence but limit "
    "the actual content to what the excerpts state."
)

USER_TEMPLATE = """
USER REQUEST (use ONLY for strategy — audience, tone, number of steps, channels, cadence, goal. NOT a source of product facts or wording):
{prompt}

DOCUMENT EXCERPTS (the ONLY source of product facts, value props, and the exact words you use to describe the product):
{rag_snippets}

HOW TO WRITE EACH MESSAGE (grounding rules — follow strictly):
- Build every email / linkedin_dm / linkedin_connect body from the excerpts above: pull the specific value props, benefits, features, metrics, proof points, and CTA directly from them.
- Reuse the document's exact terminology, product names, and concrete numbers — do not soften them into generic language. Echoing the source wording is what keeps each message grounded.
- Every factual claim or benefit in a message must trace back to a specific excerpt. If it is not in the excerpts, do not write it.
- The USER REQUEST shapes WHO you write to and HOW the cadence runs — it never adds product facts. Ignore any product claim implied by the request that the excerpts do not support.
- sequence description: one line summarizing the goal, using facts drawn from the excerpts.
- ONLY if the excerpts are empty: fall back to the user request for content.

AVAILABLE CHANNELS (only use these):
{channels}

TEMPLATE TOKENS (only these — use in subject/body):
{tokens}

CONSTRAINTS:
- 1 to {max_steps} steps total
- Step 1 must have delay_days=0
- email: subject required (max 250 chars), body max 16000 chars
- linkedin_connect: no subject, body max 300 chars (connection note)
- linkedin_dm: no subject, body max 8000 chars
- whatsapp: no subject, body max 4000 chars (short, conversational, no links/markdown)
- delay_days: 0-365, delay_hours: 0-23
- Use realistic spacing between steps based on the user's request
- CHANNEL MIX: use the channels the user asks for. If the request mentions LinkedIn (a connection request / connect note or a LinkedIn DM), you MUST include those step types — do not silently turn everything into email. A "mixed" sequence should combine email and LinkedIn steps.
- SIGN-OFF: when a message has a signature, sign it with {{sender_name}} (the sender). NEVER sign off with the recipient's {{first_name}} and never write a literal "[Your name]" placeholder.
- Base ALL product facts, value props, and descriptive wording ONLY on the document excerpts — never invent claims or import unsupported substance from the request

Return JSON only:
{{
  "name": "short sequence name (max 120 chars)",
  "description": "one-line summary of the sequence goal",
  "steps": [
    {{
      "channel": "email | linkedin_dm | linkedin_connect | whatsapp",
      "delay_days": 0,
      "delay_hours": 0,
      "subject": "string or null (email only)",
      "body": "message text with {{{{first_name}}}} etc."
    }}
  ]
}}
"""


def _parse_json(text: str) -> dict | None:
    # Robust extraction: tolerate markdown fences, preamble/trailing prose, and
    # stray {{token}} braces the model adds around the JSON. prefer_keys ensures
    # we pick the real sequence object (the one with "steps"), not a wrapper.
    return llm_client.extract_json_object(text, prefer_keys=("steps",))


def _scan_tokens(text: str) -> set[str]:
    return {m.group(1) for m in _TOKEN_RE.finditer(text or "")}


async def _connected_channels(session: AsyncSession, user_id: int) -> set[str]:
    """Step-channel kinds the user has an ACTIVE channel for (may be empty)."""
    rows = (await session.execute(
        select(Channel.channel_type).where(
            Channel.user_id == user_id,
            Channel.status == "active",
        )
    )).scalars().all()
    out: set[str] = set()
    for ct in rows:
        if ct == "email":
            out.add("email")
        elif ct == "linkedin":
            out.add("linkedin_dm")
            out.add("linkedin_connect")
        elif ct == "whatsapp":
            out.add("whatsapp")
    return out


def _validate_plan(
    plan: dict,
    allowed_channels: set[str],
) -> tuple[list[str], list[str]]:
    """Return (violations, warnings)."""
    violations: list[str] = []
    warnings: list[str] = []
    steps = plan.get("steps")
    if not isinstance(steps, list) or not (1 <= len(steps) <= MAX_STEPS):
        violations.append(f"steps must be a list of 1-{MAX_STEPS} items")
        return violations, warnings

    allowed_token_set = set(SUPPORTED_TOKENS)
    for i, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            violations.append(f"step {i}: must be an object")
            continue
        channel = raw.get("channel")
        if channel not in ALL_CHANNELS:
            violations.append(f"step {i}: invalid channel {channel!r}")
            continue
        if channel not in allowed_channels:
            violations.append(f"step {i}: channel {channel!r} not available for this user")
            continue

        delay_days = raw.get("delay_days", 0)
        delay_hours = raw.get("delay_hours", 0)
        if not isinstance(delay_days, int) or not (0 <= delay_days <= 365):
            violations.append(f"step {i}: delay_days must be int 0-365")
        if not isinstance(delay_hours, int) or not (0 <= delay_hours <= 23):
            violations.append(f"step {i}: delay_hours must be int 0-23")
        if i == 1 and delay_days != 0:
            violations.append("step 1: delay_days must be 0")

        subject = raw.get("subject")
        body = raw.get("body", "")
        if not isinstance(body, str) or not body.strip():
            violations.append(f"step {i}: body is required")
            continue

        if channel == "email":
            if not subject or not str(subject).strip():
                violations.append(f"step {i}: email steps require a subject")
            elif len(str(subject)) > 250:
                violations.append(f"step {i}: email subject exceeds 250 chars")
            if len(body) > 16000:
                violations.append(f"step {i}: email body exceeds 16000 chars")
        elif channel == "linkedin_connect":
            if subject is not None and str(subject).strip():
                warnings.append(f"step {i}: linkedin_connect subject ignored")
            if len(body) > 300:
                violations.append(f"step {i}: linkedin_connect body exceeds 300 chars")
        elif channel == "linkedin_dm":
            if subject is not None and str(subject).strip():
                warnings.append(f"step {i}: linkedin_dm subject ignored")
            if len(body) > 8000:
                violations.append(f"step {i}: linkedin_dm body exceeds 8000 chars")
        elif channel == "whatsapp":
            if subject is not None and str(subject).strip():
                warnings.append(f"step {i}: whatsapp subject ignored")
            if len(body) > 4000:
                violations.append(f"step {i}: whatsapp body exceeds 4000 chars")

        for text in filter(None, [subject, body]):
            unknown = _scan_tokens(str(text)) - allowed_token_set
            if unknown:
                violations.append(f"step {i}: unknown tokens {sorted(unknown)}")

    name = plan.get("name", "")
    if not isinstance(name, str) or not name.strip():
        violations.append("name is required")
    elif len(name) > 120:
        violations.append("name exceeds 120 chars")

    return violations, warnings


def _to_step_create(raw: dict, step_order: int) -> StepCreate:
    channel = raw["channel"]
    base = {
        "step_order": step_order,
        "delay_days": int(raw.get("delay_days", 0)),
        "delay_hours": int(raw.get("delay_hours", 0)),
        "config": {},
    }
    body = str(raw["body"]).strip()
    if channel == "email":
        return EmailStepCreate(
            channel="email",
            subject=str(raw["subject"]).strip(),
            body=body,
            **base,
        )
    if channel == "linkedin_dm":
        return LinkedInDmStepCreate(channel="linkedin_dm", body=body, **base)
    if channel == "whatsapp":
        return WhatsAppStepCreate(channel="whatsapp", body=body, **base)
    return LinkedInConnectStepCreate(channel="linkedin_connect", body=body, **base)


def _format_rag_snippets(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "  (no uploaded documents — use the user request only)"
    settings = get_settings()
    cap = settings.rag_chunk_prompt_chars
    lines: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        fname = chunk.filename or "document"
        lines.append(f"[{i}] (file: {fname}, relevance: {chunk.score:.2f})")
        lines.append(f"  {chunk.text[:cap]}")
    return "\n".join(lines)


def _rag_grounding_warnings(chunks: list[RetrievedChunk], had_doc_ids: bool) -> list[str]:
    if chunks:
        names = sorted({c.filename for c in chunks if c.filename})
        src = ", ".join(names) if names else "uploaded documents"
        return [f"Grounded generation used {len(chunks)} document chunks from: {src}"]
    if had_doc_ids:
        return ["No matching document context — sequence may not reflect uploaded docs"]
    return []


def _anthropic_error_message(e: Exception) -> str:
    """Pull Anthropic's human-readable message out of an SDK exception.

    The API returns ``{"error": {"message": "..."}}`` in the response body; the
    old handler discarded it and showed only the exception *type* (e.g. the
    useless "BadRequestError"), hiding the real reason — e.g. an account usage
    cap with a regain date. Falls back to ``.message`` then ``str(e)``.
    """
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    msg = getattr(e, "message", None)
    return str(msg) if msg else str(e)


def _is_usage_or_rate_limit(e: Exception, message: str) -> bool:
    if isinstance(e, RateLimitError):
        return True
    low = message.lower()
    return any(s in low for s in ("usage limit", "regain access", "rate limit", "quota", "credit"))


async def _anthropic_complete(settings, user_msg: str) -> str:
    """Call Claude (messages API). Returns the text; raises HTTPException with the
    real reason on failure (auth/usage-limit/bad-request/unexpected)."""
    client = AsyncAnthropic(api_key=settings.anthropic_api_key.strip())
    try:
        response = await client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except AuthenticationError as e:
        log.warning("sequence generate auth failed: %s", e)
        raise HTTPException(
            401,
            "Anthropic API key rejected (invalid or expired). "
            "Update ANTHROPIC_API_KEY in backend/.env with a valid key from console.anthropic.com, then restart the backend.",
        ) from e
    except (RateLimitError, BadRequestError) as e:
        msg = _anthropic_error_message(e)
        if _is_usage_or_rate_limit(e, msg):
            log.warning("sequence generate blocked by usage/rate limit: %s", msg)
            raise HTTPException(429, f"AI temporarily unavailable — {msg}") from e
        log.warning("sequence generate rejected (400): %s", msg)
        raise HTTPException(400, f"AI request rejected: {msg}") from e
    except (httpx.HTTPError, Exception) as e:  # noqa: BLE001
        log.warning("sequence generate LLM call failed: %s", e)
        raise HTTPException(502, f"LLM call failed: {_anthropic_error_message(e)}") from e
    return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")


async def _deepseek_complete(settings, user_msg: str) -> str:
    """Call DeepSeek (OpenAI-compatible chat API). Returns the text; raises
    HTTPException with the real reason on failure. Same error semantics as the
    Anthropic path so the UI behaves identically regardless of provider."""
    from openai import (
        AsyncOpenAI,
        AuthenticationError as OpenAIAuthError,
        BadRequestError as OpenAIBadRequest,
        RateLimitError as OpenAIRateLimit,
    )

    client = AsyncOpenAI(
        api_key=settings.deepseek_api_key.strip(),
        base_url=settings.deepseek_base_url,
    )
    try:
        resp = await client.chat.completions.create(
            model=settings.deepseek_model,
            max_tokens=4000,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
    except OpenAIAuthError as e:
        log.warning("deepseek auth failed: %s", e)
        raise HTTPException(
            401,
            "DeepSeek API key rejected — update DEEPSEEK_API_KEY in backend/.env, then restart the backend.",
        ) from e
    except (OpenAIRateLimit, OpenAIBadRequest) as e:
        msg = _anthropic_error_message(e)  # generic {"error":{"message"}} extractor
        if _is_usage_or_rate_limit(e, msg):
            log.warning("deepseek blocked by usage/rate limit: %s", msg)
            raise HTTPException(429, f"AI temporarily unavailable — {msg}") from e
        log.warning("deepseek rejected (400): %s", msg)
        raise HTTPException(400, f"AI request rejected: {msg}") from e
    except Exception as e:  # noqa: BLE001
        log.warning("deepseek call failed: %s", e)
        raise HTTPException(502, f"LLM call failed: {_anthropic_error_message(e)}") from e
    return resp.choices[0].message.content or ""


async def _call_llm(
    prompt: str,
    allowed_channels: set[str],
    rag_chunks: list[RetrievedChunk],
    violations_hint: list[str] | None = None,
) -> dict:
    settings = get_settings()
    # Prefer DeepSeek when its key is set (cheap, no Anthropic usage cap); else Claude.
    use_deepseek = bool(settings.deepseek_api_key.strip())
    if not use_deepseek and not settings.anthropic_api_key.strip():
        raise HTTPException(
            503,
            "No LLM key configured — set DEEPSEEK_API_KEY (or ANTHROPIC_API_KEY) in backend/.env",
        )

    channels_str = ", ".join(sorted(allowed_channels))
    tokens_str = ", ".join(f"{{{{{t}}}}}" for t in SUPPORTED_TOKENS)
    rag_block = _format_rag_snippets(rag_chunks)
    user_msg = USER_TEMPLATE.format(
        prompt=prompt,
        rag_snippets=rag_block,
        channels=channels_str,
        tokens=tokens_str,
        max_steps=MAX_STEPS,
    )
    if not rag_chunks:
        # No documents to ground in. Override the strict grounding rules so the
        # model writes from the USER REQUEST instead of refusing for lack of
        # excerpts. Grounding only applies when there are excerpts to ground in.
        user_msg += (
            "\n\nNO DOCUMENTS PROVIDED: the excerpts section is empty. Disregard the "
            "document-grounding rules above and write a complete sequence using the "
            "USER REQUEST as the source of content. Do NOT refuse or return a blocking "
            "message — always produce a valid sequence."
        )
    if violations_hint:
        user_msg += "\n\nPREVIOUS ATTEMPT VIOLATIONS (fix all of these):\n" + "\n".join(
            f"- {v}" for v in violations_hint
        )

    text = (
        await _deepseek_complete(settings, user_msg)
        if use_deepseek
        else await _anthropic_complete(settings, user_msg)
    )
    plan = _parse_json(text)
    if not plan:
        raise HTTPException(502, "LLM returned unparseable response — try again")
    return plan


async def _persist_sequence(
    session: AsyncSession,
    user_id: int,
    plan: dict,
    prompt: str,
    timezone: str,
    source_document_ids: list[int] | None = None,
) -> SequenceDetail:
    llm_desc = str(plan.get("description") or "").strip()
    desc = f"AI-generated from prompt: {prompt[:500]}"
    if llm_desc:
        desc = f"{llm_desc}\n\n{desc}"

    seq_out = await sequences_service.create_sequence(
        session,
        user_id,
        SequenceCreate(
            name=str(plan["name"]).strip()[:120],
            description=desc[:4000],
            timezone=timezone,
        ),
    )

    steps_raw: list[dict[str, Any]] = plan["steps"]
    for order, raw in enumerate(steps_raw, start=1):
        dto = _to_step_create(raw, order)
        step = SequenceStep(
            sequence_id=seq_out.id,
            step_order=order,
            channel=dto.channel,
            delay_days=dto.delay_days,
            delay_hours=dto.delay_hours,
            subject=getattr(dto, "subject", None),
            body=dto.body,
            config=dto.config,
        )
        session.add(step)

    # Record which RAG documents grounded this generation, so the grounding
    # evaluator can later score the sequence against the exact docs it used.
    for doc_id in dict.fromkeys(source_document_ids or []):
        session.add(SequenceRagSource(sequence_id=seq_out.id, document_id=doc_id))

    await session.commit()

    return await sequences_service.get_sequence_detail(session, user_id, seq_out.id)


async def generate_sequence_from_prompt(
    session: AsyncSession,
    user_id: int,
    prompt: str,
    timezone: str,
    document_ids: list[int] | None = None,
) -> tuple[SequenceDetail, list[str]]:
    connected = await _connected_channels(session, user_id)
    # Generate whatever channels the user asks for (email + LinkedIn). The
    # connection requirement is enforced at activation time (sequences_service
    # .change_status), so a draft may contain LinkedIn steps before LinkedIn is
    # connected — we just warn about it below.
    allowed = set(ALL_CHANNELS)
    all_warnings: list[str] = []
    violations_hint: list[str] | None = None

    rag_chunks, rag_warnings, had_docs = await rag_retriever.retrieve_context(
        session, user_id, prompt, document_ids,
    )
    all_warnings.extend(rag_warnings)
    all_warnings.extend(_rag_grounding_warnings(rag_chunks, had_docs))

    # The indexed documents this generation is grounded on (the same resolution
    # the retriever used). Persisted alongside the sequence for later evaluation.
    source_doc_ids = await document_ingest.resolve_document_ids(
        session, user_id, document_ids or None,
    )

    for attempt in range(2):
        plan = await _call_llm(prompt, allowed, rag_chunks, violations_hint)
        violations, warnings = _validate_plan(plan, allowed)
        all_warnings.extend(warnings)
        if not violations:
            used = {
                str(s.get("channel"))
                for s in plan.get("steps", [])
                if isinstance(s, dict)
            }
            missing = used - connected
            if missing:
                pretty = ", ".join(sorted(missing))
                all_warnings.append(
                    f"Sequence includes {pretty} step(s) but no active channel is "
                    "connected for them — connect it in Channels before activating."
                )
            detail = await _persist_sequence(
                session, user_id, plan, prompt, timezone, source_doc_ids,
            )
            return detail, all_warnings
        violations_hint = violations
        log.info("sequence generate validation failed (attempt %d): %s", attempt + 1, violations)

    raise HTTPException(
        422,
        detail={"message": "Generated sequence failed validation", "violations": violations_hint},
    )
