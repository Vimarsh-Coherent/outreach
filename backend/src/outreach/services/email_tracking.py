"""Email open/click tracking.

- An invisible 1x1 pixel whose URL encodes the step_run_id (HMAC-signed) →
  GET hit ⇒ an `open` event.
- Links rewritten through a signed redirect → GET hit ⇒ a `click` event, then
  302 to the original URL.

Tokens are HMAC-signed with the kind ('o'|'c') bound in, so an open token can't
be replayed as a click token and ids can't be forged. Same trust model as
threading_email.make_message_id.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html as html_lib
import re

from outreach.config import get_settings

# Smallest possible transparent GIF (43 bytes).
_PIXEL_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)

# Match bare http(s) URLs in plain text (stops at whitespace / common delimiters).
_URL_RE = re.compile(r"https?://[^\s<>\"')]+")


def _sig(payload: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.token_secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()[:16]


def make_token(step_run_id: int, kind: str) -> str:
    """kind: 'o' (open) or 'c' (click). Shape: '<id>.<hmac16>'."""
    return f"{step_run_id}.{_sig(f'{kind}:{step_run_id}')}"


def parse_token(token: str, kind: str) -> int | None:
    """Reverse of make_token. Returns step_run_id if the HMAC (bound to `kind`)
    verifies, else None."""
    if not token or "." not in token:
        return None
    id_part, sig = token.rsplit(".", 1)
    try:
        step_run_id = int(id_part)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sig(f"{kind}:{step_run_id}")):
        return None
    return step_run_id


def b64url_encode(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def b64url_decode(s: str) -> str:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad).decode()


def pixel_gif() -> bytes:
    return _PIXEL_GIF


def build_tracked_html(
    plain_body: str,
    *,
    step_run_id: int,
    base_url: str,
    track_opens: bool,
    track_clicks: bool,
) -> str:
    """Render a plain-text body as HTML with (optionally) click-rewritten links
    and a trailing open pixel. Used as the multipart/alternative HTML part."""
    base = (base_url or "").rstrip("/")
    click_token = make_token(step_run_id, "c") if (track_clicks and base) else None

    parts: list[str] = []
    last = 0
    for m in _URL_RE.finditer(plain_body):
        parts.append(html_lib.escape(plain_body[last:m.start()]))
        url = m.group(0)
        if click_token:
            tracked = f"{base}/t/c/{click_token}?u={b64url_encode(url)}"
            parts.append(f'<a href="{html_lib.escape(tracked)}">{html_lib.escape(url)}</a>')
        else:
            parts.append(f'<a href="{html_lib.escape(url)}">{html_lib.escape(url)}</a>')
        last = m.end()
    parts.append(html_lib.escape(plain_body[last:]))
    body_html = "".join(parts).replace("\n", "<br>\n")

    pixel = ""
    if track_opens and base:
        pixel = (
            f'<img src="{base}/t/o/{make_token(step_run_id, "o")}" '
            'width="1" height="1" alt="" style="display:none">'
        )
    return f"<!doctype html><html><body>{body_html}{pixel}</body></html>"
