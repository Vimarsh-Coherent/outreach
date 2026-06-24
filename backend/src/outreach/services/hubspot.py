"""HubSpot native CRM connector — OAuth + Contacts/Notes API.

Pure helpers (URL building, field mapping, token-expiry) are unit-tested; the
HTTP calls use httpx. OAuth tokens are stored encrypted on the CrmIntegration.
"""
from __future__ import annotations

import time
import urllib.parse

import httpx

from outreach.config import get_settings
from outreach.utils.crypto import decrypt_json, encrypt_json

AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
API = "https://api.hubapi.com"
SCOPES = "crm.objects.contacts.read crm.objects.contacts.write"
# HubSpot association type id for note → contact.
NOTE_TO_CONTACT = 202

# Coherent lead field → HubSpot contact property.
DEFAULT_MAPPING: dict[str, str] = {
    "email": "email",
    "first_name": "firstname",
    "last_name": "lastname",
    "company": "company",
    "title": "jobtitle",
    "phone": "phone",
}


def is_configured() -> bool:
    s = get_settings()
    return bool(s.hubspot_client_id.strip() and s.hubspot_client_secret.strip())


def authorize_url(state: str) -> str:
    s = get_settings()
    params = {
        "client_id": s.hubspot_client_id,
        "redirect_uri": s.hubspot_redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def needs_refresh(expires_at: float) -> bool:
    """True if the access token expires within 2 minutes (or already has)."""
    return time.time() >= (float(expires_at or 0) - 120)


def lead_to_props(lead: dict, mapping: dict | None = None) -> dict:
    """Map a Coherent lead dict → HubSpot contact properties (non-empty only)."""
    m = mapping or DEFAULT_MAPPING
    return {dst: lead[src] for src, dst in m.items() if lead.get(src)}


def contact_to_lead(contact: dict, mapping: dict | None = None) -> dict:
    """Map a HubSpot contact → a Coherent lead dict."""
    m = mapping or DEFAULT_MAPPING
    inverse = {dst: src for src, dst in m.items()}
    props = contact.get("properties", {}) or {}
    return {inverse[k]: v for k, v in props.items() if k in inverse and v}


# ── OAuth HTTP ───────────────────────────────────────────────────────────────

async def exchange_code(code: str) -> dict:
    s = get_settings()
    data = {
        "grant_type": "authorization_code",
        "client_id": s.hubspot_client_id,
        "client_secret": s.hubspot_client_secret,
        "redirect_uri": s.hubspot_redirect_uri,
        "code": code,
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(TOKEN_URL, data=data)
    r.raise_for_status()
    return r.json()


async def refresh_tokens(refresh_token: str) -> dict:
    s = get_settings()
    data = {
        "grant_type": "refresh_token",
        "client_id": s.hubspot_client_id,
        "client_secret": s.hubspot_client_secret,
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(TOKEN_URL, data=data)
    r.raise_for_status()
    return r.json()


async def token_info(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{API}/oauth/v1/access-tokens/{access_token}")
    r.raise_for_status()
    return r.json()


def build_config(tokens: dict, hub_id: int | None = None) -> dict:
    """Shape a token response into the stored (encrypted) config."""
    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_at": time.time() + int(tokens.get("expires_in", 1800)),
        "hub_id": hub_id,
    }


async def get_access_token(session, integration) -> str:
    """Return a valid access token, refreshing + persisting if near expiry."""
    cfg = decrypt_json(integration.config_encrypted)
    if needs_refresh(cfg.get("expires_at", 0)):
        tok = await refresh_tokens(cfg["refresh_token"])
        cfg = build_config(
            {**tok, "refresh_token": tok.get("refresh_token", cfg["refresh_token"])},
            cfg.get("hub_id"),
        )
        integration.config_encrypted = encrypt_json(cfg)
        await session.commit()
    return cfg["access_token"]


# ── Contacts / Notes ─────────────────────────────────────────────────────────

async def upsert_contact(token: str, props: dict) -> str | None:
    """Create or update a contact by email. Returns the HubSpot contact id."""
    headers = {"Authorization": f"Bearer {token}"}
    email = props.get("email")
    async with httpx.AsyncClient(timeout=20) as c:
        cid = None
        if email:
            r = await c.post(
                f"{API}/crm/v3/objects/contacts/search", headers=headers,
                json={
                    "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
                    "properties": ["email"], "limit": 1,
                },
            )
            if r.status_code == 200 and r.json().get("results"):
                cid = r.json()["results"][0]["id"]
        if cid:
            await c.patch(f"{API}/crm/v3/objects/contacts/{cid}", headers=headers, json={"properties": props})
        else:
            r = await c.post(f"{API}/crm/v3/objects/contacts", headers=headers, json={"properties": props})
            if r.status_code in (200, 201):
                cid = r.json().get("id")
    return cid


async def log_note(token: str, contact_id: str, body: str) -> bool:
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "properties": {"hs_note_body": body[:65000], "hs_timestamp": int(time.time() * 1000)},
        "associations": [{
            "to": {"id": contact_id},
            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": NOTE_TO_CONTACT}],
        }],
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/crm/v3/objects/notes", headers=headers, json=payload)
    return r.status_code in (200, 201)


async def list_contacts(token: str, after: str | None = None, limit: int = 100) -> tuple[list[dict], str | None]:
    headers = {"Authorization": f"Bearer {token}"}
    params: dict = {"limit": limit, "properties": ",".join(DEFAULT_MAPPING.values())}
    if after:
        params["after"] = after
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{API}/crm/v3/objects/contacts", headers=headers, params=params)
    r.raise_for_status()
    j = r.json()
    next_after = ((j.get("paging") or {}).get("next") or {}).get("after")
    return j.get("results", []), next_after
