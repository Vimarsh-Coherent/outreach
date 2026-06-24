"""webhooks (outbox) + public API keys

Revision ID: 0008
Revises: 0007
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("secret", sa.String(80), nullable=False),
        sa.Column("event_types", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("description", sa.String(200)),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_webhook_endpoints_user", "webhook_endpoints", ["user_id", "active"], schema=SCHEMA)

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("endpoint_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.webhook_endpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.BigInteger),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('pending','delivered','failed')", name="ck_webhook_delivery_status"),
        schema=SCHEMA,
    )
    op.create_index("ix_webhook_deliveries_due", "webhook_deliveries", ["status", "next_attempt_at"], schema=SCHEMA)

    op.create_table(
        "webhook_cursor",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("last_event_id", sa.BigInteger, nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.execute(f"INSERT INTO {SCHEMA}.webhook_cursor (id, last_event_id) VALUES (1, 0) ON CONFLICT DO NOTHING")

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("label", sa.String(120), nullable=False, server_default=""),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_api_keys_hash", "api_keys", ["key_hash"], unique=True, schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("api_keys", schema=SCHEMA)
    op.drop_table("webhook_cursor", schema=SCHEMA)
    op.drop_table("webhook_deliveries", schema=SCHEMA)
    op.drop_table("webhook_endpoints", schema=SCHEMA)
