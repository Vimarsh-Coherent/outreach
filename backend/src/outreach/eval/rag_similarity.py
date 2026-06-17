r"""RAG grounding / hallucination evaluator for generated sequences.

For every email and LinkedIn message in a sequence, this measures how
semantically close the *generated text* is to the *source document chunks* it
was supposed to be grounded in. It uses the exact same embedding model
(all-MiniLM-L6-v2, cosine-normalised) and Qdrant collection the live RAG
pipeline uses, so the scores reflect real retrieval behaviour.

Two signals per message:

* ``similarity`` — cosine similarity of the whole message to its single
  best-matching document chunk. High = the message echoes something the
  document actually says. This is the headline "is it grounded?" number.
* ``supported_ratio`` — fraction of the message's sentences that each have at
  least one document chunk above ``sentence_threshold``. A confidently
  hallucinated claim shows up as an unsupported sentence even when the overall
  message looks on-topic, so this catches per-claim drift the headline misses.

Run it::

    cd backend
    .\.venv\Scripts\python.exe -m outreach.eval.rag_similarity --all-sequences
    .\.venv\Scripts\python.exe -m outreach.eval.rag_similarity --sequence-id 7 --doc-ids 5
    .\.venv\Scripts\python.exe -m outreach.eval.rag_similarity --sequence-id 7 --json report.json

The pure scoring helpers (``cosine_sims``, ``score_text``, ``verdict_for``,
``split_sentences``, ``strip_tokens``) have no DB/Qdrant dependency and are unit
tested in ``tests/test_rag_similarity.py``.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field

import numpy as np
from qdrant_client.http import models as qmodels
from sqlalchemy import create_engine, text

from outreach.config import get_settings
from outreach.models._mixins import SCHEMA
from outreach.services import embedding_service, qdrant_store

log = logging.getLogger("outreach.eval.rag_similarity")

# Verdict cut-offs on the headline whole-message similarity. MiniLM cosine
# between a chatty sales email and a factual doc chunk typically lands in
# 0.3-0.6 when grounded, so these are deliberately lower than a doc-to-doc
# threshold. ``sentence_threshold`` mirrors the live retriever's rag_min_score.
GROUNDED_THRESHOLD = 0.45
WEAK_THRESHOLD = 0.30
SENTENCE_THRESHOLD = 0.35

_TOKEN_RE = re.compile(r"\{\{[^}]+\}\}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


# --------------------------------------------------------------------------- #
# Pure scoring helpers (no I/O — unit tested)
# --------------------------------------------------------------------------- #
def strip_tokens(text_in: str) -> str:
    """Drop personalization tokens like ``{{first_name}}`` before embedding.

    They never appear in source docs, so leaving them in only adds noise that
    drags every similarity score down uniformly.
    """
    return _TOKEN_RE.sub(" ", text_in or "").strip()


def split_sentences(text_in: str, *, min_chars: int = 12) -> list[str]:
    """Split into sentence-ish units, dropping fragments shorter than min_chars."""
    cleaned = strip_tokens(text_in)
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(cleaned)]
    return [p for p in parts if len(p) >= min_chars]


def cosine_sims(vec: list[float] | np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of ``vec`` against every row of ``matrix``.

    Inputs are L2-normalised defensively, so the result is valid even if a
    caller passes un-normalised vectors. Returns an empty array when matrix is
    empty.
    """
    m = np.asarray(matrix, dtype=np.float64)
    if m.ndim != 2 or m.shape[0] == 0:
        return np.empty(0, dtype=np.float64)
    v = np.asarray(vec, dtype=np.float64)
    vn = np.linalg.norm(v)
    if vn == 0:
        return np.zeros(m.shape[0], dtype=np.float64)
    v = v / vn
    row_norms = np.linalg.norm(m, axis=1)
    row_norms[row_norms == 0] = 1.0
    m = m / row_norms[:, None]
    return m @ v


@dataclass(slots=True)
class TextScore:
    max_sim: float
    mean_top_k: float
    best_index: int  # row in the chunk matrix, or -1 if no chunks


def score_text(vec: list[float] | np.ndarray, matrix: np.ndarray, *, top_k: int = 3) -> TextScore:
    """Score one embedded text against the chunk matrix."""
    sims = cosine_sims(vec, matrix)
    if sims.size == 0:
        return TextScore(max_sim=0.0, mean_top_k=0.0, best_index=-1)
    k = min(top_k, sims.size)
    top = np.sort(sims)[-k:]
    return TextScore(
        max_sim=float(sims.max()),
        mean_top_k=float(top.mean()),
        best_index=int(sims.argmax()),
    )


