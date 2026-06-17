# PLAN v2 — AI-Driven Campaign Creation ("Describe → Generate → Launch")

Date: 2026-06-12
Status: proposal / not started
Builds on: docs/PLAN.md (v1, shipped), docs/SYSTEM_APPROACH.md
Detailed specs: docs/plans/ARCHITECTURE_V2.md + docs/plans/PHASE_1..5 (per-phase
schemas, API contracts, prompts, tests, acceptance criteria)

## 0. Vision

The user's only jobs are:
1. Connect channels (SMTP/IMAP form + LinkedIn extension token) — **already exists**
2. Upload a lead list (CSV/XLSX) — **already exists**
3. Describe the product/service they want to pitch (free text and/or upload a doc — PDF/DOCX/PPTX/TXT)

Everything after that is automatic: AI extracts the offering's value props and ICP,
generates a multi-step multichannel sequence (emails + LinkedIn connect note + LinkedIn DMs),
shows a review screen, and on "Launch" enrols the whole list and starts running through
the existing dispatcher. Per-lead AI personalization happens at send time.

## 1. What already exists (do NOT rebuild)

| Capability | Where |
|---|---|
| Lead upload CSV/XLSX w/ auto column mapping, dedupe | `backend/src/outreach/services/lead_upload.py`, `frontend/src/pages/Leads.tsx` |
| Email channel: SMTP send, IMAP reply polling, bounce detection, 13 presets, test button | `channels/email_channel.py`, `workers/imap_poller.py`, `pages/Channels.tsx` |
| LinkedIn channel: extension command queue (connect, DM, profile visit), acceptance gate, warm-up engine, selector self-heal | `api/routes_extension.py`, `extension/background.js`, `extension/content_linkedin.js` |
| Sequences engine: steps (email / linkedin_connect / linkedin_dm), delays, send windows, timezone, days mask, jitter | `models/sequence.py`, `models/step.py`, `workers/dispatcher.py` |
| Enrolment lifecycle: active/paused/done/stopped_reply/stopped_bounce, stop-on-reply | `models/enrolment.py`, `services/reply_processor.py` |
| Daily caps: email channel cap, LinkedIn per-type rolling 24h caps (connect 20 / dm 25) | `dispatcher.process_one` |
| Reply sentiment classification (Claude Haiku, 7 labels) + auto-suppress on unsubscribe | `services/sentiment.py` |
| AI follow-up drafting (Claude Sonnet + per-lead ChromaDB vault RAG) | `services/followup_agent.py`, `components/FollowUpDrafter.tsx` |
| Dashboard: KPIs, sentiment timeseries, funnel, hot leads, at-risk | `pages/Dashboard.tsx`, `api/routes_dashboard.py` |
| 5-tier watchdog + circuit breaker + recovery sweeps | `workers/watchdog.py` |
| Boot agent (backend + Chrome + extension at login) | `agent/coherent_agent.py` |

## 2. Gap analysis — what does NOT exist

1. **Offering/Product model** — nowhere to store "what am I pitching". No table, no API, no UI.
2. **Pitch-document ingestion** — `lead_upload.py` only parses spreadsheets into leads. No PDF/DOCX
   parsing, no extraction of value props / pain points / ICP / proof points / CTA.
3. **AI sequence generation** — sequences are 100% hand-written step by step. No
   "generate a 5-step email+LinkedIn sequence from this offering" service.
4. **Per-lead AI personalization at send time** — dispatcher only does `{{first_name}}`-style
   token substitution (`services/template_render.py`). The AI drafter only runs *after* a reply.
5. **Campaign wizard / onboarding flow** — setup is scattered across 4 pages; no guided
   "channels → leads → offering → review → launch" flow, no single Launch action
   (enrol + activate are two separate API calls today).
6. **LinkedIn connect-note generation** — connect commands support a note, but nothing writes one.
7. Secondary (later): open/click tracking (Event types exist, never emitted), unified inbox,
   A/B variants, multi-mailbox rotation, sequence templates/cloning, email warmup ramp.

## 3. Architecture additions

```
                       ┌─────────────────────────────┐
  product text/doc ──► │ Offering (new table)        │
                       │  + offering docs (files)    │
                       │  + offering_{id} vault coll │
                       └──────────┬──────────────────┘
                                  │ extraction (Claude Sonnet)
                                  ▼
                       offering.extracted JSONB
                       {value_props, pain_points, icp,
                        proof_points, cta, tone}
                                  │
                                  ▼
                       campaign_generator.py (Claude Sonnet, structured output)
                                  │ creates draft Sequence + Steps
                                  ▼
                       Review UI ──"Launch"──► POST /api/campaigns/launch
                                               (enrol all + activate, one call)
                                  │
                                  ▼
                       dispatcher.process_one
                         └─ if step.config.ai_personalize:
                              personalizer.py (Claude Haiku)
                              rewrite body w/ lead snapshot + offering context
                              FALL BACK to rendered template on any AI error
```

## 4. Phases

### Phase 1 — Offering foundation (backend) ~1 session
- Alembic migration: `offerings` table — id, user_id, name, description (Text),
  tone (enum-ish text), status, `extracted` JSONB, timestamps.
  `offering_documents` table — id, offering_id, filename, path, mime, parsed_chars, created_at.
  Add nullable `offering_id` FK to `sequences`.
- File parsing service `services/offering_ingest.py`: PDF (pypdf), DOCX (python-docx),
  TXT/MD passthrough. 20 MB cap (reuse upload conventions from `lead_upload.py`,
  store under `data/uploads/{user_id}/offerings/`).
