"""Authoring tools — spam/deliverability content scoring for a draft message."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from outreach.deps import get_current_user
from outreach.models.user import User
from outreach.services import spam_check

router = APIRouter(prefix="/api/tools", tags=["tools"])


class SpamCheckRequest(BaseModel):
    subject: str | None = Field(default=None, max_length=500)
    body: str = Field(default="", max_length=20000)


class SpamFindingOut(BaseModel):
    category: str
    severity: str
    message: str
    suggestion: str | None = None


class SpamCheckResponse(BaseModel):
    score: int
    verdict: str
    findings: list[SpamFindingOut]


@router.post("/spam-check", response_model=SpamCheckResponse)
async def spam_check_endpoint(
    dto: SpamCheckRequest,
    _user: User = Depends(get_current_user),
) -> SpamCheckResponse:
    return SpamCheckResponse(**spam_check.check(dto.subject, dto.body))
