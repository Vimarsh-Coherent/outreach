"""sequence rag documents

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-15
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "outreach"


def upgrade() -> None:
    op.create_table(
        "sequence_rag_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False, server_default=""),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','indexed','failed')",
            name="ck_sequence_rag_documents_status",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sequence_rag_documents_user",
        "sequence_rag_documents",
        ["user_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_sequence_rag_documents_user", table_name="sequence_rag_documents", schema=SCHEMA)
    op.drop_table("sequence_rag_documents", schema=SCHEMA)
