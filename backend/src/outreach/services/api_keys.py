"""Public-API key helpers. Only the SHA-256 hash is stored; the raw key is
shown once at creation."""
from __future__ import annotations

import hashlib
import secrets

_PREFIX = "cok_"  # Coherent Outreach Key


def generate_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_hash, display_prefix). Persist the hash + prefix."""
    raw = _PREFIX + secrets.token_urlsafe(32)
    return raw, hash_key(raw), raw[:12]


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.strip().encode()).hexdigest()
