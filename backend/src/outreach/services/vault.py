"""Per-lead semantic vault — Qdrant-backed local RAG store.

Each lead gets a Qdrant collection `lead_{lead_id}` with formatted snippets
of sent/received events. AI knowledge bases use `kb_{user_id}_{knowledge_id}`.

Embeddings: OpenAI text-embedding-3-small (1536-dim, cosine distance).
Storage: local Qdrant path (default) or remote Qdrant server via QDRANT_URL.

Defensive design: Qdrant and OpenAI errors must NOT take down the platform.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from outreach.config import get_settings
from outreach.models.event import Event, ReplySentiment
from outreach.models.step import SequenceStep
from outreach.models.step_run import StepRun
from outreach.models.vault_index import VaultIndex
from outreach.services import embeddings

log = logging.getLogger("outreach.vault")


def _collection_name(lead_id: int) -> str:
    return f"lead_{lead_id}"


def _kb_collection_name(user_id: int, knowledge_id: str) -> str:
    return f"kb_{user_id}_{knowledge_id}"


def _point_id(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


@lru_cache(maxsize=1)
def _qdrant_client() -> Any | None:
    settings = get_settings()
    try:
        from qdrant_client import QdrantClient  # type: ignore[import-not-found]

        if settings.qdrant_url.strip():
            kwargs: dict[str, Any] = {"url": settings.qdrant_url.strip()}
            if settings.qdrant_api_key.strip():
                kwargs["api_key"] = settings.qdrant_api_key.strip()
            return QdrantClient(**kwargs)
        return QdrantClient(path=str(settings.qdrant_path))
    except Exception as e:  # noqa: BLE001
        log.warning("qdrant client init failed: %s", e)
        return None


def _ensure_collection(client: Any, name: str) -> bool:
    settings = get_settings()
    try:
        from qdrant_client.models import Distance, VectorParams  # type: ignore[import-not-found]

        existing = {c.name for c in client.get_collections().collections}
        if name not in existing:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=settings.embedding_dim,
                    distance=Distance.COSINE,
                ),
            )
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("qdrant ensure collection %s failed: %s", name, e)
        return False


def _upsert_points(
    collection: str,
    ids: list[str],
    texts: list[str],
    metadatas: list[dict[str, Any]],
) -> int:
    client = _qdrant_client()
    if client is None or not texts:
        return 0
    if not _ensure_collection(client, collection):
        return 0
    vectors = embeddings.embed_texts(texts)
    if vectors is None or len(vectors) != len(texts):
        return 0
    try:
        from qdrant_client.models import PointStruct  # type: ignore[import-not-found]

        points = [
            PointStruct(
                id=_point_id(pid),
                vector=vec,
                payload={"text": text, **meta},
            )
            for pid, vec, text, meta in zip(ids, vectors, texts, metadatas)
        ]
        client.upsert(collection_name=collection, points=points)
        return len(points)
    except Exception as e:  # noqa: BLE001
        log.warning("qdrant upsert failed for %s: %s", collection, e)
        return 0


def _search(collection: str, query: str, n: int) -> list[str]:
    client = _qdrant_client()
    if client is None:
        return []
    if not _ensure_collection(client, collection):
        return []
    qv = embeddings.embed_query(query)
    if qv is None:
        return []
    try:
        hits = client.search(collection_name=collection, query_vector=qv, limit=min(n, 12))
        out: list[str] = []
        for hit in hits:
            payload = hit.payload or {}
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                out.append(text)
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("qdrant search failed for %s: %s", collection, e)
        return []


def index_knowledge(user_id: int, knowledge_id: str, chunks: list[dict[str, str]]) -> dict:
    """Index knowledge-base chunks into Qdrant. chunks: [{text, source, chunk_index}]."""
    if not chunks:
        return {"knowledge_id": knowledge_id, "indexed": 0, "skipped": "empty"}
    collection = _kb_collection_name(user_id, knowledge_id)
    docs = [c["text"] for c in chunks]
    ids = [f"{knowledge_id}-{i}" for i in range(len(chunks))]
    metas = [
        {"source": c.get("source", ""), "chunk_index": c.get("chunk_index", str(i))}
        for i, c in enumerate(chunks)
    ]
    indexed = _upsert_points(collection, ids, docs, metas)
    if indexed == 0:
        return {"knowledge_id": knowledge_id, "indexed": 0, "skipped": "vault_unavailable"}
    return {"knowledge_id": knowledge_id, "indexed": indexed}


def get_knowledge_similar(user_id: int, knowledge_id: str, query: str, n: int = 8) -> list[str]:
    return _search(_kb_collection_name(user_id, knowledge_id), query, n)


def probe_qdrant() -> tuple[bool, str]:
    """Health-check write + read against a sentinel collection."""
    client = _qdrant_client()
    if client is None:
        return False, "qdrant client init failed"
    if embeddings.embed_query("probe") is None:
        return False, "no embeddings (OpenAI key missing?)"
    collection = "watchdog_probe"
    indexed = _upsert_points(
        collection,
        ids=["sentinel"],
        texts=["watchdog probe"],
        metadatas=[{"kind": "probe"}],
    )
    if indexed != 1:
        return False, "upsert failed"
    hits = _search(collection, "probe", n=1)
    return (len(hits) >= 1, f"got {len(hits)} hit(s)")


def _format_sent(*, when: datetime, step_order: int, channel: str, subject: str | None, body: str) -> str:
    head = f"[{when.strftime('%Y-%m-%d %H:%M UTC')} | SENT step {step_order} ({channel})]"
    return f"{head}\nSubject: {subject or '(none)'}\nBody: {body or '(empty)'}"


def _format_event(
    *, when: datetime, kind: str, sentiment: str | None, confidence: float | None,
    from_addr: str | None, subject: str | None, body: str,
) -> str:
    sent_tag = f" | sentiment: {sentiment} {confidence:.2f}" if sentiment else ""
    head = f"[{when.strftime('%Y-%m-%d %H:%M UTC')} | {kind.upper()}{sent_tag}]"
    addr = f" (from {from_addr})" if from_addr else ""
    return f"{head}{addr}\nSubject: {subject or '(none)'}\nBody: {body or '(empty)'}"


async def index_lead(session: AsyncSession, lead_id: int) -> dict:
    """Incrementally append new events for `lead_id` to its vault."""
    idx = await session.scalar(select(VaultIndex).where(VaultIndex.lead_id == lead_id))
    watermark = int(idx.indexed_event_count) if idx else 0

    from outreach.models.enrolment import Enrolment
    enrol_ids = [e for e in (await session.execute(
        select(Enrolment.id).where(Enrolment.lead_id == lead_id)
    )).scalars().all()]
    if not enrol_ids:
        return {"lead_id": lead_id, "appended": 0, "skipped": "no_enrolments"}

    runs = (await session.execute(
        select(StepRun, SequenceStep)
        .join(SequenceStep, SequenceStep.id == StepRun.step_id)
        .where(StepRun.enrolment_id.in_(enrol_ids), StepRun.status == "sent", StepRun.id > watermark)
        .order_by(StepRun.sent_at.asc())
    )).all()
    events = (await session.execute(
        select(Event, ReplySentiment)
        .join(ReplySentiment, ReplySentiment.event_id == Event.id, isouter=True)
        .where(Event.enrolment_id.in_(enrol_ids), Event.id > watermark,
               Event.event_type.in_(("reply", "auto_reply", "bounce")))
        .order_by(Event.occurred_at.asc())
    )).all()
    if not runs and not events:
        return {"lead_id": lead_id, "appended": 0, "skipped": "no_new_items"}

    if _qdrant_client() is None:
        return {"lead_id": lead_id, "appended": 0, "skipped": "vault_unavailable"}

    docs: list[str] = []
    ids: list[str] = []
    metadatas: list[dict[str, Any]] = []
    new_max = watermark

    for run, step in runs:
        when = run.sent_at or datetime.now(timezone.utc)
        snippet = _format_sent(
            when=when, step_order=step.step_order, channel=step.channel,
            subject=step.subject, body=step.body,
        )
        docs.append(snippet)
        ids.append(f"run-{run.id}")
        metadatas.append({"kind": "sent", "step_order": step.step_order, "ts": when.isoformat()})
        if run.id > new_max:
            new_max = run.id

    for event, sent in events:
        payload = event.payload or {}
        sentiment_label = getattr(sent, "label", None)
        sentiment_conf = getattr(sent, "confidence", None)
        snippet = _format_event(
            when=event.occurred_at, kind=event.event_type,
            sentiment=sentiment_label, confidence=sentiment_conf,
            from_addr=payload.get("from"), subject=payload.get("subject"),
            body=str(payload.get("snippet") or ""),
        )
        docs.append(snippet)
        ids.append(f"event-{event.id}")
        meta: dict[str, Any] = {"kind": event.event_type, "ts": event.occurred_at.isoformat()}
        if sentiment_label:
            meta["sentiment"] = sentiment_label
        metadatas.append(meta)
        if event.id > new_max:
            new_max = event.id

    appended = _upsert_points(_collection_name(lead_id), ids, docs, metadatas)

    stmt = insert(VaultIndex).values(
        lead_id=lead_id, vault_name=_collection_name(lead_id),
        indexed_event_count=new_max, last_indexed_at=datetime.now(timezone.utc),
    ).on_conflict_do_update(
        index_elements=[VaultIndex.lead_id],
        set_=dict(
            indexed_event_count=new_max,
            last_indexed_at=datetime.now(timezone.utc),
        ),
    )
    await session.execute(stmt)
    await session.commit()
    return {"lead_id": lead_id, "appended": appended, "watermark": new_max}


async def get_similar(lead_id: int, query: str, n: int = 4) -> list[str]:
    """Return up to `n` snippets similar to query for this lead."""
    return _search(_collection_name(lead_id), query, n)
