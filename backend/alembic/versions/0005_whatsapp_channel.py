"""whatsapp channel type

Promotes 'whatsapp' from a manual-task step to a first-class automated channel:
allow channel_type='whatsapp' on the channels table. (Step + event constraints
already include whatsapp / are unconstrained, per 0002.)

Revision ID: 0005
Revises: 0004
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.drop_constraint("ck_channels_type", "channels", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_channels_type",
        "channels",
        "channel_type IN ('email','linkedin','whatsapp')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("ck_channels_type", "channels", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_channels_type",
        "channels",
        "channel_type IN ('email','linkedin')",
        schema=SCHEMA,
    )
