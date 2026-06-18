"""Edge cases for sequence_generator LLM error handling + provider dispatch.

RCA context: a real run failed with a bare "LLM call failed: BadRequestError"
because the handler discarded the API's message. The underlying 400 was an
account *usage limit* ("regain access on 2026-07-01"). The fix surfaces the real
message and classifies usage/rate limits as 429 — for BOTH providers (Anthropic
and the OpenAI-compatible DeepSeek).
"""
from types import SimpleNamespace

import httpx
import openai
import pytest
from anthropic import AuthenticationError, BadRequestError, RateLimitError
from fastapi import HTTPException

from outreach.services import sequence_generator as sg


def _resp(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", "https://api.example.com/v1"))


def _bad_request(message: str) -> BadRequestError:
    return BadRequestError(
        message,
        response=_resp(400),
        body={"error": {"type": "invalid_request_error", "message": message}},
    )


def _settings(*, anthropic: str = "", deepseek: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        anthropic_api_key=anthropic,
        deepseek_api_key=deepseek,
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-chat",
    )


# ── _anthropic_error_message (generic {"error":{"message"}} extractor) ───────

def test_message_extracted_from_body():
    e = _bad_request("You have reached your specified API usage limits.")
    assert sg._anthropic_error_message(e) == "You have reached your specified API usage limits."


def test_message_falls_back_to_str_when_no_body():
    assert sg._anthropic_error_message(ValueError("boom")) == "boom"


def test_message_falls_back_to_attr_when_body_has_no_message():
    class E(Exception):
        body = {"error": {"type": "overloaded_error"}}
        message = "attr-level message"
    assert sg._anthropic_error_message(E()) == "attr-level message"


# ── _is_usage_or_rate_limit ─────────────────────────────────────────────────

@pytest.mark.parametrize("msg,expected", [
    ("You have reached your specified API usage limits. Regain access on 2026-07-01", True),
    ("Rate limit exceeded, slow down", True),
    ("Your credit balance is too low", True),
    ("quota exceeded", True),
    ("messages.0.content.0.text: Field required", False),
    ("model: claude-bogus is not supported", False),
    ("", False),
])
def test_is_usage_or_rate_limit_by_message(msg, expected):
    assert sg._is_usage_or_rate_limit(Exception(), msg) is expected


def test_ratelimit_type_is_always_a_limit():
    e = RateLimitError("anything", response=_resp(429), body=None)
    assert sg._is_usage_or_rate_limit(e, "no keywords at all") is True


# ── Anthropic path: _anthropic_complete error mapping (client mocked) ────────

def _patch_anthropic(monkeypatch, exc: Exception):
    class _Msgs:
        async def create(self, **_kw):
            raise exc

    class _Client:
        def __init__(self, *_a, **_k):
            self.messages = _Msgs()

    monkeypatch.setattr(sg, "AsyncAnthropic", _Client)


async def test_anthropic_usage_limit_maps_to_429(monkeypatch):
    _patch_anthropic(monkeypatch, _bad_request(
        "You have reached your specified API usage limits. You will regain access on 2026-07-01 at 00:00 UTC."
    ))
    with pytest.raises(HTTPException) as ei:
        await sg._anthropic_complete(_settings(anthropic="sk-x"), "msg")
    assert ei.value.status_code == 429
    assert "regain access on 2026-07-01" in ei.value.detail.lower()


async def test_anthropic_generic_400_maps_to_400(monkeypatch):
    _patch_anthropic(monkeypatch, _bad_request("messages.0.content.0.text: Field required"))
    with pytest.raises(HTTPException) as ei:
        await sg._anthropic_complete(_settings(anthropic="sk-x"), "msg")
    assert ei.value.status_code == 400
    assert "field required" in ei.value.detail.lower()


async def test_anthropic_auth_maps_to_401(monkeypatch):
    _patch_anthropic(monkeypatch, AuthenticationError("bad key", response=_resp(401), body=None))
    with pytest.raises(HTTPException) as ei:
        await sg._anthropic_complete(_settings(anthropic="sk-x"), "msg")
    assert ei.value.status_code == 401


async def test_anthropic_unexpected_maps_to_502(monkeypatch):
    _patch_anthropic(monkeypatch, RuntimeError("connection reset by peer"))
    with pytest.raises(HTTPException) as ei:
        await sg._anthropic_complete(_settings(anthropic="sk-x"), "msg")
    assert ei.value.status_code == 502
    assert "connection reset" in ei.value.detail.lower()


# ── DeepSeek path: _deepseek_complete error mapping (OpenAI client mocked) ───

def _openai_err(cls, message: str, status: int):
    return cls(message, response=_resp(status), body={"error": {"message": message}})


def _patch_openai(monkeypatch, *, exc: Exception | None = None, content: str | None = None):
    class _Completions:
        async def create(self, **_kw):
            if exc:
                raise exc
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    class _Client:
        def __init__(self, *_a, **_k):
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)


