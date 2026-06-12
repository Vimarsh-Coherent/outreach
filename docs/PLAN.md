# Coherent Outreach — Standalone Platform Plan

**Status:** Draft v1 — ready to implement
**Date:** 2026-05-29
**Owner:** Vimarsh
**Companion to:** `email_platform/docs/plans/2026-05-18-multichannel-sequences-implementation-plan.md` (v3) — the eventual merge target.

This is a **separate, standalone** multi-channel outreach platform that runs entirely on your laptop. It will be merged into `coherentlead` (`email_platform/`) later. Where the upstream v3 plan made sense, I copied it. Where it didn't, I deviated — every deviation is called out.

---

## 1. What we're building

A single-user (for now) cold-outreach platform with:

1. **Lead ingestion** — CSV / Excel upload, manual add, deduplication by canonical identity (email lowercased, phone in E.164, LinkedIn URL normalized).
2. **Email outreach via user's SMTP/IMAP** — user pastes SMTP host/port/user/app-password + IMAP host/port for reply detection. No OAuth in v1.
3. **LinkedIn outreach via Chrome extension bridge** — backend queues `connect` / `dm` commands; an extension running in the user's logged-in LinkedIn tab polls and executes.
4. **Sequences** — ordered list of steps per sequence; each step is one channel (`email`, `linkedin_connect`, `linkedin_dm`). Per-sequence timezone + send window + weekday mask. Reply auto-stops the enrolment.
5. **Follow-up scheduling** — delay_days + delay_hours from previous send, jittered, snapped to next valid send window.
6. **Dashboard with sentiment analysis** — every inbound reply gets a sentiment label (`positive` / `interested` / `objection` / `negative` / `unsubscribe` / `auto_reply` / `neutral`) + a confidence score, surfaced as charts and per-lead timelines.
7. **AI-drafted follow-ups (VectorVault-powered)** — per-lead vault stores `lead profile + sent messages + received replies`. When the user clicks "draft follow-up" or auto-draft is on, the agent retrieves context from the vault + similar successful past replies and generates a draft.

**Out of scope for v1** (defer to v2 unless asked):
- WhatsApp / SMS (Twilio) — covered in upstream v3 plan, add later.
- Multi-tenant auth — single local user, simple cookie or no-auth localhost mode.
- Visual workflow builder (n8n style) — v1 is a linear step list.
- A/B variant testing.
- Unified reply inbox UI (replies surface on the per-enrolment timeline only).
- Credit-bucket / billing — not needed for local single-user platform; will be re-added when merging with coherentlead.
- Mailbox rotation / warmup — single mailbox per user in v1.

---

## 2. Tech stack (locked)

| Layer | Choice | Reason |
|---|---|---|
| Backend | Python 3.12 + FastAPI + SQLAlchemy 2.x (async) + Alembic | Native fit for VectorVault, sentiment, ML; FastAPI for clean REST. |
| Job queue | Postgres-backed (`FOR UPDATE SKIP LOCKED`) + APScheduler | No Redis dependency in v1. Matches the upstream tick-worker pattern. |
| LLM | Anthropic Claude (haiku for sentiment, sonnet for follow-up drafting) | Defaults to Claude; OpenAI key only needed for VectorVault embeddings. |
| Vector / RAG | VectorVault (`pip install vector-vault`, `local=True`) | Per-lead vault, semantic search across past replies. OpenAI key for embeddings. |
| DB | PostgreSQL 18 (already installed locally) | — |
| Frontend | Vite + React 19 + Tailwind v4 + TanStack Query + Recharts | Matches coherentlead frontend stack. |
| Extension | Manifest V3, JS only (no build step) | Polls backend at 30s interval. |
| Process manager (local dev) | Two terminals: `uvicorn` (backend) + `vite` (frontend) + APScheduler in-process | Simplicity. PM2 later if desired. |

**Why Python over Node** (despite eventual merge into TS backend2): VectorVault is Python-native, sentiment models live in Python, and the merge will go through a service boundary anyway (the sequences module can call the Python platform via HTTP, or the Python code can be ported to TS once the design is proven). Locking in Python lets us ship the AI features first-class.

---

## 3. Repo layout