- Extraction service `services/offering_extract.py`: Claude Sonnet, structured output →
  value_props[], pain_points[], icp, proof_points[], cta, suggested_tone. Stored in
  `offering.extracted`. Re-runs when description/docs change.
- Vault: new collection `offering_{id}` in existing ChromaDB (`services/vault.py`) holding
  chunked doc text, so the follow-up agent AND personalizer can RAG over the pitch.
- Routes `routes_offerings.py`: CRUD + POST `/api/offerings/{id}/documents` +
  POST `/api/offerings/{id}/extract` (idempotent re-extract).
- Reuse the selector_healer quota-cooldown pattern for all new AI calls.

### Phase 2 — AI campaign generator (backend) ~1 session
- `services/campaign_generator.py`: input = offering + available channel types +
  small sample of leads (titles/companies for audience flavor) + user knobs
  (num emails, include LinkedIn yes/no, aggressiveness/spacing).
- Output (Claude Sonnet, structured/tool output, validated):
  - Default shape when both channels exist:
    1. linkedin_connect (day 0, AI-written note ≤ 280 chars)
    2. email #1 intro (day 0/1)
    3. linkedin_dm after acceptance (existing acceptance gate handles timing)
    4. email #2 value/proof (day +3)
    5. email #3 breakup (day +4)
  - Email-only and LinkedIn-only shapes when only one channel is connected.
- Guardrails in code, not the prompt: connect-note char limit, subject ≤ 80 chars,
  spam-trigger word lint, required `{{first_name}}`-token sanity, no fabricated claims
  (instructed to only use offering.extracted facts).
- Persists as a **draft** Sequence + SequenceSteps (existing tables) linked to offering_id.
- Routes `routes_campaigns.py`:
  - POST `/api/campaigns/generate` {offering_id, options} → draft sequence
  - POST `/api/campaigns/steps/{step_id}/regenerate` {instructions?} → rewrite one step
  - POST `/api/campaigns/launch` {sequence_id, lead_ids|filter|upload_token} →
    validates channels (reuse existing activation validation), enrols, sets status=active,
    returns enrolled/deduped/skipped counts. One click = running.

### Phase 3 — Send-time per-lead personalization (backend) ~1 session
- Step config flag `ai_personalize: true` (set by generator on email + dm steps).
- `services/personalizer.py` (Claude Haiku for cost): inputs = rendered template,
  lead contact_snapshot, offering.extracted (+ optional vault snippets). Output = final body
  (and subject for email). Hard rules: keep meaning, keep length similar, never invent facts.
- Dispatcher hook in `process_one` just before send; result cached on the StepRun
  (`personalized_body` column or reuse error_message-style text col) for audit/timeline.
- **Fail-open**: any AI error/timeout (>10s) → send the plain rendered template.
  Sends must never block on Anthropic availability (we've had quota outages — see memory:
  June 2026 quota exhaustion).
- Per-day AI-call budget knob in config to control spend.

### Phase 4 — Campaign wizard UI (frontend) ~1–2 sessions
- New page `pages/NewCampaign.tsx` — 4-step wizard:
  1. **Channels**: shows connected channels w/ status; inline reuse of the existing SMTP
     form + LinkedIn token mint if missing. (NOTE: Brevo SMTP creds were lost in the
     2026-06-12 incident and must be re-entered anyway.)
  2. **Leads**: upload new file (reuse existing preview/mapping components) or select existing.
  3. **Offering**: name + big description textarea + multi-file doc upload + tone picker;
     shows the AI-extracted summary (value props / ICP) for confirmation, editable.
  4. **Review**: generated sequence as a vertical timeline; each step editable inline,
     per-step "Regenerate" (with optional instruction), live preview rendered against
     2–3 real uploaded leads; send window/timezone/caps panel; **Launch** button.
- `pages/Offerings.tsx` — list/manage saved offerings (re-use across campaigns).
- API clients `api/offerings.ts`, `api/campaigns.ts`; "New Campaign" CTA on Dashboard + nav.

### Phase 5 — Lifecycle polish ~1 session
- Simple **Inbox** page: list reply events across all leads, sentiment-filtered, with the
  existing FollowUpDrafter inline (backend data already exists; this is mostly a new
  query endpoint + page).
- Campaign detail view = existing sequence stats + enrolment table (finish `Enrolments.tsx`
  placeholder).
- Launch-time safety summary: projected days-to-complete given caps, warn if list size vs
  caps means a long tail.

### Phase 6 — Later / optional backlog
- Open/click tracking pixel + redirect (Event types already modeled).
- A/B step variants; sequence template library/cloning.
- Multi-mailbox rotation + warmup ramp for email.
- LinkedIn withdraw-stale-invites command type.
- Multi-tenant auth (currently single-user by design).

## 5. Constraints & cautions (from operational memory)
- NEVER run `scripts/smoke_*.py` against the live backend (wipes DB) — guard env
  `OUTREACH_DESTRUCTIVE_SMOKE_OK=1` required.
- Extension reload only via ↻ in chrome://extensions; verify build marker via backend breadcrumb.
- LinkedIn per-type caps (connect 20/dm 25 rolling 24h) are enforced at command mint — the
  generator's pacing must assume these, and the wizard should surface them.
- SQLAlchemy JSONB: always reassign a fresh dict (`offering.extracted = {**old, ...}`).
- All new AI calls go through a quota-cooldown wrapper (pattern in `selector_healer.py`).
- DM targeting stays fail-closed; the generator/personalizer never touches targeting logic.

## 6. Suggested build order
P1 → P2 → P4 (wizard, wired to generate+launch) → P3 (personalization) → P5 → P6.
P1+P2 alone already deliver "describe product → sequence auto-created and running"
via API; P4 makes it the product experience.
