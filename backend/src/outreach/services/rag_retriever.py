"""Retrieve RAG context from Qdrant for sequence generation."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from outreach.config import get_settings
from outreach.services import document_ingest, embedding_service, qdrant_store
from outreach.services.qdrant_store import RetrievedChunk

log = logging.getLogger("outreach.rag_retriever")


def select_top_chunks(
    candidates: list[RetrievedChunk],
    *,
    min_score: float,
    top_k: int,
) -> list[RetrievedChunk]:
    """Filter by score, dedupe prefix overlaps, return top_k."""
    selected: list[RetrievedChunk] = []
    for chunk in sorted(candidates, key=lambda c: c.score, reverse=True):
        if chunk.score < min_score:
            continue
        norm = chunk.text.strip()
        if any(norm.startswith(s.text.strip()) or s.text.strip().startswith(norm) for s in selected):
            continue
        selected.append(chunk)
        if len(selected) >= top_k:
            break
    return selected


async def retrieve_context(
    session: AsyncSession,
    user_id: int,
    query: str,
    document_ids: list[int] | None = None,
    top_k: int | None = None,
) -> tuple[list[RetrievedChunk], list[str], bool]:
    """Return (top chunks, warnings, had_indexed_docs). Graceful fallback if Qdrant is down."""
    warnings: list[str] = []
    settings = get_settings()
    k = top_k or settings.rag_top_k
    fetch_k = settings.rag_fetch_k

    explicit_docs = bool(document_ids)
    ids = await document_ingest.resolve_document_ids(session, user_id, document_ids or None)
    if not ids:
        return [], warnings, False

    if not qdrant_store.is_available():
        warnings.append("Qdrant unavailable — generating from prompt only (no document context)")
        return [], warnings, True

    # When the user explicitly picked documents, they want the sequence grounded
    # in THOSE docs — so rank their chunks by the query but don't drop them for
    # being below the absolute floor (a strategy-only prompt scores low against
    # product text). The floor only guards the "search across all my docs" case.
    min_score = 0.0 if explicit_docs else settings.rag_min_score

    try:
        vector = await asyncio.to_thread(embedding_service.embed_query, query)
        candidates = await asyncio.to_thread(
            qdrant_store.search,
            user_id,
            vector,
            fetch_k,
            ids,
        )
        chunks = select_top_chunks(
            candidates,
            min_score=min_score,
            top_k=k,
        )
        if not chunks:
            warnings.append(
                "No relevant document chunks found for this prompt — generating from prompt only"
            )
        return chunks, warnings, True
    except Exception as e:  # noqa: BLE001
        log.warning("rag retrieval failed: %s", e)
        warnings.append(f"Document retrieval failed ({type(e).__name__}) — using prompt only")
        return [], warnings, True
