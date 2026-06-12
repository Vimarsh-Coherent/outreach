# Phase 1 — Offering Foundation (backend)

Goal: a persistent "what am I pitching" object — free-text description + uploaded
documents → AI-extracted structured facts → embedded into ChromaDB for downstream RAG.

Depends on: nothing. Blocks: Phases 2–4.

## 1. Deliverables
1. Alembic migration: `offerings`, `offering_documents`, `sequences.offering_id`,
   `sequences.generated_by` (DDL in ARCHITECTURE_V2.md §2).
2. Models: `models/offering.py` (`Offering`, `OfferingDocument`), matching existing
   model conventions (schema `outreach`, `TimestampMixin`-style created/updated cols
   as used by `models/sequence.py`).
3. `services/ai_guard.py` — shared AI-call wrapper (used by every later phase).
4. `services/offering_ingest.py` — file parse + chunk.
5. `services/offering_extract.py` — Claude Sonnet structured extraction.
6. `services/vault.py` additions — offering collections.
7. `api/routes_offerings.py` + `schemas/offerings.py`.
8. Dependencies: `pypdf`, `python-docx` added to backend `pyproject.toml`/requirements.
9. Tests (see §8).

## 2. ai_guard.py (shared infra — build first)

Generalizes the cooldown logic currently embedded in `selector_healer.py`.

```python
class AIGuard:                       # module-level singleton, like get_settings()
    async def guarded_call(self, *, kind: str, fn: Callable[[], Awaitable[T]]) -> T
    # kind: 'extract' | 'generate' | 'personalize' | 'followup' | 'sentiment' | 'heal'
```
Behaviour:
- In-memory state per kind: `cooldown_until`, `calls_today`, `day_anchor`.
- Anthropic error classification: quota/credit (`overloaded_error`, 429 with
  billing hint, `credit` in message) → cooldown 6h; transient (5xx, timeout,
  connection) → cooldown 15m; other errors propagate without cooldown.
- Daily budget: `settings.ai_daily_call_budget` (new config field, default 500,
  counts personalize+followup only — manual kinds exempt). Over budget →
  raise `AIBudgetExceeded`.
- During cooldown → raise `AICooldownActive(kind, until)` immediately (no API hit).
- Callers decide the fallback (template / 503 to UI). The guard never swallows results.
- Watchdog `deep_verify` tier surfaces guard state (add to its existing AI health block).

NOT in scope: refactoring sentiment.py/selector_healer.py onto the guard (do
opportunistically in Phase 3 if trivial, else leave).

## 3. offering_ingest.py