def verdict_for(
    similarity: float,
    *,
    grounded: float = GROUNDED_THRESHOLD,
    weak: float = WEAK_THRESHOLD,
) -> str:
    if similarity >= grounded:
        return "grounded"
    if similarity >= weak:
        return "weak"
    return "possible_hallucination"


# --------------------------------------------------------------------------- #
# Data loading (DB + Qdrant)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Chunk:
    text: str
    document_id: int
    filename: str
    vector: list[float]


@dataclass(slots=True)
class Step:
    step_order: int
    channel: str
    subject: str | None
    body: str


@dataclass(slots=True)
class StepReport:
    step_order: int
    channel: str
    subject: str | None
    similarity: float
    mean_top_k: float
    supported_ratio: float
    verdict: str
    best_chunk: dict | None
    weakest_sentence: dict | None


@dataclass(slots=True)
class SequenceReport:
    sequence_id: int
    name: str
    doc_ids: list[int]
    chunk_count: int
    doc_source: str = "linked"  # linked | override | fallback-all-indexed
    steps: list[StepReport] = field(default_factory=list)

    @property
    def avg_similarity(self) -> float:
        return float(np.mean([s.similarity for s in self.steps])) if self.steps else 0.0

    @property
    def flagged(self) -> int:
        return sum(1 for s in self.steps if s.verdict == "possible_hallucination")


def _engine():
    return create_engine(get_settings().sync_database_url, future=True)


def resolve_user_id(conn, email: str) -> int:
    uid = conn.execute(
        text(f"SELECT id FROM {SCHEMA}.users WHERE email = :e"), {"e": email}
    ).scalar()
    if uid is None:
        raise SystemExit(f"No user with email {email!r}")
    return int(uid)


def list_sequence_ids(conn, user_id: int) -> list[int]:
    rows = conn.execute(
        text(
            f"SELECT id FROM {SCHEMA}.sequences WHERE user_id = :u ORDER BY id"
        ),
        {"u": user_id},
    ).scalars().all()
    return [int(r) for r in rows]


def load_sequence(conn, user_id: int, sequence_id: int) -> tuple[str, list[Step]]:
    name = conn.execute(
        text(
            f"SELECT name FROM {SCHEMA}.sequences WHERE id = :s AND user_id = :u"
        ),
        {"s": sequence_id, "u": user_id},
    ).scalar()
    if name is None:
        raise SystemExit(f"Sequence {sequence_id} not found for user {user_id}")
    rows = conn.execute(
        text(
            f"SELECT step_order, channel, subject, body FROM {SCHEMA}.sequence_steps "
            "WHERE sequence_id = :s ORDER BY step_order"
        ),
        {"s": sequence_id},
    ).all()
    steps = [Step(step_order=r[0], channel=r[1], subject=r[2], body=r[3]) for r in rows]
    return str(name), steps


def load_sequence_doc_ids(conn, sequence_id: int) -> list[int]:
    """Document ids recorded as the grounding sources for this sequence."""
    rows = conn.execute(
        text(
            f"SELECT document_id FROM {SCHEMA}.sequence_rag_sources "
            "WHERE sequence_id = :s ORDER BY document_id"
        ),
        {"s": sequence_id},
    ).scalars().all()
    return [int(r) for r in rows]


def resolve_doc_ids(conn, user_id: int, doc_ids: list[int] | None) -> list[int]:
    q = (
        f"SELECT id FROM {SCHEMA}.sequence_rag_documents "
        "WHERE user_id = :u AND status = 'indexed'"
    )
    params: dict = {"u": user_id}
    if doc_ids:
        q += " AND id = ANY(:ids)"
        params["ids"] = doc_ids
    rows = conn.execute(text(q), params).scalars().all()
    return [int(r) for r in rows]


def load_chunks(user_id: int, doc_ids: list[int]) -> list[Chunk]:
    """Pull every indexed chunk (text + vector) for the docs straight from Qdrant."""
    client = qdrant_store._client()
    name = get_settings().qdrant_collection
    if not client.collection_exists(name):
        return []
    must: list[qmodels.Condition] = [
        qmodels.FieldCondition(key="user_id", match=qmodels.MatchValue(value=user_id)),
    ]
    if doc_ids:
        must.append(
            qmodels.FieldCondition(key="document_id", match=qmodels.MatchAny(any=doc_ids))
        )
    flt = qmodels.Filter(must=must)
    chunks: list[Chunk] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=name,
            scroll_filter=flt,
            with_payload=True,
            with_vectors=True,
            limit=256,
            offset=offset,
        )
        for p in points:
            payload = p.payload or {}
            txt = payload.get("text")
            if not txt or p.vector is None:
                continue
            chunks.append(
                Chunk(
                    text=str(txt),
                    document_id=int(payload.get("document_id") or 0),
                    filename=str(payload.get("filename") or ""),
                    vector=list(p.vector),
                )
            )
        if offset is None:
            break
    return chunks


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def _message_text(step: Step) -> str:
    if step.channel == "email" and step.subject:
        return f"{step.subject}\n{step.body}"
    return step.body


