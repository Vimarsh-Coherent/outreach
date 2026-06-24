"""Phase 4 — webhook signing/payload/backoff + API-key hashing (pure)."""
from datetime import datetime, timezone

from outreach.services import api_keys, webhooks


# ── signing ──────────────────────────────────────────────────────────────────

def test_sign_is_stable_and_verifiable():
    secret, body = "whsec_abc", b'{"event":"reply"}'
    sig = webhooks.sign(secret, body)
    assert sig == webhooks.sign(secret, body)          # deterministic
    assert len(sig) == 64                              # sha256 hex
    # a receiver recomputes with the same secret to verify
    import hashlib
    import hmac
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sig == expected


def test_signature_changes_with_secret_and_body():
    b = b"payload"
    assert webhooks.sign("s1", b) != webhooks.sign("s2", b)
    assert webhooks.sign("s1", b"a") != webhooks.sign("s1", b"b")


def test_signature_header_format():
    assert webhooks.signature_header("s", b"x").startswith("sha256=")


def test_generate_secret_unique_and_prefixed():
    a, b = webhooks.generate_secret(), webhooks.generate_secret()
    assert a != b and a.startswith("whsec_")


# ── event-type validation ────────────────────────────────────────────────────

def test_validate_event_types_filters_unknown_and_dedups():
    out = webhooks.validate_event_types(["reply", "reply", "bogus", "open", 5])
    assert out == ["reply", "open"]


def test_validate_event_types_non_list():
    assert webhooks.validate_event_types("reply") == []


# ── backoff ──────────────────────────────────────────────────────────────────

def test_backoff_increases_then_caps():
    seq = [webhooks.backoff_seconds(n) for n in range(1, 9)]
    assert seq[0] == 60
    assert all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))   # monotonic
    assert seq[-1] == 21600                                          # capped at 6h


# ── payload shaping ──────────────────────────────────────────────────────────

def test_build_payload_shape():
    row = {
        "id": 9, "event_type": "reply", "channel": "email",
        "occurred_at": datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc),
        "enrolment_id": 3, "lead_id": 7, "email": "a@b.com",
        "first_name": "Ann", "last_name": "Bee", "company": "Acme",
        "payload": {"snippet": "hi"},
    }
    p = webhooks.build_payload(row)
    assert p["event"] == "reply" and p["event_id"] == 9 and p["channel"] == "email"
    assert p["occurred_at"].startswith("2026-06-22T10:00")
    assert p["lead"] == {"id": 7, "email": "a@b.com", "first_name": "Ann", "last_name": "Bee", "company": "Acme"}
    assert p["data"] == {"snippet": "hi"}


# ── api keys ─────────────────────────────────────────────────────────────────

def test_generate_key_returns_raw_hash_prefix():
    raw, h, prefix = api_keys.generate_key()
    assert raw.startswith("cok_")
    assert h == api_keys.hash_key(raw)
    assert prefix == raw[:12] and len(h) == 64


def test_hash_is_stable_and_distinct():
    raw, h, _ = api_keys.generate_key()
    assert api_keys.hash_key(raw) == h
    assert api_keys.hash_key(raw + "x") != h


def test_hash_strips_whitespace():
    assert api_keys.hash_key(" cok_x ") == api_keys.hash_key("cok_x")
