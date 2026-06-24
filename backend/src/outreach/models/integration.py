from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from outreach.db import Base
from outreach.models._mixins import SCHEMA, TimestampMixin


class CrmIntegration(Base, TimestampMixin):
    """A connected native CRM (e.g. HubSpot). OAuth tokens live encrypted in
    config_encrypted ({access_token, refresh_token, expires_at, hub_id})."""
    __tablename__ = "crm_integrations"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_crm_user_provider"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="connected", nullable=False)
    portal_name: Mapped[str | None] = mapped_column(String(200))
    config_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    field_mapping: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # High-water mark of the last event synced OUT to this CRM.
    last_event_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
