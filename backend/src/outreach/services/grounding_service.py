"""Compute per-step RAG grounding (cosine similarity) for a sequence.

Scores each generated email/DM against the document(s) the sequence was
generated from (recorded in ``sequence_rag_sources``). Reuses the same pure
scoring helpers as the offline evaluator so the browser numbers match the CLI.
"""
from __future__ import annotations

import asyncio

import numpy as np
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.eval.rag_similarity import (
    load_chunks,
    score_text,
    split_sentences,
    strip_tokens,
    verdict_for,
)
from outreach.models.sequence import Sequence
from outreach.models.sequence_rag_document import SequenceRagDocument
from outreach.models.sequence_rag_source import SequenceRagSource
from outreach.models.step import SequenceStep
from outreach.schemas.grounding import (
    GroundingDocument,
    SequenceGroundingResponse,
    StepGrounding,
)
from outreach.services import embedding_service

SENTENCE_THRESHOLD = 0.35


def _message_text(step: SequenceStep) -> str:
    if step.channel == "email" and step.subject:
        return f"{step.subject}\n{step.body}"
    return step.body


async def compute_sequence_grounding(
    session: AsyncSession, user_id: int, sequence_id: int
) -> SequenceGroundingResponse:
    seq = await session.scalar(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.user_id == user_id)
    )
    if seq is None:
        raise HTTPException(404, "sequence not found")

    steps = (await session.execute(
        select(SequenceStep)
        .where(SequenceStep.sequence_id == sequence_id)
        .order_by(SequenceStep.step_order.asc())
    )).scalars().all()

    doc_rows = (await session.execute(
        select(
            SequenceRagDocument.id,
            SequenceRagDocument.filename,
            SequenceRagDocument.status,
        )
        .join(SequenceRagSource, SequenceRagSource.document_id == SequenceRagDocument.id)
        .where(SequenceRagSource.sequence_id == sequence_id)
        .order_by(SequenceRagDocument.id)
    )).all()

    documents = [
        GroundingDocument(
            id=r.id, filename=r.filename, status=r.status, indexed=(r.status == "indexed")
        )
        for r in doc_rows
    ]
    indexed_ids = [r.id for r in doc_rows if r.status == "indexed"]

    def empty(note: str) -> SequenceGroundingResponse:
        return SequenceGroundingResponse(
            sequence_id=sequence_id,
            documents=documents,
            has_indexed_docs=bool(indexed_ids),
            chunk_count=0,
            avg_similarity=0.0,
            steps=[],
            note=note,
        )

    if not documents:
        return empty(
            "No source documents are linked to this sequence — it was created "
            "manually or generated without documents, so there's nothing to score against."
        )
    if not indexed_ids:
        return empty("Linked documents are not indexed (still processing or failed).")

    chunks = await asyncio.to_thread(load_chunks, user_id, indexed_ids)
    if not chunks:
        return empty(
            "Linked documents have no chunks in the vector store (deleted or re-indexing)."
        )
    if not steps:
        return SequenceGroundingResponse(
            sequence_id=sequence_id,
            documents=documents,
            has_indexed_docs=True,
            chunk_count=len(chunks),
            avg_similarity=0.0,
            steps=[],
            note="Sequence has no steps yet.",
        )

    chunk_matrix = np.array([c.vector for c in chunks], dtype=np.float64)

    # One batched embed call: each message vector + its sentence vectors.
    sentence_lists = [split_sentences(_message_text(s)) for s in steps]
    to_embed: list[str] = []
    for step, sents in zip(steps, sentence_lists, strict=True):
        to_embed.append(strip_tokens(_message_text(step)))
        to_embed.extend(sents)
    vectors = await asyncio.to_thread(embedding_service.embed_texts, to_embed)

    step_reports: list[StepGrounding] = []
    cursor = 0
    for step, sents in zip(steps, sentence_lists, strict=True):
        msg_vec = vectors[cursor]
        cursor += 1
        sent_vecs = vectors[cursor : cursor + len(sents)]
        cursor += len(sents)

        msg_score = score_text(msg_vec, chunk_matrix)
        best_filename = (
            chunks[msg_score.best_index].filename if msg_score.best_index >= 0 else None
        )

        supported = 0
        for svec in sent_vecs:
            if score_text(svec, chunk_matrix).max_sim >= SENTENCE_THRESHOLD:
                supported += 1
        supported_ratio = supported / len(sents) if sents else 0.0

        step_reports.append(
            StepGrounding(
                step_id=step.id,
                step_order=step.step_order,
                channel=step.channel,
                subject=step.subject,
                similarity=round(msg_score.max_sim, 4),
                mean_top_k=round(msg_score.mean_top_k, 4),
                supported_ratio=round(supported_ratio, 4),
                verdict=verdict_for(msg_score.max_sim),
                best_chunk_filename=best_filename,
            )
        )

    avg = float(np.mean([s.similarity for s in step_reports])) if step_reports else 0.0
    return SequenceGroundingResponse(
        sequence_id=sequence_id,
        documents=documents,
        has_indexed_docs=True,
        chunk_count=len(chunks),
        avg_similarity=round(avg, 4),
        steps=step_reports,
        note=None,
    )
