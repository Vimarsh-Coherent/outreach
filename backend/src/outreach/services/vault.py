"""Per-lead semantic vault — ChromaDB-backed local RAG store.

Pivot note: PLAN.md §6.2 originally specified VectorVault, but VectorVault 7.x
dropped the `local=True` constructor (it's now a paid-cloud product). Switched
to ChromaDB which:
  - runs fully local (`PersistentClient(path=...)`)
  - uses OpenAI for embeddings (same cost as VectorVault)
  - has the same RAG primitives: collection.add(documents=...) / collection.query(query_texts=...)
  - is the most-used open-source vector DB so it's well-supported

For every lead we maintain a Chroma `Collection(name="lead_{lead_id}")` that
holds structured snippets of every event in this lead's history. M9's follow-up
agent will call `get_similar(query)` to retrieve context for personalized drafts.

Defensive design: ChromaDB and OpenAI errors must NOT take down the platform.
Everything wrapped in try/except.
"""
from __future__ import annotations

import logging
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

log = logging.getLogger("outreach.vault")


def _collection_name(lead_id: int) -> str:
    return f"lead_{lead_id}"


@lru_cache(maxsize=1)
def _chroma_client() -> Any | None:
    settings = get_settings()
    try:
        import chromadb  # type: ignore[import-not-found]
        return chromadb.PersistentClient(path=str(settings.vault_dir))
    except Exception as e:  # noqa: BLE001
        log.warning("chroma client init failed: %s", e)
        return None


@lru_cache(maxsize=1)
def _embedding_fn() -> Any | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    try:
        from chromadb.utils import embedding_functions  # type: ignore[import-not-found]
        return embedding_functions.OpenAIEmbeddingFunction(
            api_key=settings.openai_api_key,
            model_name="text-embedding-3-small",
        )
    except Exception as e:  # noqa: BLE001
        log.warning("OpenAI embedding fn init failed: %s", e)
        return None


def _get_or_create_collection(lead_id: int) -> Any | None:
    client = _chroma_client()
    if client is None:
        return None
    ef = _embedding_fn()
    if ef is None:
        return None
    try:
        return client.get_or_create_collection(
            name=_collection_name(lead_id),
            embedding_function=ef,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("get_or_create_collection failed for lead %d: %s", lead_id, e)
        return None


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
    """Incrementally append new events for `lead_id` to its vault.

    Watermark is `vault_index.indexed_event_count` — repurposed as
    "max source_id indexed so far" (a step_run.id for SENT items, an event.id
    for inbound items). Only items with id > watermark get appended.
    """
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

    collection = _get_or_create_collection(lead_id)
    if collection is None:
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

    appended = 0
    try:
        # ChromaDB's add() is idempotent on (id) if we use upsert; older versions
        # raise on duplicate ids. Use upsert when available, fall back to add.
        upsert = getattr(collection, "upsert", None)
        if callable(upsert):
            upsert(documents=docs, ids=ids, metadatas=metadatas)
        else:
            collection.add(documents=docs, ids=ids, metadatas=metadatas)
        appended = len(docs)
    except Exception as e:  # noqa: BLE001
        log.warning("chroma upsert failed for lead %d: %s", lead_id, e)

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
    """Read-side helper for M9. Returns up to `n` snippets similar to query."""
    collection = _get_or_create_collection(lead_id)
    if collection is None:
        return []
    try:
        result = collection.query(query_texts=[query], n_results=n)
    except Exception as e:  # noqa: BLE001
        log.warning("chroma query failed for lead %d: %s", lead_id, e)
        return []
    documents = result.get("documents") or [[]]
    return list(documents[0]) if documents else []
