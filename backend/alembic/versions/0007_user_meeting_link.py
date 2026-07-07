"""user meeting_link (scheduling link)

Adds users.meeting_link — a Calendly/Cal.com/Google Calendar scheduling URL
the user pastes once, then reuses as a {{meeting_link}} merge token in any
email/LinkedIn/WhatsApp step body.

Revision ID: 0007
Revises: 0006
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("meeting_link", sa.String(length=500), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("users", "meeting_link", schema=SCHEMA)
