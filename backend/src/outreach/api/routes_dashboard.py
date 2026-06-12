from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.db import get_session
from outreach.deps import get_current_user
from outreach.models.user import User
from outreach.schemas.dashboard import (
    AtRiskEnrolment,
    DashboardSummary,
    HotLead,
    SentimentTimeseriesResponse,
    SequenceStats,
)
from outreach.services import dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(
    days: int = Query(default=30, ge=1, le=365),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DashboardSummary:
    return await dashboard_service.summary(session, user.id, days=days)


@router.get("/sentiment-timeseries", response_model=SentimentTimeseriesResponse)
async def get_sentiment_timeseries(
    days: int = Query(default=30, ge=1, le=365),
    bucket: Literal["day", "week"] = "day",
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SentimentTimeseriesResponse:
    return await dashboard_service.sentiment_timeseries(
        session, user.id, days=days, bucket=bucket
    )


@router.get("/sequences", response_model=list[SequenceStats])
async def get_sequences_stats(
    days: int = Query(default=30, ge=1, le=365),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SequenceStats]:
    return await dashboard_service.sequences_stats(session, user.id, days=days)


@router.get("/hot-leads", response_model=list[HotLead])
async def get_hot_leads(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=25, ge=1, le=200),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[HotLead]:
    return await dashboard_service.hot_leads(session, user.id, days=days, limit=limit)


@router.get("/at-risk", response_model=list[AtRiskEnrolment])
async def get_at_risk(
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=25, ge=1, le=200),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AtRiskEnrolment]:
    return await dashboard_service.at_risk(session, user.id, hours=hours, limit=limit)
