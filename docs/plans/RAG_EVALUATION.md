# RAG Grounding Evaluation (hallucination check)

Goal: measure, per generated message, **how much of it is actually backed by the
source documents** the sequence was generated from — so you can tell whether the
RAG pipeline is grounding its output or hallucinating. The score is a cosine
similarity available both as a CLI tool and as a per-step badge in the UI.

Depends on: the RAG pipeline ([../RAG_PIPELINE.md](../RAG_PIPELINE.md)). Builds on
the same embedding model and Qdrant collection, so eval scores reflect the *real*
retrieval space, not a re-implementation.

---

## 1. The problem

The generator ([`services/sequence_generator.py`](../../backend/src/outreach/services/sequence_generator.py))
is instructed to ground every email/DM in the uploaded document excerpts. But an
LLM can still drift — invent a metric, a feature, or a value prop that the
document never stated. We needed an objective, repeatable way to answer:

> *For this generated email, is the content supported by the document, or made up?*

A human can eyeball one email. They can't eyeball 30 sequences × 4 steps after
every prompt tweak. This phase automates that judgement.

---

## 2. The technique (how it works)

The core idea: **semantic similarity in the same embedding space the RAG pipeline
uses.** If a generated sentence is grounded, its embedding sits close (high cosine
similarity) to *some* chunk of the source document. If it's hallucinated, it sits
far from *every* chunk.

### 2.1 Same space as retrieval
- Documents are chunked and embedded with `all-MiniLM-L6-v2` (384-dim, L2-normalised)
  and stored in the Qdrant collection `sequence_rag_chunks`, scoped by
  `user_id` + `document_id` (see RAG_PIPELINE §3).
- The evaluator embeds the **generated text** with the *same* model, then compares
  it to the stored chunk vectors. Because vectors are normalised, **cosine
  similarity = dot product**.

### 2.2 Two complementary signals

**(a) Whole-message similarity** — the headline number.
For each step we build the message text (`subject + "\n" + body` for email, else
`body`), strip personalization tokens like `{{first_name}}` (they add noise and
never appear in docs), embed it, and take the **maximum cosine similarity to any
single document chunk**:

```
similarity = max over chunks c of  cosine(embed(message), c)
```

`max` (not mean) because grounding only requires that the message echoes *some*
part of the document — not all of it. We also report `mean_top_k` (mean of the
top-3 chunks) as a robustness cross-check.

**(b) Sentence-level support ratio** — catches localized hallucination.
A message can look on-topic overall yet contain one fabricated claim. So we split
the body into sentences and, for each, compute its own max-cosine to the chunks. A
sentence is "supported" if it clears `SENTENCE_THRESHOLD` (0.35, the same floor the
live retriever uses for `rag_min_score`):

```
supported_ratio = (# sentences with max-cosine ≥ 0.35) / (# sentences)
```

The single lowest-scoring sentence is surfaced as the **weakest line** — the most
likely hallucination — for explainability.

### 2.3 Verdict
The headline similarity is bucketed into a traffic-light verdict:

| Verdict | Cosine | Meaning |
|---------|--------|---------|
| `grounded` | ≥ 0.45 | message clearly echoes the document |
| `weak` | 0.30 – 0.45 | on-topic but loosely tied to the source |
| `possible_hallucination` | < 0.30 | not supported by any chunk — investigate |

> **Why these cut-offs?** MiniLM cosine between a chatty sales email and a factual
> doc chunk lands lower than doc-to-doc similarity (typically 0.3–0.6 when
> grounded). The thresholds are tuned for that gap and are configurable on the CLI.

### 2.4 Worked example
Sequence "Release Visibility" scored against `coherent-analytics-pitch.md` →
0.55–0.60 (`grounded`). The same emails scored against an *unrelated*
`security-compliance-brief.txt` → 0.16–0.40, with steps flagged
`possible_hallucination`. The metric cleanly separates "grounded in the right doc"
from "compared against the wrong doc / made up". That separation is the whole point.

---

## 3. The sequence → document link

