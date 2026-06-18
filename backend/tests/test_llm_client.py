"""Provider dispatch for the shared llm_client.

DeepSeek is preferred when its key is set (it isn't subject to the Anthropic
usage cap); Anthropic is the fallback; no key → RuntimeError. Token usage is
normalized across the two providers' different field names.
"""
from types import SimpleNamespace

import pytest

from outreach.services import llm_client


def _settings(*, deepseek="", anthropic=""):
    return SimpleNamespace(
        deepseek_api_key=deepseek,
        anthropic_api_key=anthropic,
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-chat",
    )


def _patch_openai(monkeypatch, content="hello", *, prompt=11, completion=7):
    import openai

    class _Completions:
        async def create(self, **_kw):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
            )

    class _Client:
        def __init__(self, *_a, **_k):
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(openai, "AsyncOpenAI", _Client)


def _patch_anthropic(monkeypatch, text="claude-says-hi", *, inp=5, out=3):
    import anthropic

    class _Messages:
        async def create(self, **_kw):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=text)],
                usage=SimpleNamespace(input_tokens=inp, output_tokens=out),
            )

    class _Client:
        def __init__(self, *_a, **_k):
            self.messages = _Messages()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _Client)


# ── active_provider ──────────────────────────────────────────────────────────

def test_provider_prefers_deepseek(monkeypatch):
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings(deepseek="sk-d", anthropic="sk-a"))
    assert llm_client.active_provider() == "deepseek"


def test_provider_falls_back_to_anthropic(monkeypatch):
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings(deepseek="", anthropic="sk-a"))
    assert llm_client.active_provider() == "anthropic"


def test_provider_none_when_no_keys(monkeypatch):
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings())
    assert llm_client.active_provider() is None


# ── complete() dispatch ──────────────────────────────────────────────────────

async def test_complete_uses_deepseek_and_normalizes_usage(monkeypatch):
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings(deepseek="sk-d", anthropic="sk-a"))
    _patch_openai(monkeypatch, content="  ds-out  ", prompt=20, completion=9)
    r = await llm_client.complete(system="s", user="u", max_tokens=50)
    assert r.provider == "deepseek"
    assert r.text == "ds-out"  # stripped
    assert (r.input_tokens, r.output_tokens) == (20, 9)
    assert r.model == "deepseek-chat"


async def test_complete_uses_anthropic_when_no_deepseek(monkeypatch):
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings(anthropic="sk-a"))
    _patch_anthropic(monkeypatch, text="ant-out", inp=4, out=6)
    r = await llm_client.complete(system="s", user="u", max_tokens=50, anthropic_model="claude-haiku-4-5")
    assert r.provider == "anthropic"
    assert r.text == "ant-out"
    assert (r.input_tokens, r.output_tokens) == (4, 6)
    assert r.model == "claude-haiku-4-5"


async def test_complete_raises_without_provider(monkeypatch):
    monkeypatch.setattr(llm_client, "get_settings", lambda: _settings())
    with pytest.raises(RuntimeError):
        await llm_client.complete(system="s", user="u", max_tokens=10)
