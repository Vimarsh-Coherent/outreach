"""add delay_minutes to sequence_steps

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sequence_steps",
        sa.Column("delay_minutes", sa.SmallInteger(), nullable=False, server_default="0"),
        schema="outreach",
    )
    op.create_check_constraint(
        "ck_steps_delay_minutes",
        "sequence_steps",
        "delay_minutes BETWEEN 0 AND 59",
        schema="outreach",
    )


def downgrade() -> None:
    op.drop_constraint("ck_steps_delay_minutes", "sequence_steps", schema="outreach")
    op.drop_column("sequence_steps", "delay_minutes", schema="outreach")