```
coherent-outreach/
├── README.md
├── docs/
│   └── PLAN.md                          ← this file
├── backend/
│   ├── pyproject.toml
│   ├── .env.example
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   │       └── 0001_initial.py
│   ├── src/outreach/
│   │   ├── main.py                      ← FastAPI app + lifespan + APScheduler boot
│   │   ├── config.py                    ← Pydantic settings
│   │   ├── db.py                        ← Async engine + session
│   │   ├── deps.py                      ← FastAPI dependencies
│   │   ├── models/
│   │   │   ├── user.py
│   │   │   ├── channel.py               ← SMTP/IMAP creds + LinkedIn channels
│   │   │   ├── lead.py                  ← canonical lead identity
│   │   │   ├── sequence.py
│   │   │   ├── step.py
│   │   │   ├── enrolment.py
│   │   │   ├── step_run.py
│   │   │   ├── event.py
│   │   │   ├── li_command.py            ← LinkedIn extension command queue
│   │   │   └── suppression.py
│   │   ├── schemas/                     ← Pydantic v2 DTOs
│   │   ├── api/
│   │   │   ├── routes_health.py
│   │   │   ├── routes_leads.py          ← upload, list, dedupe
│   │   │   ├── routes_channels.py      ← SMTP test/save, LinkedIn extension token
│   │   │   ├── routes_sequences.py
│   │   │   ├── routes_enrolments.py
│   │   │   ├── routes_dashboard.py      ← sentiment charts, funnel
│   │   │   ├── routes_followups.py      ← AI draft endpoint
│   │   │   └── routes_extension.py      ← /extension/next-command, /complete, /replies
│   │   ├── services/
│   │   │   ├── identity.py              ← canonical hash
│   │   │   ├── send_window.py           ← timezone + business hours
│   │   │   ├── jitter.py
│   │   │   ├── sentiment.py             ← Claude haiku classifier
│   │   │   ├── vault.py                 ← VectorVault per-lead wrapper
│   │   │   ├── followup_agent.py        ← AI follow-up drafting
│   │   │   ├── leads_service.py
│   │   │   ├── sequences_service.py
│   │   │   ├── enrolments_service.py
│   │   │   └── recovery.py              ← orphan-run sweeper
│   │   ├── channels/
│   │   │   ├── base.py                  ← ChannelAdapter interface
│   │   │   ├── email_channel.py         ← SMTP send
│   │   │   └── linkedin_channel.py      ← enqueue li_command
│   │   ├── workers/
│   │   │   ├── tick_worker.py           ← scheduler (APScheduler)
│   │   │   ├── send_worker.py           ← claims due, sends, persists run
│   │   │   ├── imap_poller.py           ← polls IMAP, detects replies + DSN bounces
│   │   │   └── reply_processor.py       ← OOO filter, sentiment, stop enrolment
│   │   └── utils/
│   │       ├── crypto.py                ← Fernet for SMTP password at rest
│   │       └── email_parse.py           ← header decode, DSN parse
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx                      ← router shell + sidebar
│       ├── api/client.ts                ← axios + react-query
│       ├── components/
│       └── pages/
│           ├── Dashboard.tsx            ← sentiment charts + KPIs
│           ├── Leads.tsx                ← upload + table + dedupe stats
│           ├── Channels.tsx             ← SMTP setup + LinkedIn ext token
│           ├── Sequences.tsx            ← list
│           ├── SequenceEditor.tsx       ← linear step list with per-channel forms
│           ├── Enrolments.tsx
│           ├── Timeline.tsx             ← per-lead conversation + sentiment
│           └── FollowUpDrafter.tsx      ← AI draft preview & send
├── extension/
│   ├── manifest.json
│   ├── background.js                    ← poll /extension/next-command
│   ├── content_linkedin.js              ← DOM automation on linkedin.com
│   ├── popup.html / popup.js            ← paste platform URL + token
│   └── icons/
├── scripts/
│   ├── init_db.ps1                      ← creates db, role, runs migrations
│   ├── seed_demo.py                     ← test leads + sample sequence
│   └── pg_trust_localhost.ps1           ← helper for re-applying trust auth
└── data/
    ├── uploads/                         ← CSV/Excel staging (gitignored)
    └── vectorvault/                     ← VectorVault local storage (gitignored)
```

---

## 4. Data model

All tables in schema `outreach` (separate from any future `app` schema when merging). Migration file `0001_initial.py` (Alembic) creates everything.

**Naming alignment with upstream v3 plan:** where columns mean the same thing as in `app.sequences` / `app.sequence_*`, I kept the names identical. The merge can then map `outreach.sequences → app.sequences` with a SQL view or a one-shot copy.

