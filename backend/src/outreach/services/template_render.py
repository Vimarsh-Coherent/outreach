import re

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\|([^}]*))?\}\}")

# Tokens that can be substituted in subject + body.
# Contact tokens come from the contact snapshot; sender_name and meeting_link are
# injected from the sending user's profile at render time (see workers/dispatcher.py).
SUPPORTED_TOKENS = (
    "first_name", "last_name", "company", "title", "email", "sender_name", "meeting_link",
)


def build_render_snapshot(
    contact_snapshot: dict | None,
    *,
    sender_name: str = "",
    meeting_link: str = "",
    include_meeting_link: bool = True,
) -> dict:
    """Merge contact fields with sender tokens for template rendering.

    meeting_link is blanked when include_meeting_link is False so the first
    outreach email never exposes a scheduling URL even if the template contains
    {{meeting_link}}.
    """
    snap = dict(contact_snapshot or {})
    snap["sender_name"] = sender_name
    snap["meeting_link"] = meeting_link if include_meeting_link else ""
    return snap


def render(text: str | None, snapshot: dict) -> str:
    if text is None:
        return ""

    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        default = (m.group(2) or "").strip()
        value = snapshot.get(key)
        if value is None or value == "":
            return default
        return str(value)

    return _TOKEN_RE.sub(sub, text)
