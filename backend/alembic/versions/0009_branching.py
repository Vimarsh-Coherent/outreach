"""conditional branching (DAG): step transitions + enrolment next_step_id

Revision ID: 0009
Revises: 0008
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.add_column(
        "sequence_steps",
        sa.Column("transitions", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        schema=SCHEMA,
    )
    op.add_column(
        "enrolments",
        sa.Column("next_step_id", sa.BigInteger, nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("enrolments", "next_step_id", schema=SCHEMA)
    op.drop_column("sequence_steps", "transitions", schema=SCHEMA)