### 4.1 Tables

#### `outreach.users`
Single-user dev table. Columns: `id`, `email`, `display_name`, `created_at`. One row seeded by `init_db`.

#### `outreach.leads` — canonical identity store
```
id BIGSERIAL PK
user_id INT NOT NULL
identity_hash BYTEA NOT NULL                 -- sha256(normalized_email|normalized_phone|normalized_li_url)
email TEXT, phone TEXT, linkedin_url TEXT
first_name TEXT, last_name TEXT
company TEXT, title TEXT
custom JSONB DEFAULT '{}'
source TEXT                                  -- 'csv'|'xlsx'|'manual'
created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
UNIQUE (user_id, identity_hash)
```
Dedupe: `INSERT ... ON CONFLICT (user_id, identity_hash) DO UPDATE SET ... RETURNING (xmax = 0) AS inserted`. Tells us inserted-vs-deduped count for the upload report.

#### `outreach.channels` — sending mailboxes + linkedin tokens
```
id SERIAL PK
user_id INT NOT NULL
channel_type VARCHAR(20) NOT NULL            -- 'email'|'linkedin'
display_label VARCHAR(120) NOT NULL
config_encrypted BYTEA NOT NULL              -- Fernet-encrypted JSON: smtp/imap creds OR linkedin ext token
status VARCHAR(20) DEFAULT 'active'          -- 'active'|'paused'|'invalid'
daily_cap INT DEFAULT 100
sent_today INT DEFAULT 0
sent_today_window_start TIMESTAMPTZ
ext_last_heartbeat_at TIMESTAMPTZ           -- linkedin only
created_at, updated_at
```

#### `outreach.sequences`
```
id SERIAL PK, user_id INT
name VARCHAR(120), description TEXT
status VARCHAR(20) DEFAULT 'draft'           -- draft|active|paused|archived
timezone VARCHAR(64) DEFAULT 'Asia/Kolkata'
send_window_start TIME DEFAULT '09:00'
send_window_end TIME DEFAULT '18:00'
send_days_mask SMALLINT DEFAULT 31           -- Mon-Fri
ai_followups_enabled BOOL DEFAULT false      -- NEW vs v3: auto-draft followup before send
created_at, updated_at
CHECK send_window_start < send_window_end
```

#### `outreach.sequence_steps`
```
id, sequence_id, step_order, channel
delay_days SMALLINT, delay_hours SMALLINT
subject TEXT          -- email only
body TEXT             -- supports {{first_name}}, {{last_name}}, {{company}}, {{title}}, {{email}}
config JSONB DEFAULT '{}'
created_at, updated_at
UNIQUE (sequence_id, step_order)
CHECK channel IN ('email','linkedin_dm','linkedin_connect')
```
Body length caps per channel (Pydantic-enforced, not just DB CHECK):
- email body: 16 000 chars, subject: 250
- linkedin_dm: 8 000
- linkedin_connect (note): 300

#### `outreach.enrolments`
```
id, sequence_id, user_id, lead_id (-> leads.id)
identity_hash BYTEA       -- denormalized for cross-enrolment lookups
contact_snapshot JSONB    -- frozen at enrolment
status VARCHAR(20) DEFAULT 'active'   -- active|paused|done|stopped_reply|stopped_bounce|stopped_manual|stopped_archived|errored
current_step_order SMALLINT DEFAULT 0
next_send_at TIMESTAMPTZ
runtime_state JSONB DEFAULT '{}'      -- selected_mailbox_id, normalised_phone, consecutive_failures, etc.
enrolled_at, stopped_at, stopped_reason TEXT, updated_at
UNIQUE INDEX (sequence_id, identity_hash) WHERE status IN ('active','paused')
INDEX (next_send_at) WHERE status='active' AND next_send_at IS NOT NULL
```

#### `outreach.step_runs`
```
id BIGSERIAL, enrolment_id, step_id, channel
status VARCHAR(20)         -- queued|reserving|sent|failed|bounced|queued_external|skipped
scheduled_at, sent_at
provider_message_id VARCHAR(200)
error_message TEXT
created_at
INDEX (enrolment_id, sent_at DESC) WHERE status='sent'   -- threading hot path
INDEX (provider_message_id) WHERE provider_message_id IS NOT NULL
```
Simplified from upstream (no credit_state because no billing in v1).

