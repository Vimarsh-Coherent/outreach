from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from outreach.db import Base
from outreach.models._mixins import SCHEMA


class LinkedInCommand(Base):
    __tablename__ = "li_commands"
    __table_args__ = (
        CheckConstraint(
            "command_type IN ('dm','connect','view_profile','like_posts')", name="ck_licmd_type"
        ),
        CheckConstraint(
            "status IN ('pending','claimed','done','failed','expired')", name="ck_licmd_status"
        ),
        Index(
            "ix_licmd_pending",
            "user_id",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    enrolment_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.enrolments.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[int | None] = mapped_column(ForeignKey(f"{SCHEMA}.sequence_steps.id"), nullable=True)
    command_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_li_url: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
