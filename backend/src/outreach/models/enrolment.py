from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from outreach.db import Base
from outreach.models._mixins import SCHEMA, TimestampMixin


class Enrolment(Base, TimestampMixin):
    __tablename__ = "enrolments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','paused','done','stopped_reply','stopped_bounce',"
            "'stopped_manual','stopped_archived','errored')",
            name="ck_enrolments_status",
        ),
        Index("ix_enrol_sequence", "sequence_id", "status"),
        Index("ix_enrol_user", "user_id", "status"),
        Index(
            "ix_enrol_due",
            "next_send_at",
            postgresql_where=text(
                "status = 'active' AND next_send_at IS NOT NULL"
            ),
        ),
        Index(
            "uq_enrol_active_identity",
            "sequence_id",
            "identity_hash",
            unique=True,
            postgresql_where=text("status IN ('active','paused')"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sequence_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.sequences.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.leads.id", ondelete="CASCADE"), nullable=False
    )
    identity_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    contact_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    current_step_order: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    # Branching: explicit next node chosen by a transition (overrides step_order
    # ordering). NULL = follow linear order.
    next_step_id: Mapped[int | None] = mapped_column(BigInteger)
    next_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runtime_state: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_reason: Mapped[str | None] = mapped_column(Text)