#### `outreach.events`
```
id BIGSERIAL, enrolment_id, step_run_id NULL
event_type VARCHAR(30)     -- reply|delivered|bounce|auto_reply|open|click|connection_accepted|dm_delivered|error
channel, external_id, payload JSONB, occurred_at, created_at
UNIQUE (enrolment_id, event_type, external_id) WHERE external_id IS NOT NULL
```

#### `outreach.reply_sentiment` (separate from events)
```
event_id BIGINT PK REFERENCES events(id) ON DELETE CASCADE
label VARCHAR(20) NOT NULL    -- positive|interested|objection|negative|unsubscribe|auto_reply|neutral
confidence REAL               -- 0.0-1.0
reasoning TEXT                -- model's short explanation, max 500 chars
model VARCHAR(40)             -- e.g. 'claude-haiku-4-5'
created_at
INDEX (label)
```
Reasoning for separate table: keeps `events` lean (used for cadence logic), allows re-classification later by deleting+re-inserting without touching event rows.

#### `outreach.li_commands` — LinkedIn extension queue
```
id BIGSERIAL, user_id, enrolment_id, step_id
command_type VARCHAR(20)   -- 'dm'|'connect'|'view_profile'
target_li_url TEXT, body_text TEXT
status VARCHAR(20) DEFAULT 'pending'   -- pending|claimed|done|failed|expired
claimed_at, completed_at, error_message, created_at
INDEX (user_id, created_at) WHERE status='pending'
```

#### `outreach.suppressions`
```
id, user_id, identity_hash BYTEA, channel, reason VARCHAR(40), created_at
UNIQUE (user_id, identity_hash, channel)
```
Populated on hard bounce or `unsubscribe` sentiment. Send-worker checks before every send.

#### `outreach.vault_index`
```
lead_id BIGINT PK REFERENCES leads(id) ON DELETE CASCADE
vault_name TEXT NOT NULL             -- e.g. 'lead_42'
indexed_event_count INT DEFAULT 0
last_indexed_at TIMESTAMPTZ
```
Tracks which events have been pushed into the lead's VectorVault. The `vault.py` service appends new events incrementally.

### 4.2 Migration order
- `0001_initial.py` — everything above (no incremental v3-style numbering since we ship as one).

---

## 5. Send / receive lifecycle

### 5.1 Tick worker (APScheduler, every 60s)
```
SELECT e.* FROM outreach.enrolments e
  JOIN outreach.sequences s ON s.id = e.sequence_id
 WHERE e.status='active' AND e.next_send_at <= NOW()
   AND s.status='active'
 ORDER BY e.next_send_at
 LIMIT 500
   FOR UPDATE OF e SKIP LOCKED;
```
For each claimed enrolment, push a `send` job onto an in-process asyncio queue with `jobId = f"enrol-{id}-step-{order}"` for in-memory dedup.

### 5.2 Send worker (asyncio task pool, concurrency 8)
1. Re-lock the enrolment with `FOR UPDATE`.
2. Re-check suppressions for `(identity_hash, channel)`.
3. Daily-cap bump on the channel (atomic CAS UPDATE with sliding-window rollover, copied from upstream §4.2).
4. Insert `step_run` with `status='reserving'`.
5. **If `ai_followups_enabled` on the sequence AND this is step >= 2**: call `followup_agent.draft()` to optionally rewrite `body` based on prior reply context. The rewritten body is what gets sent and recorded.
6. Provider call **outside any DB transaction**:
   - Email: `aiosmtplib` send with HMAC-tokenized Message-ID + threading headers (`In-Reply-To` / `References` filtered to `status='sent'` runs only).
   - LinkedIn: insert into `li_commands`; mark step_run `queued_external`. The extension will complete it asynchronously.
7. On result: mark run `sent` / `failed`, advance `enrolment.current_step_order`, compute next `next_send_at` via `send_window.next_slot(...)` + jitter.

### 5.3 IMAP poller (APScheduler, every 60s)
- Connect via stored creds (`channels.config_encrypted` for type `email` with role=imap).
- Pull UNSEEN messages from INBOX since last poll cursor.
- For each:
  - **DSN bounce**: parse `multipart/report; report-type=delivery-status`, extract `Original-Message-ID`, find matching `step_run`, emit `bounce` event, stop enrolment with `stopped_bounce`, insert suppression.
  - **Auto-reply / OOO**: header heuristics (`Auto-Submitted`, `X-Autoreply`, `Precedence: bulk`, `no-reply` From, subject regex). Emit `auto_reply` event, **do not stop**.
  - **Real reply**: HMAC-verify the threading token in `In-Reply-To` / `References`, find enrolment, emit `reply` event, stop enrolment with `stopped_reply`.
  - **Pre-classification trigger**: every non-OOO reply queued for sentiment (see §6).

