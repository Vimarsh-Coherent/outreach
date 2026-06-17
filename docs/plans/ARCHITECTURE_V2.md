# Architecture v2 — AI Campaign Layer

Date: 2026-06-12 · Companion to: `PLAN_V2_AI_CAMPAIGNS.md` (overview) and `plans/PHASE_*.md` (specs)

## 1. System overview (after v2)

```
┌──────────────────────────── FRONTEND (React 19 / Vite) ────────────────────────────┐
│ Dashboard │ Leads │ Channels │ Sequences │ NEW: NewCampaign wizard │ NEW: Offerings │
│ Timeline  │ Watchdog │ NEW: Inbox │ Enrolments (finish placeholder)                 │
└──────────────┬──────────────────────────────────────────────────────────────────────┘
               │ axios /api
┌──────────────▼──────────────────────── BACKEND (FastAPI) ───────────────────────────┐
│ ROUTES                                                                               │
│  existing: leads, sequences, enrolments, channels, timeline, dashboard,             │
│            followups, watchdog, extension                                            │
│  NEW: routes_offerings.py   CRUD + doc upload + extract                              │
│  NEW: routes_campaigns.py   generate / regenerate-step / launch / pacing             │
│  NEW: routes_inbox.py       unified reply feed                                       │
│                                                                                      │
│ SERVICES                                                                             │
│  existing: lead_upload, template_render, sentiment, followup_agent, vault,           │
│            selector_healer, send_window, jitter, cap_check, suppressions             │
│  NEW: ai_guard.py           shared quota-cooldown + budget wrapper for ALL AI calls  │
│  NEW: offering_ingest.py    PDF/DOCX/TXT parse + chunk                               │
│  NEW: offering_extract.py   Claude Sonnet → offering.extracted JSONB                 │
│  NEW: campaign_generator.py Claude Sonnet → draft Sequence+Steps (structured output) │
│  NEW: personalizer.py       Claude Haiku → step-1 emails + LinkedIn note/DM bodies   │
│  CHANGED: followup_agent.py gains offering context (extracted facts + offering vault)│
│  CHANGED: vault.py          gains offering_{id} collections                          │
│                                                                                      │
│ WORKERS (APScheduler — unchanged cadence)                                            │
│  dispatcher.tick 60s │ recovery 300s │ imap_poller 60s │ watchdog 5-tier             │
│  CHANGED: dispatcher.process_one — personalization hooks (see §4)                    │
└──────┬───────────────────────┬───────────────────────────────┬──────────────────────┘
       │ Postgres (schema      │ ChromaDB ../data/vectorvault  │ Anthropic / OpenAI
       │ outreach)             │ lead_{id} + NEW offering_{id} │ (all via ai_guard)
       ▼                       ▼                               ▼
┌──────────────────────── CHANNELS ────────────────────────┐
│ email: aiosmtplib send / aioimaplib reply+bounce poll    │
│ linkedin: li_commands queue ←→ Chrome extension (MV3)    │
└──────────────────────────────────────────────────────────┘
```

## 2. New / changed data model

```
offerings
  id            PK
  user_id       FK users.id
  name          varchar(200)
  description   text                -- the user's free-text pitch
  tone          varchar(40)         -- 'professional' | 'friendly' | 'direct' | 'casual'
  status        varchar(20)         -- 'draft' | 'ready' | 'archived'
  extracted     jsonb               -- see §3 contract
  extraction_model varchar(60)
  extracted_at  timestamptz
  created_at / updated_at

offering_documents
  id            PK
  offering_id   FK offerings.id ON DELETE CASCADE
  filename      varchar(300)
  stored_path   varchar(500)        -- data/uploads/{user_id}/offerings/{uuid}.{ext}
  mime          varchar(100)
  size_bytes    int
  parsed_chars  int
  status        varchar(20)         -- 'parsed' | 'failed'
  error         varchar(500) NULL
  created_at

sequences
  + offering_id FK offerings.id NULL   -- which offering generated this sequence
  + generated_by varchar(20) NULL      -- 'ai' | 'manual'

step_runs
  + sent_subject text NULL   -- final subject actually sent (post-personalization)
  + sent_body    text NULL   -- final body actually sent — audit + timeline + vault
```

`steps.config` (existing JSONB) gains generator-written keys:
`{"ai_personalize": true, "generation_notes": "...", "charter": "intro|value|breakup|connect|dm"}`

ChromaDB: new collection family `offering_{offering_id}` holding chunked document
text (chunk ≈1500 chars, 200 overlap). Same embedding fn as lead vaults.

## 3. `offering.extracted` JSONB contract (single source of truth for all AI copy)

```json
{
  "product_name": "…",
  "one_liner": "…",
  "value_props": ["…", "…"],          // 3-6
  "pain_points": ["…"],               // pains the product solves
  "icp": {"titles": ["…"], "industries": ["…"], "company_size": "…"},
  "proof_points": ["…"],              // ONLY facts present in source material
  "cta": "…",                         // e.g. "15-min intro call"
  "suggested_tone": "professional",
  "differentiators": ["…"]
}
```
Rule enforced in every downstream prompt: copy may ONLY use facts from this
object + the lead snapshot. No invented metrics, customers, or claims.

