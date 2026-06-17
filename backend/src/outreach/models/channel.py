from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from outreach.db import Base
from outreach.models._mixins import SCHEMA, TimestampMixin


class Channel(Base, TimestampMixin):
    __tablename__ = "channels"
    __table_args__ = (
        CheckConstraint(
            "channel_type IN ('email','linkedin')", name="ck_channels_type"
        ),
        CheckConstraint(
            "status IN ('active','paused','invalid')", name="ck_channels_status"
        ),
        Index("ix_channels_user_type", "user_id", "channel_type", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False)
    display_label: Mapped[str] = mapped_column(String(120), nullable=False)
    config_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    daily_cap: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    sent_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_today_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ext_last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