### 5.4 LinkedIn webhook (from extension)
- `POST /extension/replies` — extension scrapes new LinkedIn messages on each poll, posts deltas. Backend matches to enrolments by `target_li_url`, emits `reply` event, classifies sentiment, stops enrolment.
- `POST /extension/commands/{id}/complete` — extension reports success/failure of a queued `dm`/`connect`. Backend marks run `sent` / `failed`, advances enrolment.

### 5.5 Recovery sweeper (every 5 min)
Reclaims `step_run` rows stuck in `reserving` >10 min and `li_commands` stuck in `claimed` > 120 min. Refunds (n/a for v1 — no credits), marks run failed, reschedules enrolment.

---

## 6. Sentiment & AI follow-ups (the differentiator)

### 6.1 Sentiment classifier (`services/sentiment.py`)
Single Anthropic `messages.create` call per inbound reply (model `claude-haiku-4-5` for cost). Prompt:

```
You are classifying a cold-outreach reply from a prospect.

INBOUND REPLY:
---
{reply_body}
---

ORIGINAL OUTREACH:
---
{last_sent_body}
---

Classify into exactly ONE of:
- positive   (interested, asking for more info, wants a meeting)
- interested (lukewarm but engaged: "send me details", "next quarter maybe")
- objection  (specific objection: price, timing, "not the right person")
- negative   (clear disinterest but not unsubscribe)
- unsubscribe (asks to stop / remove / opt out)
- neutral    (acknowledgement only, e.g., "thanks")

Return JSON: {"label": "...", "confidence": 0.0-1.0, "reasoning": "<=200 chars"}
```

Result stored in `reply_sentiment`. If label = `unsubscribe`, automatically insert a suppression row for `(identity_hash, *)` (all channels).

**Async path**: classification is non-blocking. Reply detection inserts the event + stops the enrolment immediately; sentiment runs in a background task.

### 6.2 Per-lead VectorVault (`services/vault.py`)
- One `Vault` per lead, named `lead_{lead_id}`, with `local=True`.
- `OPENAI_API_KEY` for embeddings (configured in `.env`).
- On every event (send + reply), call `vault.add_n_save()` with a structured snippet:
  ```
  [2026-05-29 10:14 — SENT — step 2 — email]
  Subject: Following up on our conversation
  Body: Hi Sarah, just wanted to check...

  [2026-05-30 14:22 — REPLY — sentiment: interested (0.78)]
  Body: Thanks - can you share pricing?
  ```
- An indexer task processes `events.id > vault_index.indexed_event_count` in batches.

### 6.3 Follow-up agent (`services/followup_agent.py`)
Triggered:
- **Auto-draft** when `sequence.ai_followups_enabled = true` and we're about to send step ≥ 2.
- **Manual** via `POST /followups/draft` from the UI's "Draft AI follow-up" button.

Pipeline:
1. Load lead profile + last 5 sent/received events from DB.
2. Open `Vault(lead_{lead_id})`.
3. Pull `vault.get_similar("...recent reply...", n=4)` for prior-context recall on that lead.
4. **Cross-lead RAG**: open `Vault("global_replies")` containing **anonymized** snippets of past replies tagged with the outcome (`booked_meeting` / `opted_out` / `no_response`). Pull `n=3` similar successful follow-up examples.
5. Send to Claude Sonnet with a structured prompt:
   ```
   You are drafting a follow-up message for a cold outreach.
   LEAD: {profile}
   PRIOR CONVERSATION: {recent events}
   SIMILAR SUCCESSFUL FOLLOW-UPS (anonymized): {global retrieval}
   USER'S TEMPLATE FOR THIS STEP: {step.body}
   TASK: rewrite the template, personalized, <=150 words, same tone.
   Return JSON: {"subject": "...", "body": "...", "notes": "<=120 chars why this works"}
   ```
6. UI shows a side-by-side diff: template vs draft, with the agent's `notes`. User can edit + approve or one-click send.

