"""Edge cases for llm_client.extract_json_object — the robust JSON extractor
that replaced the brittle greedy `\\{.*\\}` regex.

RCA context: sequence /generate intermittently raised "LLM returned unparseable
response". Root cause: the old parser captured from the FIRST '{' to the LAST
'}', so any stray brace OUTSIDE the JSON (a preamble mentioning {{first_name}},
a trailing note, a reasoning example, markdown fences, or a wrapper object)
corrupted the span. DeepSeek emits those intermittently, hence the flakiness.
"""
from outreach.services.llm_client import extract_json_object

VALID = '{"name":"X","steps":[{"channel":"email","body":"Hi {{first_name}}"}]}'


# ── the real-world failure modes that broke the old greedy regex ─────────────

def test_clean_json():
    assert extract_json_object(VALID, prefer_keys=("steps",))["name"] == "X"


def test_markdown_json_fence():
    text = "```json\n" + VALID + "\n```"
    assert extract_json_object(text, prefer_keys=("steps",))["name"] == "X"


def test_bare_triple_backtick_fence():
    text = "```\n" + VALID + "\n```"
    assert extract_json_object(text, prefer_keys=("steps",))["name"] == "X"


def test_preamble_containing_token_brace():
    # The brace inside {{first_name}} used to become the span start → invalid.
    text = "Sure! I'll use tokens like {{first_name}}. Here is the sequence:\n" + VALID
    obj = extract_json_object(text, prefer_keys=("steps",))
    assert obj is not None and obj["name"] == "X"


def test_trailing_note_containing_token_brace():
    text = VALID + "\n\nNote: remember to set {{sender_name}} before sending."
    obj = extract_json_object(text, prefer_keys=("steps",))
    assert obj is not None and obj["name"] == "X"


def test_reasoning_with_example_brace():
    text = "The expected shape is {key: value}. Final answer:\n" + VALID
    obj = extract_json_object(text, prefer_keys=("steps",))
    assert obj is not None and "steps" in obj


def test_wrapper_object_does_not_shadow_real_payload():
    # A leading valid-but-wrong object must NOT win when prefer_keys is set.
    text = '{"draft": true}\n' + VALID
    obj = extract_json_object(text, prefer_keys=("steps",))
    assert obj["name"] == "X" and "steps" in obj


def test_preamble_and_fence_combined():
    text = "Here you go:\n```json\n" + VALID + "\n```\nLet me know if you want changes."
    assert extract_json_object(text, prefer_keys=("steps",))["name"] == "X"


# ── degenerate inputs → None (caller raises its proper error / retries) ──────

def test_truncated_json_returns_none():
    # max_tokens cut the response mid-string: unterminated → not decodable.
    text = '{"name":"x","steps":[{"channel":"email","body":"Hi {{first'
    assert extract_json_object(text, prefer_keys=("steps",)) is None


def test_pure_prose_returns_none():
    assert extract_json_object("I cannot help with that request.") is None


def test_empty_returns_none():
    assert extract_json_object("") is None
    assert extract_json_object(None) is None  # type: ignore[arg-type]


def test_json_array_only_returns_none():
    # A top-level array isn't a dict; we only accept objects.
    assert extract_json_object('["a","b"]') is None


# ── prefer_keys fallback ─────────────────────────────────────────────────────

def test_no_prefer_keys_returns_first_object():
    text = '{"label":"positive","confidence":0.9}'
    assert extract_json_object(text)["label"] == "positive"


def test_prefer_keys_falls_back_to_first_when_none_match():
    # Single-step regeneration: no object has "steps" → return the first valid.
    step = '{"channel":"email","subject":"Hi","body":"Hello {{first_name}}"}'
    obj = extract_json_object(step, prefer_keys=("steps",))
    assert obj is not None and obj["channel"] == "email"
