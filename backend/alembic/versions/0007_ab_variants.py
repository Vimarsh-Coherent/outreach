"""A/B variant label on step_runs

Variants themselves live in sequence_steps.config['variants'] (JSONB, no schema
change). step_runs records WHICH variant it sent so opens/clicks/replies can be
attributed per variant for winner stats.

Revision ID: 0007
Revises: 0006
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.add_column(
        "step_runs",
        sa.Column("variant_label", sa.String(40), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_runs_step_variant", "step_runs", ["step_id", "variant_label"],
        schema=SCHEMA, postgresql_where=sa.text("variant_label IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_runs_step_variant", table_name="step_runs", schema=SCHEMA)
    op.drop_column("step_runs", "variant_label", schema=SCHEMA)