```python
MAX_DOC_BYTES = 20 * 1024 * 1024
ALLOWED = {".pdf", ".docx", ".txt", ".md"}

def parse_document(path: Path, mime: str) -> str        # raises DocParseError
def chunk_text(text: str, max_chars=1500, overlap=200) -> list[str]
async def stash_offering_doc(user_id: int, offering_id: int, upload: UploadFile) -> OfferingDocument
```
- PDF via `pypdf.PdfReader` (text extraction only — scanned/image PDFs yield ~empty
  text → if `len(text.strip()) < 100` mark status='failed', error='no extractable text
  (scanned PDF?)'). DOCX via `python-docx` (paragraphs + tables). TXT/MD read as utf-8
  with `errors="replace"`.
- Storage mirrors `lead_upload.stash_upload` conventions:
  `Path(settings.vault_dir).parent / "uploads" / str(user_id) / "offerings" / f"{uuid4()}{ext}"`.
- Chunking: split on paragraph boundaries, pack to ≤1500 chars, 200-char overlap.

## 4. vault.py additions

```python
def _offering_collection_name(offering_id: int) -> str   # f"offering_{offering_id}"
async def index_offering(offering_id: int, chunks: list[str], doc_id: int) -> dict
    # ids = f"doc-{doc_id}-chunk-{i}"; upsert; same defensive try/except style
async def get_offering_context(offering_id: int, query: str, n: int = 4) -> list[str]
def drop_offering_collection(offering_id: int) -> None   # on offering delete
```
Re-upload of a doc deletes its old chunk ids first (`collection.delete(where doc id prefix)`
— Chroma supports delete(ids=...); track chunk count on offering_documents.parsed_chars).

## 5. offering_extract.py

```python
DEFAULT_MODEL = "claude-sonnet-4-6"
async def extract_offering(offering: Offering, doc_texts: list[str]) -> dict
```
- Input assembly: description + concatenated doc texts, **truncated to ~60k chars**
  (head of each doc proportionally) — extraction needs the gist, not every page.
- Structured output via **forced tool use** (not regex-JSON like followup_agent):
  one tool `record_offering_facts` whose input schema IS the §3 contract from
  ARCHITECTURE_V2.md (`tool_choice={"type": "tool", "name": ...}`). This removes
  the `_parse_json` fragility class.
- System prompt rules: extract only what the material supports; `proof_points`
  must be verbatim-supported claims; empty arrays over invention; infer `icp`
  conservatively.
- Wrapped in `ai_guard.guarded_call(kind='extract', ...)`.
- Caller stores: `offering.extracted = result` (fresh dict!), `extraction_model`,
  `extracted_at`, and flips status 'draft'→'ready'.

## 6. API — routes_offerings.py

| Method/Path | Req → Resp |
|---|---|
| GET /api/offerings | → `[{id,name,tone,status,extracted_at,doc_count,one_liner}]` |
| POST /api/offerings | `{name, description, tone?}` → full offering. Side effect: fire extraction if description ≥ 80 chars (await it — wizard wants the result; typical <15s) |
| GET /api/offerings/{id} | → full offering incl. `extracted`, `documents[]` |
| PATCH /api/offerings/{id} | `{name?, description?, tone?}` → full offering. Description change ⇒ re-extract |
| DELETE /api/offerings/{id} | 409 if any non-archived sequence references it; else delete + drop collection + unlink files |
| POST /api/offerings/{id}/documents | multipart file → `{document, extracted}` — parse, chunk, index, **re-extract** with all docs |
| DELETE /api/offerings/{id}/documents/{doc_id} | remove file + chunks, re-extract |
| POST /api/offerings/{id}/extract | force re-extract (retry button) → `{extracted}` |

Errors: 422 unsupported file type/too large; 503 with `{cooldown_until}` when
ai_guard is in cooldown (UI shows "AI temporarily unavailable, retry at …");
extraction failure leaves offering at status='draft' with `extracted=null`.

All routes scoped by `user_id` via existing `get_current_user` dep, matching
`routes_sequences.py` patterns.

## 7. Config additions (config.py + .env.example)
```
ai_daily_call_budget: int = 500
offering_extract_model: str = "claude-sonnet-4-6"
```

## 8. Tests (backend/tests/)
- `test_offering_ingest.py`: chunker properties (overlap, max len, no data loss);
  txt/md parse; corrupt-pdf → DocParseError; tiny-text pdf → failed status.
- `test_offering_extract.py`: tool-output happy path with mocked Anthropic client;
  cooldown raise path; fresh-dict persistence of `extracted` (regression for the
  JSONB trap).
- `test_routes_offerings.py`: CRUD; delete-blocked-by-sequence 409; doc upload
  round-trip with a small fixture .docx and .pdf.
- ai_guard unit tests: quota→6h, transient→15m, budget exhaustion, cooldown raise.
- NO live-API tests; mock AsyncAnthropic like existing sentiment tests do.

## 9. Acceptance criteria
1. `curl -X POST /api/offerings` with a 3-paragraph description returns an offering
   whose `extracted.value_props` is non-empty within one request.
2. Uploading a product PDF adds an `offering_{id}` Chroma collection with >0 chunks
   and refreshes `extracted`.
3. Anthropic key removed → POST /offerings still creates the row (status stays
   'draft', extracted null, response includes a warning field) — platform never 500s
   on AI absence.
4. All existing tests still pass; migration up+down clean.

## 10. Estimated effort
~1 working session. ai_guard + ingest are mechanical; extraction prompt needs a
few iterations against the user's real pitch doc.
