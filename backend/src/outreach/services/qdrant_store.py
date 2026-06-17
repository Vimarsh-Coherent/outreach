"""Qdrant vector store for sequence RAG chunks (user-scoped via payload filters)."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from outreach.config import get_settings
from outreach.services.embedding_service import EMBEDDING_DIM

log = logging.getLogger("outreach.qdrant")

NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


@dataclass(slots=True)
class ChunkPayload:
    text: str
    chunk_index: int
    total_chunks: int
    char_start: int
    char_end: int
    filename: str
    file_type: str


@dataclass(slots=True)
class RetrievedChunk:
    text: str
    score: float
    document_id: int
    filename: str
    chunk_index: int


@lru_cache
def _client() -> QdrantClient:
    settings = get_settings()
    return QdrantClient(url=settings.qdrant_url)


def _collection_name() -> str:
    return get_settings().qdrant_collection


def _point_id(user_id: int, document_id: int, chunk_index: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{user_id}:{document_id}:{chunk_index}"))


def ensure_collection() -> None:
    client = _client()
    name = _collection_name()
    if client.collection_exists(name):
        return
    client.create_collection(
        collection_name=name,
        vectors_config=qmodels.VectorParams(size=EMBEDDING_DIM, distance=qmodels.Distance.COSINE),
    )
    log.info("created qdrant collection %s", name)


def upsert_chunks(
    user_id: int,
    document_id: int,
    chunks: list[ChunkPayload],
    vectors: list[list[float]],
) -> None:
    if len(chunks) != len(vectors):
        raise ValueError("chunks and vectors length mismatch")
    ensure_collection()
    now = datetime.now(timezone.utc).isoformat()
    points = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        points.append(
            qmodels.PointStruct(
                id=_point_id(user_id, document_id, chunk.chunk_index),
                vector=vector,
                payload={
                    "user_id": user_id,
                    "document_id": document_id,
                    "filename": chunk.filename,
                    "chunk_index": chunk.chunk_index,
                    "total_chunks": chunk.total_chunks,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "text": chunk.text,
                    "file_type": chunk.file_type,
                    "created_at": now,
                },
            )
        )
    if not points:
        return
    _client().upsert(collection_name=_collection_name(), points=points)


def delete_document_chunks(user_id: int, document_id: int) -> None:
    client = _client()
    name = _collection_name()
    if not client.collection_exists(name):
        return
    client.delete(
        collection_name=name,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(key="user_id", match=qmodels.MatchValue(value=user_id)),
                    qmodels.FieldCondition(key="document_id", match=qmodels.MatchValue(value=document_id)),
                ]
            )
        ),
    )


def search(
    user_id: int,
    query_vector: list[float],
    fetch_k: int,
    document_ids: list[int] | None = None,
) -> list[RetrievedChunk]:
    client = _client()
    name = _collection_name()
    if not client.collection_exists(name):
        return []

    must: list[qmodels.Condition] = [
        qmodels.FieldCondition(key="user_id", match=qmodels.MatchValue(value=user_id)),
    ]
    if document_ids:
        must.append(
            qmodels.FieldCondition(key="document_id", match=qmodels.MatchAny(any=document_ids))
        )

    # qdrant-client >= 1.16 removed `.search()`; `query_points()` is the
    # replacement and returns a response object with a `.points` list.
    response = client.query_points(
        collection_name=name,
        query=query_vector,
        query_filter=qmodels.Filter(must=must),
        limit=fetch_k,
        with_payload=True,
    )
    out: list[RetrievedChunk] = []
    for hit in response.points:
        payload = hit.payload or {}
        text = payload.get("text")
        if not text:
            continue
        out.append(
            RetrievedChunk(
                text=str(text),
                score=float(hit.score or 0.0),
                document_id=int(payload.get("document_id") or 0),
                filename=str(payload.get("filename") or ""),
                chunk_index=int(payload.get("chunk_index") or 0),
            )
        )
    return out


def search_texts(
    user_id: int,
    query_vector: list[float],
    top_k: int,
    document_ids: list[int] | None = None,
) -> list[str]:
    return [c.text for c in search(user_id, query_vector, top_k, document_ids)]


def is_available() -> bool:
    try:
        _client().get_collections()
        return True
    except Exception:  # noqa: BLE001
        return False
