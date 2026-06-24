"""Manage outbound webhook subscriptions (the 'sync with any CRM' path:
point these at a Zapier/Make/n8n catch hook)."""
from __future__ import annotations

import json
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.user import User
from outreach.models.webhook import WebhookEndpoint
from outreach.services import webhooks

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class WebhookCreate(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    event_types: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=200)


class WebhookOut(BaseModel):
    id: int
    url: str
    event_types: list[str]
    description: str | None
    active: bool
    secret: str
    created_at: datetime


def _out(e: WebhookEndpoint) -> WebhookOut:
    return WebhookOut(
        id=e.id, url=e.url, event_types=e.event_types, description=e.description,
        active=e.active, secret=e.secret, created_at=e.created_at,
    )


@router.get("/event-types", response_model=list[str])
async def event_types() -> list[str]:
    return webhooks.SUBSCRIBABLE_EVENT_TYPES


@router.get("", response_model=list[WebhookOut])
async def list_webhooks(
    user: User = Depends(get_current_user), session=Depends(get_session),
) -> list[WebhookOut]:
    rows = (await session.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.user_id == user.id).order_by(WebhookEndpoint.id.desc())
    )).scalars().all()
    return [_out(e) for e in rows]


@router.post("", response_model=WebhookOut, status_code=201)
async def create_webhook(
    dto: WebhookCreate,
    user: User = Depends(get_current_user), session=Depends(get_session),
) -> WebhookOut:
    if not dto.url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must be http(s)")
    types = webhooks.validate_event_types(dto.event_types)
    if not types:
        raise HTTPException(400, f"event_types must be a subset of {webhooks.SUBSCRIBABLE_EVENT_TYPES}")
    ep = WebhookEndpoint(
        user_id=user.id, url=dto.url, secret=webhooks.generate_secret(),
        event_types=types, description=dto.description, active=True,
    )
    session.add(ep)
    await session.commit()
    await session.refresh(ep)
    return _out(ep)


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: int,
    user: User = Depends(get_current_user), session=Depends(get_session),
) -> None:
    ep = await session.scalar(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == webhook_id, WebhookEndpoint.user_id == user.id
        )
    )
    if ep is None:
        raise HTTPException(404, "webhook not found")
    await session.delete(ep)
    await session.commit()


@router.post("/{webhook_id}/test")
async def test_webhook(
    webhook_id: int,
    user: User = Depends(get_current_user), session=Depends(get_session),
) -> dict:
    """Synchronously POST a sample signed payload so you can confirm the
    receiver verifies the signature."""
    ep = await session.scalar(
        select(WebhookEndpoint).where(
            WebhookEndpoint.id == webhook_id, WebhookEndpoint.user_id == user.id
        )
    )
    if ep is None:
        raise HTTPException(404, "webhook not found")
    body = json.dumps({
        "event": "test",
        "data": {"message": "Coherent Outreach webhook test"},
    }, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Coherent-Event": "test",
        "X-Coherent-Signature": webhooks.signature_header(ep.secret, body),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(ep.url, content=body, headers=headers)
        return {"ok": 200 <= resp.status_code < 300, "status": resp.status_code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"}
