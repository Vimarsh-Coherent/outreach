"""native CRM integrations (HubSpot OAuth)

Revision ID: 0010
Revises: 0009
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.create_table(
        "crm_integrations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="connected"),
        sa.Column("portal_name", sa.String(200)),
        sa.Column("config_encrypted", sa.LargeBinary, nullable=False),
        sa.Column("field_mapping", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_event_id", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("user_id", "provider", name="uq_crm_user_provider"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("crm_integrations", schema=SCHEMA)
