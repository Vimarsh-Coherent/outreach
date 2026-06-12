import hashlib
import re
from dataclasses import dataclass

import phonenumbers
from email_validator import EmailNotValidError, validate_email

LI_PATH_RE = re.compile(r"/(?:in|pub)/([^/?#\s]+)/?", re.IGNORECASE)
LI_SLUG_RE = re.compile(r"^[a-z0-9\-_.]{3,100}$")


@dataclass(slots=True)
class CanonicalIdentity:
    email: str | None
    phone: str | None       # E.164
    linkedin_slug: str | None
    hash: bytes | None      # sha256 of the primary identifier, or None if no identifier
    primary: str | None     # which field was used: 'email' | 'phone' | 'linkedin'


def normalize_email(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        info = validate_email(raw, check_deliverability=False)
        return info.normalized.lower()
    except EmailNotValidError:
        return None


def normalize_phone(raw: str | None, default_region: str = "US") -> str | None:
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    for region in (None, default_region):
        try:
            n = phonenumbers.parse(raw, region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(n):
            return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164)
    return None


def normalize_linkedin(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = LI_PATH_RE.search(s)
    if m:
        slug = m.group(1).rstrip("/").lower()
        if LI_SLUG_RE.match(slug):
            return slug
        return None
    # User passed a bare slug
    s = s.lstrip("@").lower().rstrip("/")
    if LI_SLUG_RE.match(s):
        return s
    return None


def canonical_identity(
    *, email: str | None = None, phone: str | None = None, linkedin: str | None = None
) -> CanonicalIdentity:
    e = normalize_email(email)
    p = normalize_phone(phone)
    li = normalize_linkedin(linkedin)
    if e:
        primary_key, primary = f"email:{e}", "email"
    elif p:
        primary_key, primary = f"phone:{p}", "phone"
    elif li:
        primary_key, primary = f"linkedin:{li}", "linkedin"
    else:
        return CanonicalIdentity(email=e, phone=p, linkedin_slug=li, hash=None, primary=None)
    h = hashlib.sha256(primary_key.encode()).digest()
    return CanonicalIdentity(email=e, phone=p, linkedin_slug=li, hash=h, primary=primary)


def linkedin_url_from_slug(slug: str | None) -> str | None:
    return f"https://www.linkedin.com/in/{slug}" if slug else None
