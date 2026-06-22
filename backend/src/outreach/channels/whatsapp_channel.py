"""Client for the Baileys WhatsApp sidecar (whatsapp-sidecar/server.js).

The sidecar owns the logged-in WhatsApp session and exposes a tiny REST API on
wa_api_url. This module is the backend's side of that contract: outbound sends
(send_whatsapp) and connection/QR/logout helpers for the Channels UI.

Mirrors channels/email_channel.py: send_whatsapp never raises — any transport
or sidecar error comes back as SendResult(ok=False, error=...).
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from outreach.config import get_settings


@dataclass(slots=True)
class SendResult:
    ok: bool
    error: str | None = None
    provider_message_id: str | None = None


def _base_and_headers() -> tuple[str, dict[str, str], int]:
    s = get_settings()
    base = (s.wa_api_url or "http://127.0.0.1:8085").rstrip("/")
    headers = {"Content-Type": "application/json"}
    if s.wa_api_key:
        headers["X-Api-Key"] = s.wa_api_key
    return base, headers, s.wa_timeout_seconds


async def send_whatsapp(*, phone: str, body: str) -> SendResult:
    """POST /sendText to the sidecar. `phone` should be E.164 (e.g. +14155552671)."""
    base, headers, timeout = _base_and_headers()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base}/sendText",
                json={"to": phone, "content": body},
                headers=headers,
            )
        data: dict = resp.json() if resp.content else {}
        if resp.status_code == 200 and data.get("ok"):
            return SendResult(ok=True, provider_message_id=data.get("id"))
        return SendResult(ok=False, error=str(data.get("error") or f"HTTP {resp.status_code}"))
    except Exception as e:  # noqa: BLE001
        return SendResult(ok=False, error=f"{type(e).__name__}: {e}")


async def get_connection_state() -> dict:
    """GET /getConnectionState. Returns the sidecar's state, or a synthetic
    'unavailable' state if the sidecar isn't reachable (so the UI can show
    'connect WhatsApp' rather than erroring)."""
    base, headers, timeout = _base_and_headers()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base}/getConnectionState", headers=headers)
        if resp.status_code == 200:
            return resp.json()
        return {"state": "unavailable", "connected": False, "me": None}
    except Exception:  # noqa: BLE001
        return {"state": "unavailable", "connected": False, "me": None}


async def get_qr() -> dict:
    """GET /qr → {state, qr}. Synthetic 'unavailable' if the sidecar is down."""
    base, headers, timeout = _base_and_headers()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{base}/qr", headers=headers)
        if resp.status_code == 200:
            return resp.json()
        return {"state": "unavailable", "qr": None}
    except Exception:  # noqa: BLE001
        return {"state": "unavailable", "qr": None}


# PAIRING CODE FEATURE — remove this function to disable phone-number linking
async def request_pairing_code(phone: str) -> dict:
    """POST /requestPairingCode → {ok, code}. Phone should be digits only."""
    base, headers, timeout = _base_and_headers()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base}/requestPairingCode",
                json={"phone": phone},
                headers=headers,
            )
        return resp.json() if resp.content else {"ok": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
# END PAIRING CODE FEATURE


async def logout() -> dict:
    """POST /logout → wipe the session so a fresh QR is generated."""
    base, headers, timeout = _base_and_headers()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base}/logout", headers=headers)
        return resp.json() if resp.content else {"ok": resp.status_code == 200}
