import hashlib
import hmac

from outreach.config import get_settings


def _hmac_hex(payload: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.token_secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def make_message_id(step_run_id: int) -> str:
    """Build a deterministic, HMAC-tokenized Message-ID for a step_run.

    Shape: <{step_run_id}.{hmac8}@{trace_host}>

    The hmac8 lets the IMAP poller verify a reply's In-Reply-To/References belongs
    to us before doing a DB lookup (rejects forgery + malformed bounces cheaply).
    """
    settings = get_settings()
    h8 = _hmac_hex(str(step_run_id))[:8]
    return f"<{step_run_id}.{h8}@{settings.email_trace_host}>"


def parse_message_id(raw: str) -> int | None:
    """Reverse of make_message_id. Returns step_run_id if HMAC verifies, else None."""
    s = raw.strip().strip("<>")
    if "@" not in s or "." not in s:
        return None
    local = s.rsplit("@", 1)[0]
    parts = local.split(".")
    if len(parts) != 2:
        return None
    try:
        run_id = int(parts[0])
    except ValueError:
        return None
    expected = _hmac_hex(str(run_id))[:8]
    if not hmac.compare_digest(parts[1], expected):
        return None
    return run_id
