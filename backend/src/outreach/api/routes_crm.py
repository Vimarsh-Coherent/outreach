"""Native CRM connector routes (HubSpot OAuth + sync)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from outreach.config import get_settings
from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.integration import CrmIntegration
from outreach.models.user import User
from outreach.services import hubspot, leads_service
from outreach.utils.crypto import encrypt_json

log = logging.getLogger("outreach.crm")
router = APIRouter(prefix="/api/crm", tags=["crm"])


async def _get_hubspot(session, user_id: int) -> CrmIntegration | None:
    return await session.scalar(
        select(CrmIntegration).where(
            CrmIntegration.user_id == user_id, CrmIntegration.provider == "hubspot"
        )
    )


@router.get("/hubspot/status")
async def hubspot_status(
    user: User = Depends(get_current_user), session=Depends(get_session),
) -> dict:
    integ = await _get_hubspot(session, user.id)
    return {
        "configured": hubspot.is_configured(),
        "connected": integ is not None and integ.status == "connected",
        "portal_name": integ.portal_name if integ else None,
        "last_synced_at": integ.last_synced_at.isoformat() if integ and integ.last_synced_at else None,
    }


@router.get("/hubspot/connect")
async def hubspot_connect(user: User = Depends(get_current_user)) -> dict:
    if not hubspot.is_configured():
        raise HTTPException(400, "HubSpot app not configured — set HUBSPOT_CLIENT_ID / SECRET in backend/.env")
    # state carries the user id back to the (unauthenticated) callback.
    return {"authorize_url": hubspot.authorize_url(state=str(user.id))}


@router.get("/hubspot/callback")
async def hubspot_callback(code: str = "", state: str = "", session=Depends(get_session)):
    """HubSpot redirects the browser here after the user grants access."""
    settings = get_settings()
    dest = f"{settings.frontend_base_url}/integrations"
    if not code or not state.isdigit():
        return RedirectResponse(f"{dest}?hubspot=error", status_code=302)
    user_id = int(state)
    try:
        tokens = await hubspot.exchange_code(code)
        portal = None
        hub_id = None
        try:
            info = await hubspot.token_info(tokens["access_token"])
            portal = info.get("hub_domain")
            hub_id = info.get("hub_id")
        except Exception:  # noqa: BLE001
            pass
        cfg = hubspot.build_config(tokens, hub_id)
        integ = await _get_hubspot(session, user_id)
        if integ is None:
            integ = CrmIntegration(
                user_id=user_id, provider="hubspot", status="connected",
                portal_name=portal, config_encrypted=encrypt_json(cfg), field_mapping={},
            )
            session.add(integ)
        else:
            integ.status = "connected"
            integ.portal_name = portal
            integ.config_encrypted = encrypt_json(cfg)
        await session.commit()
        return RedirectResponse(f"{dest}?hubspot=connected", status_code=302)
    except Exception as e:  # noqa: BLE001
        log.exception("hubspot oauth callback failed")
        return RedirectResponse(f"{dest}?hubspot=error&msg={type(e).__name__}", status_code=302)


@router.post("/hubspot/disconnect", status_code=204)
async def hubspot_disconnect(
    user: User = Depends(get_current_user), session=Depends(get_session),
) -> None:
    integ = await _get_hubspot(session, user.id)
    if integ is not None:
        await session.delete(integ)
        await session.commit()


@router.post("/hubspot/sync-contacts")
async def hubspot_sync_contacts(
    user: User = Depends(get_current_user), session=Depends(get_session),
) -> dict:
    """Pull contacts from HubSpot into Coherent leads (paged, capped)."""
    integ = await _get_hubspot(session, user.id)
    if integ is None or integ.status != "connected":
        raise HTTPException(400, "HubSpot not connected")
    token = await hubspot.get_access_token(session, integ)
    mapping = integ.field_mapping or None

    rows: list[dict] = []
    after: str | None = None
    for _ in range(10):  # up to ~1000 contacts per run
        contacts, after = await hubspot.list_contacts(token, after=after)
        rows.extend(hubspot.contact_to_lead(c, mapping) for c in contacts)
        if not after:
            break
    rows = [r for r in rows if r.get("email") or r.get("phone")]
    stats = await leads_service.upsert_leads(session, user.id, rows, source="hubspot")
    integ.last_synced_at = datetime.now(timezone.utc)
    await session.commit()
    return {"pulled": len(rows), "inserted": stats.inserted, "updated": stats.updated}
