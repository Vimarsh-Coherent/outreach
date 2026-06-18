"""Local sentence-transformer embeddings for sequence RAG."""
from __future__ import annotations

import logging
from functools import lru_cache

from outreach.config import get_settings

log = logging.getLogger("outreach.embedding")

EMBEDDING_DIM = 384


@lru_cache
def _get_model():
    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    log.info("loading embedding model %s", settings.embedding_model)
    return SentenceTransformer(settings.embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = _get_model()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
