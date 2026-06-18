"""sequence rag sources (sequence -> document link)

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.create_table(
        "sequence_rag_sources",
        sa.Column(
            "sequence_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.sequences.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.sequence_rag_documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sequence_rag_sources_document",
        "sequence_rag_sources",
        ["document_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_sequence_rag_sources_document", table_name="sequence_rag_sources", schema=SCHEMA)
    op.drop_table("sequence_rag_sources", schema=SCHEMA)
