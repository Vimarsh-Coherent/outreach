from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DashboardSummary(BaseModel):
    period_days: int
    enrolled: int
    contacted: int
    replied: int
    positive_replies: int
    bounced: int
    unsubscribed: int
    reply_rate: float
    positive_rate: float


class SentimentBucket(BaseModel):
    bucket_start: datetime
    positive: int = 0
    interested: int = 0
    objection: int = 0
    negative: int = 0
    unsubscribe: int = 0
    auto_reply: int = 0
    neutral: int = 0


class SentimentTimeseriesResponse(BaseModel):
    bucket: Literal["day", "week"]
    points: list[SentimentBucket]


class SequenceStats(BaseModel):
    id: int
    name: str
    status: str
    sends: int
    replies: int
    bounces: int
    positive_replies: int
    reply_rate: float


class HotLead(BaseModel):
    lead_id: int
    name: str | None
    email: str | None
    company: str | None
    title: str | None
    latest_sentiment: str
    latest_confidence: float
    latest_reply_at: datetime
    sequence_id: int
    sequence_name: str | None


class AtRiskEnrolment(BaseModel):
    enrolment_id: int
    lead_id: int
    lead_email: str | None
    lead_name: str | None
    sequence_id: int
    sequence_name: str | None
    status: str
    stuck_since: datetime | None
    reason: str | None
