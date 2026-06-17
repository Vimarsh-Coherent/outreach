from sqlalchemy import ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column

from outreach.db import Base
from outreach.models._mixins import SCHEMA, TimestampMixin


class SequenceRagSource(Base, TimestampMixin):
    """Link between a generated sequence and the RAG document(s) it was grounded on.

    Recorded at generation time so the grounding evaluator can compare each
    sequence against the *exact* documents it actually used, rather than every
    indexed document for the user.
    """

    __tablename__ = "sequence_rag_sources"
    __table_args__ = (
        Index("ix_sequence_rag_sources_document", "document_id"),
        {"schema": SCHEMA},
    )

    sequence_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.sequences.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(f"{SCHEMA}.sequence_rag_documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