### 6.4 Dashboard
Single React page (`Dashboard.tsx`). KPIs computed by `routes_dashboard.py`:
- **Funnel** (last 30d): enrolled → contacted → opened → replied → positive replies.
- **Sentiment distribution**: stacked bar by week.
- **Top sequences by reply rate**.
- **At-risk enrolments**: errored or stuck (next_send_at < NOW() - 24h).
- **Hot leads**: enrolments with `positive` or `interested` sentiment in last 7d. CTA: "Draft follow-up".

All charts via Recharts. Data refresh on focus + every 60s.

---

## 7. Chrome extension contract

### 7.1 Auth
On extension popup, user pastes:
- Platform URL: `http://localhost:8000` (default)
- Token: a random opaque token from `Channels` page (stored in `channels.config_encrypted` for type `linkedin`).

Extension sets `X-Extension-Token: {token}` on every request.

### 7.2 Endpoints (backend)
| Method | Path | Use |
|---|---|---|
| `GET` | `/extension/heartbeat` | Updates `channels.ext_last_heartbeat_at`. Called every 30s. |
| `GET` | `/extension/next-command` | Atomically claims one `li_commands` row with `status='pending'`, sets `status='claimed', claimed_at=NOW()`. Returns `{id, command_type, target_li_url, body_text}` or `204`. |
| `POST` | `/extension/commands/{id}/complete` | Body: `{status: 'done'|'failed', provider_message_id?, error?}`. Marks run + advances enrolment. |
| `POST` | `/extension/replies` | Body: `{li_url, thread_id, message_id, body, received_at}[]`. Matches enrolment by `li_url`, emits `reply` event, queues sentiment. |

### 7.3 Daily caps (extension-side hard stop)
Manifest config: `LI_DAILY_CAP_CONNECT=20`, `LI_DAILY_CAP_DM=25`. Extension refuses to execute beyond these regardless of what backend serves. Defense in depth.

### 7.4 Scrape protocol
- `content_linkedin.js` injected on `https://www.linkedin.com/*`.
- On `command_type='dm'`: navigate to `target_li_url`, click message, type body, send. Read back the new message in the conversation list, capture its `entity_urn` → that's the `provider_message_id`.
- On `command_type='connect'`: navigate, click "Connect", attach note (≤300 chars), submit.
- New messages observed in the inbox (any thread, not just outreach) are batched and posted to `/extension/replies` every 30s.

---

## 8. Env vars (`backend/.env.example`)

```
# Database
DATABASE_URL=postgresql+asyncpg://outreach:outreach@localhost:5432/coherent_outreach

# Crypto (Fernet for SMTP-password-at-rest). Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SECRET_KEY=

# HMAC for email tokens / extension token / threading message-id
TOKEN_SECRET=

# LLM
ANTHROPIC_API_KEY=
OPENAI_API_KEY=                    # only for VectorVault embeddings

# VectorVault
VAULT_STORAGE_DIR=./data/vectorvault
VAULT_LOCAL=1

# Send / poll cadence
TICK_INTERVAL_SECONDS=60
IMAP_POLL_INTERVAL_SECONDS=60
SEND_CONCURRENCY=8
JITTER_MAX_MS=300000

# Caps
LI_DAILY_CAP_CONNECT=20
LI_DAILY_CAP_DM=25
EMAIL_DAILY_CAP=200

# Server
APP_HOST=127.0.0.1
APP_PORT=8000
APP_LOG_LEVEL=info
```

---

## 9. Milestones (revised vs v3 to fit standalone scope)