async def test_deepseek_success_returns_text(monkeypatch):
    _patch_openai(monkeypatch, content='{"name":"x","steps":[]}')
    out = await sg._deepseek_complete(_settings(deepseek="sk-x"), "msg")
    assert out == '{"name":"x","steps":[]}'


async def test_deepseek_rate_limit_maps_to_429(monkeypatch):
    _patch_openai(monkeypatch, exc=_openai_err(openai.RateLimitError, "Rate limit reached", 429))
    with pytest.raises(HTTPException) as ei:
        await sg._deepseek_complete(_settings(deepseek="sk-x"), "msg")
    assert ei.value.status_code == 429


async def test_deepseek_insufficient_balance_maps_to_429(monkeypatch):
    # DeepSeek returns 402/400 with a credit-balance message → treat as usage limit.
    _patch_openai(monkeypatch, exc=_openai_err(openai.BadRequestError, "Insufficient credit balance", 400))
    with pytest.raises(HTTPException) as ei:
        await sg._deepseek_complete(_settings(deepseek="sk-x"), "msg")
    assert ei.value.status_code == 429


async def test_deepseek_generic_400_maps_to_400(monkeypatch):
    _patch_openai(monkeypatch, exc=_openai_err(openai.BadRequestError, "Invalid messages format", 400))
    with pytest.raises(HTTPException) as ei:
        await sg._deepseek_complete(_settings(deepseek="sk-x"), "msg")
    assert ei.value.status_code == 400
    assert "invalid messages" in ei.value.detail.lower()


async def test_deepseek_auth_maps_to_401(monkeypatch):
    _patch_openai(monkeypatch, exc=_openai_err(openai.AuthenticationError, "Invalid API key", 401))
    with pytest.raises(HTTPException) as ei:
        await sg._deepseek_complete(_settings(deepseek="sk-x"), "msg")
    assert ei.value.status_code == 401


# ── _call_llm provider dispatch ─────────────────────────────────────────────

async def test_dispatch_prefers_deepseek_when_key_set(monkeypatch):
    monkeypatch.setattr(sg, "get_settings", lambda: _settings(anthropic="sk-a", deepseek="sk-d"))
    called = {}
    async def _fake_ds(_s, _m): called["deepseek"] = True; return '{"name":"n","steps":[]}'
    async def _fake_an(_s, _m): called["anthropic"] = True; return '{"name":"n","steps":[]}'
    monkeypatch.setattr(sg, "_deepseek_complete", _fake_ds)
    monkeypatch.setattr(sg, "_anthropic_complete", _fake_an)
    await sg._call_llm("test prompt", {"email"}, [])
    assert called == {"deepseek": True}


async def test_dispatch_uses_anthropic_when_no_deepseek_key(monkeypatch):
    monkeypatch.setattr(sg, "get_settings", lambda: _settings(anthropic="sk-a", deepseek=""))
    called = {}
    async def _fake_ds(_s, _m): called["deepseek"] = True; return "{}"
    async def _fake_an(_s, _m): called["anthropic"] = True; return '{"name":"n","steps":[]}'
    monkeypatch.setattr(sg, "_deepseek_complete", _fake_ds)
    monkeypatch.setattr(sg, "_anthropic_complete", _fake_an)
    await sg._call_llm("test prompt", {"email"}, [])
    assert called == {"anthropic": True}


async def test_dispatch_no_keys_maps_to_503(monkeypatch):
    monkeypatch.setattr(sg, "get_settings", lambda: _settings(anthropic="", deepseek=""))
    with pytest.raises(HTTPException) as ei:
        await sg._call_llm("test prompt", {"email"}, [])
    assert ei.value.status_code == 503
