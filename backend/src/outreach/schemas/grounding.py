"""Schemas for per-sequence RAG grounding (cosine similarity of each generated
message against the documents the sequence was generated from)."""
from __future__ import annotations

from pydantic import BaseModel


class GroundingDocument(BaseModel):
    id: int
    filename: str
    status: str
    indexed: bool


class StepGrounding(BaseModel):
    step_id: int
    step_order: int
    channel: str
    subject: str | None
    similarity: float          # cosine vs the single best-matching doc chunk
    mean_top_k: float          # mean cosine over the top-k chunks
    supported_ratio: float     # fraction of sentences individually supported
    verdict: str               # grounded | weak | possible_hallucination
    best_chunk_filename: str | None


class SequenceGroundingResponse(BaseModel):
    sequence_id: int
    documents: list[GroundingDocument]
    has_indexed_docs: bool
    chunk_count: int
    avg_similarity: float
    steps: list[StepGrounding]
    note: str | None = None