| M | Scope | Est. days |
|---|---|---|
| **M0** | Repo scaffold, Postgres, alembic init, FastAPI `/health`, frontend Vite shell | 1 |
| **M1** | Leads CRUD + CSV upload + dedupe — **done 2026-05-29**: CSV / TSV / Excel parser with auto column mapping, preview→commit flow, canonical identity (email → phone E.164 → LinkedIn slug), within-upload dedupe (merges duplicates by identity_hash before sending to PG), ON CONFLICT DO UPDATE with `COALESCE` so prior-filled fields never get blanked, search + paginated list, single + bulk delete | 1.5 |
| **M2** | Channels (SMTP test/save, encrypted store) — **done 2026-05-29**: 13 provider presets (Gmail, Outlook personal, M365, Yahoo, Zoho, Zoho EU, iCloud, Fastmail, ProtonMail Bridge, SendGrid, Mailgun, Amazon SES, Custom); SMTP/IMAP test endpoint with TLS/STARTTLS/SSL + optional probe send; Fernet-at-rest; full CRUD frontend page | 1 |
| **M3** | Sequences CRUD + step editor + send-window math + identity service — **done 2026-05-29**: sequences with timezone + business window + weekday mask, status state machine (draft/active/paused/archived) with auto-stop of enrolments on archive, steps with discriminated Pydantic per channel (email/linkedin_dm/linkedin_connect, per-channel body caps 16k/8k/300), reorder via 2-phase update to avoid UNIQUE violation, `next_valid_slot` math (6/6 worked-examples pass), `enrol_leads` with partial-unique-index dedupe by (sequence_id, identity_hash), pause/resume/stop enrolment endpoints | 2 |
| **M4** | Email send worker + tick worker + threading headers + recovery sweeper — **done 2026-05-29**: APScheduler tick (60s) + recovery sweep (5min) + asyncio send pool (concurrency 8); template renderer with `{{token}}` + `{{token|default}}` syntax; HMAC-signed Message-ID with `parse_message_id` HMAC round-trip; threading via In-Reply-To + References filtered to status='sent' runs only; atomic daily-cap CAS UPDATE with sliding 24h window; per-enrolment suppression check; LinkedIn steps shimmed to `queued_external` (M7); 5-failure → `errored` enrolment terminal state; recovery sweep reclaims `reserving` runs older than 10min. Smoke test asserts every step end-to-end against in-process aiosmtpd. | 2 |
| **M5** | IMAP poller + DSN bounce parser + reply auto-stop + OOO filter — **done 2026-05-29**: parser classifies inbound rfc822 bytes as reply / bounce / auto_reply / unrelated via HMAC Message-ID match + OOO heuristics (Auto-Submitted, X-Autoreply, Precedence: bulk, no-reply From, OOO subject regex); DSN parser walks `multipart/report` to extract Final-Recipient + Original-Message-ID; reply_processor stops enrolment (`stopped_reply` / `stopped_bounce`), adds suppression on bounce, leaves OOO enrolments active; idempotent via `events.(enrolment_id, event_type, external_id)` partial unique index — duplicate IMAP fetches collapse to `duplicate_event`; IMAP poller searches with header-targeted queries + 7-day SINCE; wired into scheduler at IMAP_POLL_INTERVAL_SECONDS. | 2 |
| **M6** | Sentiment classifier + per-lead semantic vault — **done 2026-05-29**: Claude Haiku 4.5 classifies inbound replies into the 7 PLAN.md labels with confidence + reasoning (5/5 smoke samples correct on real API); rules-engine shortcut for `auto_reply`/`bounce` skips the LLM hop; `unsubscribe` auto-suppresses the contact across ALL channels via `channel='*'`; ChromaDB persistent client (project-relative path) holds a per-lead `Collection(lead_{id})` of formatted snippets — **pivoted from VectorVault** because v7.x dropped `local=True` and went cloud-first (same RAG primitives, fully local); `get_similar()` ready for M9; sentiment + vault both fire as fire-and-forget background tasks from `reply_processor` (never blocks the cadence); `GET /api/leads/{id}/timeline` returns a chronologically merged sent + reply + bounce + auto_reply feed with sentiment labels attached. | 1.5 |
| **M7** | LinkedIn channel + Chrome extension DM/connect — **done 2026-05-29**: `POST /api/channels/linkedin` mints a one-time HMAC token (raw shown once, only hash stored at rest); 4 extension endpoints (`/heartbeat`, `/next-command` atomic claim via `FOR UPDATE SKIP LOCKED`, `/commands/{id}/complete`, `/replies`) all auth'd via `X-Extension-Channel-Id` + `X-Extension-Token`; dispatcher replaces the shim with real `li_commands` insertion, refuses to queue when `linkedin_url` missing or extension heartbeat is stale >1h; extension Manifest V3 with `background.js` polling at 30s alarm interval, `content_linkedin.js` DOM automation for DM (profile → Message → editor → Send) + Connect (Connect button → Add a note → Send invitation) + 30s inbox scan posting unseen replies to `/extension/replies`; popup captures platform URL + channel id + token. Smoke test verifies channel-create + token-leak protection + bad-token 401 + tick-queues-command + extension-polls + complete-advances + reply-stops + duplicate-dedup + offline-extension-reschedule + no-linkedin-url-skip. **Honest caveat**: LinkedIn DOM selectors will break periodically — the file is commented with "verified 2026-05-29; expect breakage on LinkedIn UI updates." | 3 |
| **M8** | Dashboard (KPIs, sentiment chart, hot leads) — **done 2026-05-29**: 5 read endpoints under `/api/dashboard/*` (summary, sentiment-timeseries day/week, sequences stats, hot-leads, at-risk); `Dashboard.tsx` with 6 KPI cards, vertical funnel (enrolled → contacted → replied → positive), stacked-bar sentiment-by-day chart, top-sequences table, hot-leads list with deep-link to `/leads/{id}/timeline`, at-risk panel; `Timeline.tsx` shows the full per-lead conversation with color-coded events + sentiment chips. `seed_demo.py` prefills 120 leads / 163 sends / 48 sentiment-classified replies / 6 bounces / 5 errored enrolments so the dashboard renders before any live outreach. | 1.5 |
| **M9** | AI follow-up drafter (manual + auto modes) — **done 2026-05-29**: `services/followup_agent.py` calls Claude Sonnet 4.6 with structured prompt grounded in (a) lead profile, (b) last 5 prior conversation events with sentiment tags, (c) ChromaDB `get_similar()` retrieval of similar past snippets for this lead; returns `{subject, body, notes}`; defensive fallback returns template unchanged on LLM failure; `POST /api/followups/draft` + `POST /api/followups/send`; dispatcher auto-draft path swaps template body with agent draft when `sequence.ai_followups_enabled` AND step >= 2; `FollowUpDrafter.tsx` modal wired into Dashboard hot-leads and Timeline with side-by-side template/draft diff, edit mode, vault-context details, send-now button. Smoke test verified real Sonnet draft mentions company name + addresses the reply's "pricing for Q3 planning" ask with a concrete 15-min CTA. | 2 |
| **M10** | Polish, seed script, end-to-end smoke test | 1 |

