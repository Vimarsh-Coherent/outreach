"""Outbound webhook helpers — signing, payload shaping, and retry backoff.

Pure + dependency-light so the security-critical bits are unit-tested. The
worker (workers/webhook_worker.py) handles the DB + HTTP.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

# Event types an endpoint may subscribe to (curated subset of the events table
# that's meaningful to an external CRM/automation).
SUBSCRIBABLE_EVENT_TYPES: list[str] = [
    "delivered", "open", "click", "reply", "bounce",
    "connection_accepted", "dm_delivered",
]

MAX_ATTEMPTS = 8
# Backoff after each failed attempt (seconds): 1m, 5m, 30m, 2h, then 6h.
_BACKOFF = [60, 300, 1800, 7200, 21600]


def generate_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def sign(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex of the raw body. Receivers verify with the same secret."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def signature_header(secret: str, body: bytes) -> str:
    return f"sha256={sign(secret, body)}"


def backoff_seconds(attempts_made: int) -> int:
    """Delay before the next retry, given how many attempts already failed."""
    return _BACKOFF[min(max(attempts_made - 1, 0), len(_BACKOFF) - 1)]


def validate_event_types(raw: object) -> list[str]:
    """Keep only known subscribable event types (dedup, preserve order)."""
    if not isinstance(raw, list):
        return []
    seen: list[str] = []
    for x in raw:
        if isinstance(x, str) and x in SUBSCRIBABLE_EVENT_TYPES and x not in seen:
            seen.append(x)
    return seen


def build_payload(row: dict) -> dict:
    """Shape an event row (events ⨝ enrolments ⨝ leads) into the webhook body."""
    occurred = row.get("occurred_at")
    return {
        "event": row.get("event_type"),
        "event_id": row.get("id"),
        "channel": row.get("channel"),
        "occurred_at": occurred.isoformat() if hasattr(occurred, "isoformat") else occurred,
        "enrolment_id": row.get("enrolment_id"),
        "lead": {
            "id": row.get("lead_id"),
            "email": row.get("email"),
            "first_name": row.get("first_name"),
            "last_name": row.get("last_name"),
            "company": row.get("company"),
        },
        "data": row.get("payload") or {},
    }