def evaluate_steps(
    steps: list[Step],
    chunks: list[Chunk],
    *,
    grounded: float = GROUNDED_THRESHOLD,
    weak: float = WEAK_THRESHOLD,
    sentence_threshold: float = SENTENCE_THRESHOLD,
) -> list[StepReport]:
    if not chunks:
        raise SystemExit(
            "No indexed document chunks found for these doc-ids — nothing to compare against. "
            "Upload/index a document first, or pass the right --doc-ids."
        )
    chunk_matrix = np.array([c.vector for c in chunks], dtype=np.float64)

    # Batch-embed: one message vector + each message's sentences, in a single
    # model call to keep this fast on CPU.
    sentence_lists = [split_sentences(_message_text(s)) for s in steps]
    to_embed: list[str] = []
    for step, sents in zip(steps, sentence_lists, strict=True):
        to_embed.append(strip_tokens(_message_text(step)))
        to_embed.extend(sents)
    vectors = embedding_service.embed_texts(to_embed)

    reports: list[StepReport] = []
    cursor = 0
    for step, sents in zip(steps, sentence_lists, strict=True):
        msg_vec = vectors[cursor]
        cursor += 1
        sent_vecs = vectors[cursor : cursor + len(sents)]
        cursor += len(sents)

        msg_score = score_text(msg_vec, chunk_matrix)
        best_chunk = None
        if msg_score.best_index >= 0:
            bc = chunks[msg_score.best_index]
            best_chunk = {
                "document_id": bc.document_id,
                "filename": bc.filename,
                "score": round(msg_score.max_sim, 4),
                "preview": bc.text[:160].replace("\n", " "),
            }

        supported = 0
        weakest_sentence = None
        weakest_score = 2.0
        for sent, svec in zip(sents, sent_vecs, strict=True):
            s_sim = score_text(svec, chunk_matrix).max_sim
            if s_sim >= sentence_threshold:
                supported += 1
            if s_sim < weakest_score:
                weakest_score = s_sim
                weakest_sentence = {"text": sent[:200], "score": round(s_sim, 4)}
        supported_ratio = supported / len(sents) if sents else 0.0

        reports.append(
            StepReport(
                step_order=step.step_order,
                channel=step.channel,
                subject=step.subject,
                similarity=round(msg_score.max_sim, 4),
                mean_top_k=round(msg_score.mean_top_k, 4),
                supported_ratio=round(supported_ratio, 4),
                verdict=verdict_for(msg_score.max_sim, grounded=grounded, weak=weak),
                best_chunk=best_chunk,
                weakest_sentence=weakest_sentence,
            )
        )
    return reports


def evaluate_sequence(
    conn,
    user_id: int,
    sequence_id: int,
    doc_ids: list[int],
    chunks: list[Chunk],
    *,
    doc_source: str = "linked",
    **thresholds,
) -> SequenceReport:
    name, steps = load_sequence(conn, user_id, sequence_id)
    step_reports = evaluate_steps(steps, chunks, **thresholds)
    return SequenceReport(
        sequence_id=sequence_id,
        name=name,
        doc_ids=doc_ids,
        chunk_count=len(chunks),
        doc_source=doc_source,
        steps=step_reports,
    )


# --------------------------------------------------------------------------- #
# CLI / reporting
# --------------------------------------------------------------------------- #
_VERDICT_MARK = {
    "grounded": "OK  ",
    "weak": "WEAK",
    "possible_hallucination": "FAIL",
}


def print_report(report: SequenceReport) -> None:
    print()
    print("=" * 78)
    print(f"Sequence {report.sequence_id}: {report.name}")
    print(
        f"  compared against {report.chunk_count} chunk(s) "
        f"from doc id(s) {report.doc_ids}  [{report.doc_source}]"
    )
    print("-" * 78)
    print(f"  {'step':>4}  {'channel':<16} {'sim':>6} {'top3':>6} {'support':>8}  verdict")
    for s in report.steps:
        print(
            f"  {s.step_order:>4}  {s.channel:<16} "
            f"{s.similarity:>6.3f} {s.mean_top_k:>6.3f} "
            f"{s.supported_ratio*100:>7.0f}%  {_VERDICT_MARK[s.verdict]} ({s.verdict})"
        )
        if s.subject:
            print(f"         subject: {s.subject[:64]}")
        if s.best_chunk:
            print(
                f"         best match: [{s.best_chunk['filename']}] "
                f"\"{s.best_chunk['preview']}\""
            )
        if s.verdict != "grounded" and s.weakest_sentence:
            print(
                f"         weakest line ({s.weakest_sentence['score']:.3f}): "
                f"\"{s.weakest_sentence['text']}\""
            )
    print("-" * 78)
    print(
        f"  avg similarity: {report.avg_similarity:.3f}   "
        f"flagged (possible hallucination): {report.flagged}/{len(report.steps)}"
    )
    print("=" * 78)


