"""Provider-agnostic single-shot LLM completion.

Prefers **DeepSeek** (OpenAI-compatible, cheap, and NOT subject to the Anthropic
account usage cap) when ``deepseek_api_key`` is set; otherwise falls back to
**Anthropic** (Claude Haiku). Every runtime AI helper — sentiment classification,
per-lead personalization, the LinkedIn selector healer, follow-up drafting, the
AI sequence generators — routes through here so the provider is chosen in ONE
place. When the Anthropic key is capped, setting DEEPSEEK_API_KEY moves all of
them onto DeepSeek at once.

`complete()` raises on no-provider / API error; callers keep their own fail-open
or HTTP-mapping behavior (sentiment → neutral, personalizer → template, etc.).
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from outreach.config import get_settings

# Matches a ```json … ``` (or bare ``` … ```) fenced block; group(1) is the body.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def extract_json_object(text: str, *, prefer_keys: tuple[str, ...] = ()) -> dict | None:
    """Robustly pull the first valid JSON object out of an LLM response.

    LLMs — DeepSeek especially — wrap JSON in markdown fences, prepend a preamble
    that mentions ``{{tokens}}``, or append a trailing note, all of which contain
    stray braces. A naive "first ``{`` to last ``}``" regex then captures an
    invalid span and ``json.loads`` fails (the root cause of the intermittent
    "unparseable response" in sequence generation).

    This instead scans each ``{`` with ``json.JSONDecoder.raw_decode`` (which
    parses one value and ignores trailing text) and collects every position that
    decodes to a dict. Fenced blocks are tried first. When ``prefer_keys`` is
    given, the first object containing ALL those keys wins (so a wrapper object
    never shadows the real ``{"steps": …}`` payload); otherwise the first valid
    object is returned.
    """
    if not text:
        return None

    blobs = [m.group(1) for m in _FENCE_RE.finditer(text)]
    blobs.append(text)  # fall back to the raw text (handles unfenced output)

    decoder = json.JSONDecoder()
    found: list[dict] = []
    for blob in blobs:
        i = 0
        while True:
            start = blob.find("{", i)
            if start == -1:
                break
            try:
                obj, _end = decoder.raw_decode(blob[start:])
                if isinstance(obj, dict):
                    found.append(obj)
            except json.JSONDecodeError:
                pass
            i = start + 1

    if not found:
        return None
    if prefer_keys:
        for obj in found:
            if all(k in obj for k in prefer_keys):
                return obj
    return found[0]

# Default cheap (Haiku-tier) model when the active provider is Anthropic.
# Callers needing a specific Claude model pass `anthropic_model`.
ANTHROPIC_DEFAULT_MODEL = "claude-haiku-4-5"


@dataclass(slots=True)
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    provider: str = ""
    model: str = ""


def active_provider() -> str | None:
    """'deepseek' if a DeepSeek key is set, else 'anthropic' if that key is set,
    else None (no LLM configured)."""
    s = get_settings()
    if (getattr(s, "deepseek_api_key", "") or "").strip():
        return "deepseek"
    if (getattr(s, "anthropic_api_key", "") or "").strip():
        return "anthropic"
    return None


async def complete(
    *,
    system: str,
    user: str,
    max_tokens: int,
    timeout: float | None = None,
    anthropic_model: str = ANTHROPIC_DEFAULT_MODEL,
) -> LLMResult:
    """One `system` + `user` → text call on the active provider.

    Args:
        timeout: optional per-call wall-clock timeout (seconds).
        anthropic_model: Claude model to use IF the active provider is Anthropic.
            Ignored under DeepSeek (which always uses ``deepseek_model``).
    """
    s = get_settings()
    provider = active_provider()
    if provider is None:
        raise RuntimeError(
            "no LLM API key configured (set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY)"
        )

    if provider == "deepseek":
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=s.deepseek_api_key.strip(), base_url=s.deepseek_base_url)
        coro = client.chat.completions.create(
            model=s.deepseek_model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        resp = await (asyncio.wait_for(coro, timeout) if timeout else coro)
        usage = getattr(resp, "usage", None)
        return LLMResult(
            text=(resp.choices[0].message.content or "").strip(),
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            provider="deepseek",
            model=s.deepseek_model,
        )

    # ── Anthropic ──
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=s.anthropic_api_key.strip())
    coro = client.messages.create(
        model=anthropic_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    resp = await (asyncio.wait_for(coro, timeout) if timeout else coro)
    usage = getattr(resp, "usage", None)
    text = "".join(
        getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
    ).strip()
    return LLMResult(
        text=text,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        provider="anthropic",
        model=anthropic_model,
    )