A faithfulness score is only meaningful against the **exact documents the sequence
used**. Sequences didn't previously record that, so we added it.

- New table `sequence_rag_sources(sequence_id, document_id)` — a many-to-many link,
  both FKs `ON DELETE CASCADE`. Migration `0003_sequence_rag_sources.py`.
- Model: [`models/sequence_rag_source.py`](../../backend/src/outreach/models/sequence_rag_source.py).
- Populated at generation time in `sequence_generator._persist_sequence`: the
  resolved indexed document ids (the same set the retriever searched) are written
  as link rows alongside the new sequence.

Resolution precedence when scoring a sequence:
1. Explicit doc ids (CLI `--doc-ids`) → `override`
2. Stored links for that sequence → `linked`
3. No links (older sequences) → fall back to all indexed docs → `fallback-all-indexed`

---

## 4. Components

| Layer | File | Role |
|-------|------|------|
| Pure scoring | [`eval/rag_similarity.py`](../../backend/src/outreach/eval/rag_similarity.py) | cosine math, sentence split, token strip, verdict — no I/O, unit-tested |
| CLI / batch eval | same module (`python -m outreach.eval.rag_similarity`) | reads steps from Postgres, chunks from Qdrant, prints a per-email table + optional JSON |
| API service | [`services/grounding_service.py`](../../backend/src/outreach/services/grounding_service.py) | async wrapper: reuses the pure helpers, embeds via `to_thread`, returns structured scores |
| Endpoint | `GET /api/sequences/{id}/grounding` in [`api/routes_sequences.py`](../../backend/src/outreach/api/routes_sequences.py) | on-demand scoring for one sequence |
| Schema | [`schemas/grounding.py`](../../backend/src/outreach/schemas/grounding.py) | `SequenceGroundingResponse` |
| UI | [`pages/SequenceEditor.tsx`](../../frontend/src/pages/SequenceEditor.tsx) | "Check document grounding" button → source-doc chips + per-step cosine badge |
| Tests | [`tests/test_rag_similarity.py`](../../backend/tests/test_rag_similarity.py) | covers the pure scoring helpers |

The CLI and the API return identical numbers because they share the scoring
helpers — the API service is only an async/transport layer over the same functions.

---

## 5. How to use

### CLI (batch / regression)
```powershell
cd backend
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m outreach.eval.rag_similarity --all-sequences
.\.venv\Scripts\python.exe -m outreach.eval.rag_similarity --sequence-id 7
.\.venv\Scripts\python.exe -m outreach.eval.rag_similarity --sequence-id 7 --doc-ids 5 --json report.json
```
Flags: `--user-email`, `--sequence-id` / `--all-sequences`, `--doc-ids`,
`--grounded` / `--weak` / `--sentence-threshold`, `--json`.

### UI (per sequence)
`/sequences/:id` → **Check document grounding**. Shows the source documents and a
`sim 0.52 · grounded` badge on each email/DM (hover for exact cosine, best-matching
file, and % of sentences supported). Scoring is lazy — it embeds every message and
queries Qdrant, so the first run loads the embedding model.

---

## 6. Reading the numbers / limitations

- **Higher cosine = more grounded.** It is *not* a correctness or quality score —
  a well-grounded email can still be a bad email.
- Similarity is **relative to what's indexed now.** If the source doc was deleted
  or re-indexed, scores change. Linked-but-unindexed docs return a clear note.
- Thresholds are **heuristic** for MiniLM; re-tune if you swap the embedding model.
- Token-only / very short messages (e.g. a bare connection note) can score low
  simply for lack of content — read `supported_ratio` alongside `similarity`.
- This is an **extractive-grounding** check (is the text near the source?). It does
  not detect a claim that is *paraphrased correctly but factually wrong* in the
  source itself — that needs an LLM-judge layer (possible future phase).

---

## 7. Possible extensions
1. Persist each grounding run (history) to track drift across prompt/model changes.
2. CI gate: fail when a sequence's avg similarity drops below a threshold.
3. Inline "weakest sentence" highlight under each flagged email in the UI.
4. LLM-judge second pass for factual (not just semantic) verification.
