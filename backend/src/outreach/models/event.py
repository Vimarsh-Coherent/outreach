from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from outreach.db import Base
from outreach.models._mixins import SCHEMA, TimestampMixin


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('reply','delivered','bounce','auto_reply','open','click',"
            "'connection_accepted','dm_delivered','error','manual_task')",
            name="ck_events_type",
        ),
        Index("ix_events_enrolment", "enrolment_id", "occurred_at"),
        Index(
            "uq_events_external",
            "enrolment_id",
            "event_type",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enrolment_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.enrolments.id", ondelete="CASCADE"), nullable=False
    )
    step_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.step_runs.id")
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )


class ReplySentiment(Base, TimestampMixin):
    __tablename__ = "reply_sentiment"
    __table_args__ = (
        CheckConstraint(
            "label IN ('positive','interested','objection','negative','unsubscribe',"
            "'auto_reply','neutral')",
            name="ck_sentiment_label",
        ),
        Index("ix_sentiment_label", "label"),
        {"schema": SCHEMA},
    )

    event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA}.events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    label: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(String(500))
    model: Mapped[str] = mapped_column(String(40), nullable=False)