## 4. Dispatcher send-path after v2 (email branch)

```
process_one(run)
  ├─ suppression / channel / cap checks            (unchanged)
  ├─ render template (template_render.render)      (unchanged)
  ├─ AI copy decision:
  │    step ≥ 2 AND seq.ai_followups_enabled  → followup_agent.draft_for_lead(...,
  │                                              offering=offering)   [exists, gains offering]
  │    step == 1 AND step.config.ai_personalize → personalizer.personalize_email(...)  [NEW]
  │    any AI error / ai_guard cooldown / >10s   → fall back to rendered template
  ├─ send_email(...)                               (unchanged)
  └─ finalise: store sent_subject/sent_body on step_run; Event 'delivered'  [small change]

LinkedIn branch (mint time):
  step.config.ai_personalize → personalizer.personalize_linkedin(kind, body, snapshot, offering)
    connect note hard-capped 280 chars / DM capped 1200 chars; failure → rendered template
    result goes into li_command.body_text (extension is untouched)
```

Invariant: **sends never block on AI**. Every AI call goes through
`ai_guard.guarded_call()` — quota error → 6h cooldown, transient → 15m,
plus a per-day call budget (`ai_daily_call_budget`, default 500). During
cooldown the guard raises immediately and callers fall back to templates.

## 5. Campaign creation flow (the new product loop)

```
1. POST /api/offerings                {name, description, tone}
2. POST /api/offerings/{id}/documents (multipart, optional, n files)
       → parse → chunk → embed into offering_{id} → extract → offering.extracted
3. POST /api/campaigns/generate       {offering_id, options}
       → campaign_generator: offering.extracted + connected channel types
         + sample of lead titles/companies → Claude Sonnet (forced tool output)
       → validated → INSERT Sequence(status='draft', offering_id, generated_by='ai')
         + SequenceSteps (config.ai_personalize=true on email+dm steps)
4. (UI review loop) PATCH steps / POST /api/campaigns/steps/{id}/regenerate
5. POST /api/campaigns/launch         {sequence_id, lead_ids | upload_token}
       → validate channels for every step channel type (reuse activation validation)
       → enrol leads (existing enrol logic, factored into a service)
       → sequence.status='active'
       → returns {enrolled, deduped, skipped, pacing: {…}}
6. dispatcher picks up next tick — running.
```

## 6. AI call inventory after v2

| Call | Model | Trigger | Volume | Fallback |
|---|---|---|---|---|
| offering extraction | sonnet | offering create/update, doc upload | rare (manual) | error surfaced to UI; offering stays 'draft' |
| campaign generation | sonnet | wizard generate / regenerate | rare (manual) | error surfaced to UI |
| step-1 email personalization | haiku | per send | high | rendered template |
| follow-up auto-draft (exists) | sonnet | per send, step ≥2 | medium | rendered template |
| LinkedIn note/DM personalization | haiku | per command mint | low (caps 20/25) | rendered template |
| reply sentiment (exists) | haiku | per inbound reply | low | unclassified |
| selector healer (exists) | haiku | extension DOM failure | rare | direct selectors |

All routed through `ai_guard`. Embeddings stay OpenAI text-embedding-3-small (vault.py pattern).

## 7. Failure-mode matrix (new paths)

| Failure | Behaviour |
|---|---|
| Anthropic quota/outage | ai_guard cooldown; sends continue with plain templates; wizard generate shows actionable error |
| OpenAI (embeddings) down | offering doc upload still parses + extracts (extraction uses raw text, not RAG); vault snippets just absent |
| Doc parse failure (scanned PDF etc.) | offering_documents.status='failed' + error; extraction proceeds from description alone |
| Generator returns invalid plan | code-level validation rejects → one retry with error feedback → then 422 to UI |
| Launch with missing channel for a step type | 409 with the exact step list; nothing enrolled (atomic) |
| Personalized connect note >280 chars | truncate at sentence boundary; if still over, use template |

## 8. Explicit non-goals of v2
Multi-tenant auth, mailbox rotation/warmup ramp, open/click tracking, A/B variants,
SMS/WhatsApp, visual drag-drop builder. (Backlog: PHASE_6.)

## 9. Operational cautions (carried from incidents)
- Never run `scripts/smoke_*.py` against the live backend (DB wipe incident 2026-06-12);
  destructive smokes require `OUTREACH_DESTRUCTIVE_SMOKE_OK=1`.
- JSONB columns: always assign a FRESH dict (`x.extracted = {**old, ...}`).
- Extension untouched by v2 except being a consumer of better `body_text`.
- LinkedIn per-type caps (connect 20 / dm 25 per rolling 24h) bound pacing math.
- DB credentials via `outreach.config.get_settings().database_url`, never hardcoded.
