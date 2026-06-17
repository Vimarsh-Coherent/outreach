"""ai sequence channels + knowledge id

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.add_column(
        "sequences",
        sa.Column("ai_knowledge_id", sa.String(64), nullable=True),
        schema=SCHEMA,
    )
    op.drop_constraint("ck_steps_channel", "sequence_steps", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_steps_channel",
        "sequence_steps",
        "channel IN ('email','linkedin_dm','linkedin_connect','call','sms','whatsapp')",
        schema=SCHEMA,
    )
    op.drop_constraint("ck_events_type", "events", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_events_type",
        "events",
        "event_type IN ('reply','delivered','bounce','auto_reply','open','click',"
        "'connection_accepted','dm_delivered','error','manual_task')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("ck_events_type", "events", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_events_type",
        "events",
        "event_type IN ('reply','delivered','bounce','auto_reply','open','click',"
        "'connection_accepted','dm_delivered','error')",
        schema=SCHEMA,
    )
    op.drop_constraint("ck_steps_channel", "sequence_steps", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_steps_channel",
        "sequence_steps",
        "channel IN ('email','linkedin_dm','linkedin_connect')",
        schema=SCHEMA,
    )
    op.drop_column("sequences", "ai_knowledge_id", schema=SCHEMA)
