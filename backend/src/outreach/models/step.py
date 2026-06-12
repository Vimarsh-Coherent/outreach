from sqlalchemy import CheckConstraint, ForeignKey, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from outreach.db import Base
from outreach.models._mixins import SCHEMA, TimestampMixin


class SequenceStep(Base, TimestampMixin):
    __tablename__ = "sequence_steps"
    __table_args__ = (
        UniqueConstraint("sequence_id", "step_order", name="uq_steps_order"),
        CheckConstraint(
            "channel IN ('email','linkedin_dm','linkedin_connect')", name="ck_steps_channel"
        ),
        CheckConstraint("delay_days BETWEEN 0 AND 365", name="ck_steps_delay_days"),
        CheckConstraint("delay_hours BETWEEN 0 AND 23", name="ck_steps_delay_hours"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sequence_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.sequences.id", ondelete="CASCADE"), nullable=False
    )
    step_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    delay_days: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    delay_hours: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)

    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
