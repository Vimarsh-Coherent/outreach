from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from outreach.db import Base
from outreach.models._mixins import SCHEMA, TimestampMixin


class StepRun(Base, TimestampMixin):
    __tablename__ = "step_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','reserving','sent','failed','bounced','queued_external','skipped')",
            name="ck_runs_status",
        ),
        Index(
            "ix_runs_enrolment_sent",
            "enrolment_id",
            "sent_at",
            postgresql_where=text("status = 'sent'"),
        ),
        Index(
            "ix_runs_provider",
            "provider_message_id",
            postgresql_where=text("provider_message_id IS NOT NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enrolment_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.enrolments.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.sequence_steps.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    error_message: Mapped[str | None] = mapped_column(Text)
