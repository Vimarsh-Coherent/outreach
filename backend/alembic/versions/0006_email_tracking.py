"""email open/click tracking flags on sequences

Adds per-sequence opt-in for open/click tracking. The events table already
allows event_type IN (...,'open','click',...) (see 0002), so no event change.

Revision ID: 0006
Revises: 0005
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.add_column(
        "sequences",
        sa.Column("track_opens", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "sequences",
        sa.Column("track_clicks", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("sequences", "track_clicks", schema=SCHEMA)
    op.drop_column("sequences", "track_opens", schema=SCHEMA)