def _parse_int_csv(value: str | None) -> list[int] | None:
    if not value:
        return None
    return [int(x) for x in value.replace(",", " ").split()]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-email", default=settings.seed_user_email, help="user whose data to evaluate")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--sequence-id", type=int, help="evaluate a single sequence")
    g.add_argument("--all-sequences", action="store_true", help="evaluate every sequence for the user")
    parser.add_argument("--doc-ids", help="comma/space separated doc ids (default: all indexed docs)")
    parser.add_argument("--grounded", type=float, default=GROUNDED_THRESHOLD)
    parser.add_argument("--weak", type=float, default=WEAK_THRESHOLD)
    parser.add_argument("--sentence-threshold", type=float, default=SENTENCE_THRESHOLD)
    parser.add_argument("--json", help="also write the full report to this JSON path")
    args = parser.parse_args(argv)

    if not qdrant_store.is_available():
        print("ERROR: Qdrant is not reachable at", settings.qdrant_url, file=sys.stderr)
        return 2

    thresholds = dict(
        grounded=args.grounded, weak=args.weak, sentence_threshold=args.sentence_threshold
    )
    requested_doc_ids = _parse_int_csv(args.doc_ids)

    engine = _engine()
    reports: list[SequenceReport] = []
    chunk_cache: dict[tuple[int, ...], list[Chunk]] = {}

    def chunks_for(user_id: int, ids: list[int]) -> list[Chunk]:
        key = tuple(sorted(ids))
        if key not in chunk_cache:
            chunk_cache[key] = load_chunks(user_id, ids)
        return chunk_cache[key]

    with engine.connect() as conn:
        user_id = resolve_user_id(conn, args.user_email)
        all_indexed = resolve_doc_ids(conn, user_id, None)

        if args.all_sequences:
            target_ids = list_sequence_ids(conn, user_id)
        elif args.sequence_id is not None:
            target_ids = [args.sequence_id]
        else:
            print("Specify --sequence-id N or --all-sequences", file=sys.stderr)
            return 2

        for sid in target_ids:
            # Per-sequence doc resolution: explicit --doc-ids overrides; else use
            # the docs recorded at generation; else fall back to all indexed docs.
            if requested_doc_ids:
                doc_ids = resolve_doc_ids(conn, user_id, requested_doc_ids)
                doc_source = "override"
            else:
                # Guard: an empty linked list must NOT fall through to
                # resolve_doc_ids (which treats [] as "no filter" = all docs).
                raw_linked = load_sequence_doc_ids(conn, sid)
                linked = resolve_doc_ids(conn, user_id, raw_linked) if raw_linked else []
                if linked:
                    doc_ids, doc_source = linked, "linked"
                else:
                    doc_ids, doc_source = all_indexed, "fallback-all-indexed"

            if not doc_ids:
                print(
                    f"\nSequence {sid}: skipped — no indexed documents to compare against.",
                    file=sys.stderr,
                )
                continue

            chunks = chunks_for(user_id, doc_ids)
            if not chunks:
                print(
                    f"\nSequence {sid}: skipped — linked docs {doc_ids} have no indexed "
                    "chunks in Qdrant (deleted or re-indexing?).",
                    file=sys.stderr,
                )
                continue

            report = evaluate_sequence(
                conn, user_id, sid, doc_ids, chunks, doc_source=doc_source, **thresholds
            )
            reports.append(report)
            print_report(report)

    if len(reports) > 1:
        overall = float(np.mean([r.avg_similarity for r in reports]))
        total_flagged = sum(r.flagged for r in reports)
        total_steps = sum(len(r.steps) for r in reports)
        print()
        print(
            f"OVERALL across {len(reports)} sequences: avg similarity {overall:.3f}, "
            f"{total_flagged}/{total_steps} messages flagged as possible hallucination"
        )

    if args.json:
        payload = [
            {
                "sequence_id": r.sequence_id,
                "name": r.name,
                "doc_ids": r.doc_ids,
                "doc_source": r.doc_source,
                "chunk_count": r.chunk_count,
                "avg_similarity": round(r.avg_similarity, 4),
                "flagged": r.flagged,
                "steps": [asdict(s) for s in r.steps],
            }
            for r in reports
        ]
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nWrote JSON report to {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
