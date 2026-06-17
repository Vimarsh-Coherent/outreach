from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from outreach.db import Base
from outreach.models._mixins import SCHEMA


class VaultIndex(Base):
    __tablename__ = "vault_index"
    __table_args__ = ({"schema": SCHEMA},)

    lead_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{SCHEMA}.leads.id", ondelete="CASCADE"),
        primary_key=True,
    )
    vault_name: Mapped[str] = mapped_column(String(120), nullable=False)
    indexed_event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
