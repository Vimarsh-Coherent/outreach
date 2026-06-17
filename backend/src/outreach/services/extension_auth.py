"""FastAPI dependency that verifies a Chrome extension's `X-Extension-Token`
header against a stored HMAC hash on the linkedin channel row.

Pattern matches PLAN.md §7.1: the user creates a LinkedIn channel via
`POST /api/channels/linkedin`, gets back `{channel_id, raw_token}` ONCE,
pastes both into the extension popup. Every extension request sends:

    X-Extension-Channel-Id: <int>
    X-Extension-Token: <opaque>

The auth dependency:
  1. Looks up the channel by id (cheap PK fetch).
  2. Decrypts its config, pulls the stored HMAC.
  3. HMACs the inbound token with TOKEN_SECRET; compares constant-time.
  4. Returns the Channel ORM row.

DB read access alone does NOT yield a forgeable token (raw is never stored).
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.models.channel import Channel
from outreach.utils.crypto import constant_time_equals, decrypt_json, hmac_token


async def get_extension_channel(
    x_extension_channel_id: int | None = Header(default=None, alias="X-Extension-Channel-Id"),
    x_extension_token: str | None = Header(default=None, alias="X-Extension-Token"),
    session: AsyncSession = Depends(get_session),
) -> Channel:
    if x_extension_channel_id is None or not x_extension_token:
        raise HTTPException(401, "missing X-Extension-Channel-Id or X-Extension-Token")
    channel = await session.scalar(
        select(Channel).where(
            Channel.id == x_extension_channel_id,
            Channel.channel_type == "linkedin",
            Channel.status == "active",
        )
    )
    if channel is None:
        raise HTTPException(401, "channel not found or paused")
    try:
        cfg = decrypt_json(channel.config_encrypted)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"channel config corrupt: {e}") from e
    stored_hash = cfg.get("linkedin", {}).get("token_hash") or ""
    presented_hash = hmac_token(x_extension_token)
    if not stored_hash or not constant_time_equals(stored_hash, presented_hash):
        raise HTTPException(401, "invalid token")
    return channel
