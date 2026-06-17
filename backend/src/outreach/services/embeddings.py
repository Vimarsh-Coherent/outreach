"""OpenAI text embeddings for Qdrant vector storage."""
from __future__ import annotations

import logging
from functools import lru_cache

from openai import OpenAI

from outreach.config import get_settings

log = logging.getLogger("outreach.embeddings")


@lru_cache(maxsize=1)
def _client() -> OpenAI | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    try:
        return OpenAI(api_key=settings.openai_api_key)
    except Exception as e:  # noqa: BLE001
        log.warning("OpenAI client init failed: %s", e)
        return None


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Return embedding vectors for `texts`, or None if unavailable."""
    if not texts:
        return []
    client = _client()
    if client is None:
        return None
    settings = get_settings()
    try:
        resp = client.embeddings.create(
            model=settings.embedding_model,
            input=texts,
        )
        return [row.embedding for row in resp.data]
    except Exception as e:  # noqa: BLE001
        log.warning("embedding create failed: %s", e)
        return None


def embed_query(text: str) -> list[float] | None:
    vecs = embed_texts([text])
    if vecs is None or not vecs:
        return None
    return vecs[0]
