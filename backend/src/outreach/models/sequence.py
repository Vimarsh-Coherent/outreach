from datetime import time

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, SmallInteger, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column

from outreach.db import Base
from outreach.models._mixins import SCHEMA, TimestampMixin


class Sequence(Base, TimestampMixin):
    __tablename__ = "sequences"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','active','paused','archived')", name="ck_sequences_status"
        ),
        CheckConstraint("send_window_start < send_window_end", name="ck_sequences_window"),
        CheckConstraint("send_days_mask BETWEEN 1 AND 127", name="ck_sequences_days"),
        Index("ix_sequences_user_status", "user_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)

    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", nullable=False)
    send_window_start: Mapped[time] = mapped_column(Time, default=time(0, 0), nullable=False)
    send_window_end: Mapped[time] = mapped_column(Time, default=time(23, 59), nullable=False)
    send_days_mask: Mapped[int] = mapped_column(SmallInteger, default=127, nullable=False)

    ai_followups_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_knowledge_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Email open/click tracking (per-sequence opt-in; off by default for
    # deliverability). Only takes effect when settings.tracking_base_url is set.
    track_opens: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    track_clicks: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
