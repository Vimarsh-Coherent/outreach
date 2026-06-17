import re

_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\|([^}]*))?\}\}")

# Tokens that can be substituted in subject + body.
# Keys are the canonical contact-snapshot field names.
SUPPORTED_TOKENS = ("first_name", "last_name", "company", "title", "email")


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
