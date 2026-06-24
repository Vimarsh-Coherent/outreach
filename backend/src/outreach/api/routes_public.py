"""Public REST API (v1) — authenticated by X-Api-Key. Lets any external system
(or middleware like Zapier/Make) push leads in and pull events out."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from outreach.db import get_session
from outreach.deps import get_api_user
from outreach.models.user import User
from outreach.services import leads_service

router = APIRouter(prefix="/api/v1", tags=["public-api"])


class PublicLead(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    linkedin_url: str | None = Field(default=None, max_length=300)


@router.post("/leads", status_code=201)
async def create_lead(
    dto: PublicLead,
    user: User = Depends(get_api_user),
    session=Depends(get_session),
) -> dict:
    stats = await leads_service.upsert_leads(
        session, user.id, [dto.model_dump()], source="api"
    )
    return {
        "inserted": stats.inserted,
        "updated": stats.updated,
        "skipped_no_identity": stats.skipped_no_identity,
    }


@router.get("/events")
async def list_events(
    limit: int = 50,
    user: User = Depends(get_api_user),
    session=Depends(get_session),
) -> dict:
    """Recent reply/delivery/open/click events for this account, with sentiment."""
    limit = max(1, min(limit, 200))
    rows = (await session.execute(text("""
        SELECT e.id, e.event_type, e.channel, e.occurred_at, e.payload,
               en.lead_id, l.email, l.first_name, l.last_name, l.company,
               rs.label AS sentiment, rs.confidence
          FROM outreach.events e
          JOIN outreach.enrolments en ON en.id = e.enrolment_id
          LEFT JOIN outreach.leads l ON l.id = en.lead_id
          LEFT JOIN outreach.reply_sentiment rs ON rs.event_id = e.id
         WHERE en.user_id = :uid
         ORDER BY e.id DESC
         LIMIT :lim
    """), {"uid": user.id, "lim": limit})).mappings().all()
    return {"events": [
        {
            "id": r["id"], "event": r["event_type"], "channel": r["channel"],
            "occurred_at": r["occurred_at"].isoformat() if r["occurred_at"] else None,
            "lead": {"id": r["lead_id"], "email": r["email"],
                     "first_name": r["first_name"], "last_name": r["last_name"], "company": r["company"]},
            "sentiment": r["sentiment"], "confidence": r["confidence"],
            "data": r["payload"] or {},
        }
        for r in rows
    ]}