**Total**: ~18.5 working days. Realistic 4–5 calendar weeks for a single dev.

---

## 10. Merge path back into coherentlead

When ready:
1. Schema → run a one-shot script that copies `outreach.*` rows into `app.sequences/sequence_steps/...` (column names already match; identity_hash is portable).
2. Sentiment + VectorVault: keep as a Python sidecar called over HTTP by backend2 (`/internal/sentiment/classify`, `/internal/followup/draft`). Or port the prompts to TS using `@anthropic-ai/sdk`.
3. Chrome extension: merge with the existing `linkedin-scraper/` extension — both already use a token-based bridge, so reconcile manifest + background workers.
4. Auth: replace single-user mode with coherentlead's existing user model.
5. Credit-bucket integration: add the two-phase reserve→charge ledger from v3 §5.5.

---

## 11. Differences vs upstream v3 plan (called out)

| v3 has | This plan |
|---|---|
| Node + TypeScript backend | Python + FastAPI |
| BullMQ + Redis | Postgres queue + APScheduler |
| Credit reservation / charging | None (no billing in v1) |
| Multi-user with `team_id` | Single local user |
| WhatsApp + SMS via Twilio | Deferred to v2 |
| Mailbox stickiness / rotation pool | Single mailbox per user |
| `sequence_billing_aggregates` | Skipped |
| Webhook secrets stored as HMAC hash, raw shown once | Same approach for extension token |
| `pg_028_sequence_billing_aggregates` etc. | Single Alembic `0001_initial.py` |
| Idempotency-Key header on `/enrol` | Same (kept) |
| OOO heuristics | Same (kept verbatim) |
| HMAC threading tokens | Same (kept) |
| Circuit breaker per channel | Deferred (single mailbox + low volume in v1) |
| AI agent drafting follow-ups | **NEW** — not in v3, this plan's differentiator |
| Per-lead VectorVault + cross-lead RAG | **NEW** |
| Sentiment-tagged dashboard | **NEW** (v3 only mentions reply events) |

---

## 12. Open questions (none blocking M0–M3)

1. Auto-draft mode default: when `ai_followups_enabled=true`, do we auto-send the AI draft, or queue for user approval? **Default: queue for approval until a config flag is flipped.**
2. Should the sentiment classifier also score outbound drafts (to warn the user "this sounds spammy")? **Not in v1; revisit after dashboard ships.**
3. Cross-lead `global_replies` vault — do we anonymize before storing? **Yes: strip names/emails/companies via regex; replace with placeholders. Implement in `vault.py.add_global_reply()`.**
