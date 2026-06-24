"""HubSpot connector — pure helpers (OAuth URL, field mapping, token expiry)."""
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from outreach.services import hubspot as hs


def _settings(**kw):
    base = dict(
        hubspot_client_id="cid-123", hubspot_client_secret="secret",
        hubspot_redirect_uri="http://localhost:8000/api/crm/hubspot/callback",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── is_configured / authorize_url ────────────────────────────────────────────

def test_is_configured(monkeypatch):
    monkeypatch.setattr(hs, "get_settings", lambda: _settings())
    assert hs.is_configured() is True
    monkeypatch.setattr(hs, "get_settings", lambda: _settings(hubspot_client_id=""))
    assert hs.is_configured() is False


def test_authorize_url_has_required_params(monkeypatch):
    monkeypatch.setattr(hs, "get_settings", lambda: _settings())
    url = hs.authorize_url(state="42")
    assert url.startswith(hs.AUTHORIZE_URL)
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["cid-123"]
    assert q["redirect_uri"] == ["http://localhost:8000/api/crm/hubspot/callback"]
    assert q["state"] == ["42"]
    assert "crm.objects.contacts.read" in q["scope"][0]


# ── token expiry ─────────────────────────────────────────────────────────────

def test_needs_refresh():
    assert hs.needs_refresh(time.time() - 10) is True        # already expired
    assert hs.needs_refresh(time.time() + 30) is True         # within 2-min skew
    assert hs.needs_refresh(time.time() + 3600) is False      # fresh
    assert hs.needs_refresh(0) is True                        # unset


def test_build_config_sets_future_expiry():
    cfg = hs.build_config({"access_token": "a", "refresh_token": "r", "expires_in": 1800}, hub_id=9)
    assert cfg["access_token"] == "a" and cfg["refresh_token"] == "r" and cfg["hub_id"] == 9
    assert cfg["expires_at"] > time.time() + 1700


# ── field mapping ────────────────────────────────────────────────────────────

def test_lead_to_props_default_mapping_drops_empty():
    props = hs.lead_to_props({"email": "a@b.com", "first_name": "Ann", "last_name": "", "company": None, "title": "CTO"})
    assert props == {"email": "a@b.com", "firstname": "Ann", "jobtitle": "CTO"}


def test_contact_to_lead_inverts_mapping():
    contact = {"properties": {"email": "a@b.com", "firstname": "Ann", "jobtitle": "CTO", "unknownprop": "x"}}
    lead = hs.contact_to_lead(contact)
    assert lead == {"email": "a@b.com", "first_name": "Ann", "title": "CTO"}


def test_mapping_roundtrip_custom():
    mapping = {"email": "email", "company": "company"}
    lead = {"email": "x@y.com", "company": "Acme", "title": "ignored"}
    props = hs.lead_to_props(lead, mapping)
    assert props == {"email": "x@y.com", "company": "Acme"}
    back = hs.contact_to_lead({"properties": props}, mapping)
    assert back == {"email": "x@y.com", "company": "Acme"}
