"""Edge cases for the WhatsApp sidecar client (channels/whatsapp_channel.py).

send_whatsapp must mirror email_channel.send_email: never raise — every
transport/sidecar failure comes back as SendResult(ok=False, error=...). The
status/QR helpers degrade to a synthetic 'unavailable' state when the sidecar
is unreachable, so the Channels UI shows 'connect WhatsApp' instead of erroring.
"""
from types import SimpleNamespace

import pytest

from outreach.channels import whatsapp_channel as wa


class _Resp:
    def __init__(self, status_code: int, json_data: dict | None, content: bytes = b"x"):
        self.status_code = status_code
        self._json = json_data
        self.content = content

    def json(self) -> dict:
        if self._json is None:
            raise ValueError("no json")
        return self._json


def _patch_client(monkeypatch, *, resp=None, exc: Exception | None = None):
    """Install a fake httpx.AsyncClient whose post/get return `resp` or raise `exc`."""
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            if exc:
                raise exc
            return resp

        async def get(self, *a, **k):
            if exc:
                raise exc
            return resp

    monkeypatch.setattr(wa.httpx, "AsyncClient", _FakeClient)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(
        wa, "get_settings",
        lambda: SimpleNamespace(
            wa_api_url="http://127.0.0.1:8085", wa_api_key="", wa_timeout_seconds=5,
        ),
    )


# ── send_whatsapp ────────────────────────────────────────────────────────────

async def test_send_success_returns_message_id(monkeypatch):
    _patch_client(monkeypatch, resp=_Resp(200, {"ok": True, "id": "WAMID.123"}))
    r = await wa.send_whatsapp(phone="+14155552671", body="hi")
    assert r.ok is True
    assert r.provider_message_id == "WAMID.123"


async def test_send_not_connected_maps_to_error(monkeypatch):
    # Sidecar returns 503 {ok:false, error:"not connected (qr)"} when unlinked.
    _patch_client(monkeypatch, resp=_Resp(503, {"ok": False, "error": "not connected (qr)"}))
    r = await wa.send_whatsapp(phone="+14155552671", body="hi")
    assert r.ok is False
    assert "not connected" in r.error


async def test_send_http_error_without_ok(monkeypatch):
    _patch_client(monkeypatch, resp=_Resp(502, {}))
    r = await wa.send_whatsapp(phone="+14155552671", body="hi")
    assert r.ok is False
    assert "HTTP 502" in r.error


async def test_send_transport_exception_never_raises(monkeypatch):
    _patch_client(monkeypatch, exc=RuntimeError("connection refused"))
    r = await wa.send_whatsapp(phone="+14155552671", body="hi")
    assert r.ok is False
    assert "connection refused" in r.error


# ── get_connection_state / get_qr — unavailable fallback ─────────────────────

async def test_connection_state_unavailable_when_sidecar_down(monkeypatch):
    _patch_client(monkeypatch, exc=RuntimeError("no route to host"))
    state = await wa.get_connection_state()
    assert state == {"state": "unavailable", "connected": False, "me": None}


async def test_connection_state_passthrough(monkeypatch):
    _patch_client(monkeypatch, resp=_Resp(200, {"state": "connected", "connected": True, "me": "1@s"}))
    state = await wa.get_connection_state()
    assert state["connected"] is True


async def test_qr_unavailable_when_sidecar_down(monkeypatch):
    _patch_client(monkeypatch, exc=RuntimeError("down"))
    data = await wa.get_qr()
    assert data == {"state": "unavailable", "qr": None}


async def test_api_key_header_injected(monkeypatch):
    captured = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **k):
            captured["headers"] = k.get("headers")
            return _Resp(200, {"ok": True, "id": "x"})

    monkeypatch.setattr(
        wa, "get_settings",
        lambda: SimpleNamespace(
            wa_api_url="http://127.0.0.1:8085", wa_api_key="secret123", wa_timeout_seconds=5,
        ),
    )
    monkeypatch.setattr(wa.httpx, "AsyncClient", _FakeClient)
    await wa.send_whatsapp(phone="+14155552671", body="hi")
    assert captured["headers"]["X-Api-Key"] == "secret123"
