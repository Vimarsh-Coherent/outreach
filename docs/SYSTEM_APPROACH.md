# Coherent Outreach — Complete System Approach

_Last updated: 2026-06-11 · extension build `hardening-v20`_

This document explains, in detail, **how the whole system works today** — the
architecture, the end-to-end flow (add leads → connect → wait for acceptance →
DM), every component, the LinkedIn DOM-automation strategy, and especially the
**DM recipient-targeting approach** that has been the hardest part to get right.

---

## 1. What the system does

A self-running, multi-channel cold-outreach platform that runs **locally** on
your machine:

- **Email** outreach via SMTP/IMAP.
- **LinkedIn** outreach via a Chrome extension that drives your logged-in
  LinkedIn tab (connection requests + DMs).
- **Acceptance-gated DMs** — a follow-up DM only fires *after* the lead accepts
  the connection request.
- **Human-like warm-up** — feed browsing, reactions, follows, to look natural.
- **Dashboards** — an in-extension side panel + a web frontend showing campaigns,
  connection status, and system health.
- **Self-healing watchdog** + a **Windows startup agent** that brings the whole
  system up on boot.

---

## 2. High-level architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  YOUR MACHINE                                                        │
│                                                                     │
│  ┌──────────────┐   HTTP    ┌───────────────────────────────────┐   │
│  │  Frontend     │◄────────►│  Backend (FastAPI + Postgres)      │   │
│  │  (Vite :5173) │          │   • scheduler/dispatcher tick      │   │
│  └──────────────┘          │   • recovery + watchdog workers    │   │
│                            │   • /api/extension/* endpoints     │   │
│                            └───────────────┬───────────────────┘   │
│                                            │ HTTP (token auth)      │
│                                            ▼                        │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  Chrome Extension                                          │    │
│  │   • background.js  (service worker: polls, dispatches)     │    │
│  │   • content_linkedin.js (DOM automation in the LI tab)    │    │
│  │   • sidepanel + popup (dashboard + config)                │    │
│  └──────────────────────────┬─────────────────────────────────┘    │
│                             │ drives                               │
│                             ▼                                      │
│                  ┌────────────────────┐                            │
│                  │  linkedin.com tab  │                            │
│                  └────────────────────┘                            │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │  Startup agent (Python) — boots backend + Chrome on login  │    │
│  └────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

**Key design choice:** the backend **never touches LinkedIn directly**. It only
*queues commands*. The extension is the only thing that touches LinkedIn, using
the user's real, logged-in session. This keeps the automation tied to a genuine
browser session (lower detection risk) and means the backend has no LinkedIn
credentials.

---

## 3. The end-to-end flow (the core loop)

Using a 2-step LinkedIn sequence (Step 1 = `linkedin_connect`, Step 2 =
`linkedin_dm`) as the example:

### Phase 1 — Enrol leads
You add leads to a sequence. Each becomes an **`enrolment`** row with
`next_send_at = now`, `current_step_order = 0`, and a `contact_snapshot`
containing the LinkedIn URL.

### Phase 2 — Backend queues the connection request
The **dispatcher `tick()`** runs every 60s:
1. `claim_due()` locks enrolments whose `next_send_at <= now`
   (`FOR UPDATE SKIP LOCKED` so workers never collide).
2. For a `linkedin_connect` step, `process_one()` checks: lead has a LinkedIn URL,
   an active LinkedIn channel exists, and the **extension heartbeat is fresh
   (<1h)**. If all good, it inserts a **`li_commands`** row (`status='pending'`,
   `command_type='connect'`) and **pauses** the enrolment
   (`next_send_at=NULL`) — it will not advance until the extension reports back.

### Phase 3 — Extension sends the connection request
The **service worker** polls every 30s:
1. `GET /api/extension/next-command` **atomically claims one** pending command
   (`pending → claimed`, `LIMIT 1 ... FOR UPDATE SKIP LOCKED`).
2. The background **navigates the LinkedIn tab to the target profile, waits for
   it to load, then sends the command** to the content script (this fixes the
   old "navigate-and-bail" bug).
3. The content script runs `executeConnect`: finds the real `<a>` Connect button
   (matched by the profile's `vanityName` in its href — ignoring the "People you
   may know" rail), opens the shadow-DOM invite modal, optionally adds a note,
   clicks "Send" / "Send without a note", and verifies the invite went out.
4. Reports back via `POST /api/extension/commands/{id}/complete` (`done`/`failed`).

On `done`, the backend marks the step sent, writes a `delivered` event, charges
the daily cap, and runs `_advance_after_linkedin`.

### Phase 4 — The acceptance gate
When the connect completes and the **next step is a DM**,
`_advance_after_linkedin` does **not** schedule the DM by timer. Instead it
**parks** the enrolment:
- `runtime_state.awaiting_acceptance = true`, `awaiting_since = now`
- `next_send_at = now + LI_ACCEPT_TIMEOUT_DAYS` (default 7) — the give-up
  deadline.
- Records `li_status = invited_pending` (or `connected` if already 1st-degree,
  in which case the DM is scheduled immediately).

### Phase 5 — Detecting acceptance
The content script's `scanConnections()` runs every 60s. While you browse **My
Network / notifications / feed**, it collects 1st-degree connection profile URLs
and acceptance-notification entries and posts them to
`POST /api/extension/connections-seen`. The backend matches each to a parked
enrolment, writes a `connection_accepted` event, sets `li_status = connected`,
clears the gate, and **schedules the DM** (`next_send_at = now + DM-step delay`).

### Phase 6 — The DM sends (only to accepted leads)
On the next tick, the released enrolment is due. `process_one` checks the gate:
- Gate cleared (accepted) → mints a `dm` command → extension sends the DM.
- Gate still set (deadline hit, never accepted) → **skips** the DM (can't message
  a non-connection) and advances.

### Phase 7 — Replies
`scanInbox()` posts inbound DMs to `POST /api/extension/replies`. A matched reply
**stops the enrolment** (`stopped_reply`) and triggers sentiment classification +
AI follow-up drafting (VectorVault).

---

## 4. Backend components

| File | Role |
|---|---|
| `workers/dispatcher.py` | `tick()` every 60s: reclaim stale commands → claim due enrolments → mint email sends / LinkedIn commands. Acceptance gate + already-connected logic in `_advance_after_linkedin`. |
| `workers/recovery.py` | Reclaim `step_runs` stuck in `reserving`. |
| `workers/watchdog.py` | 5-tier patrol (health, channels, stuck-state sweep, deep verify, daily reset) + AI selector-heal + acceptance-stall alarm. |
| `api/routes_extension.py` | All extension endpoints (token-auth'd). |
| `services/extension_auth.py` | HMAC token verification per channel. |
| `services/watchdog_state.py` | In-process metrics + circuit breaker + per-intent fragility counters. |

**Extension endpoints** (`/api/extension/*`, auth via
`X-Extension-Channel-Id` + `X-Extension-Token`):
- `GET /heartbeat` — liveness; also **wakes leads parked because the extension
  was offline** (`deferred_offline`).
- `GET /next-command` — atomically claim one pending command.
- `POST /commands/{id}/complete` — report done/failed; advances the funnel.
- `POST /connections-seen` — acceptance detection → release the DM gate.
- `POST /replies` — inbound DM ingestion.
- `POST /heal-selector` — AI selector healing (Claude).
- `POST /watchdog-note` — realtime self-heal telemetry.
- `GET /campaigns`, `GET /leads`, `GET /activity`, `GET /status` — dashboard data.

**Resilience built into the backend:**
- **Fast stale-claim reclaim** — `li_commands` stuck in `claimed` >5 min are
  reset to `pending` on every tick (vs the old hourly watchdog sweep).
- **Heartbeat-wake** — opening the extension pulls forward leads that were
  parked because it was offline.
- **SQLAlchemy persistence fix** — `runtime_state` is always re-assigned as a
  **fresh dict** (an in-place mutation isn't detected and silently fails — this
  was the bug behind the acceptance flag not saving).

---

## 5. Extension components

| File | Role |
|---|---|
| `background.js` | Service worker. Polls every 30s, **serializes** dispatch (one command at a time), navigates the tab to the target + waits for load, forwards telemetry/heal/acceptance. Prints a **build marker** on boot so you can verify the loaded version. |
| `content_linkedin.js` | All DOM automation: `executeConnect`, `executeDm`, selector registry + AI self-heal, `scanInbox`, `scanConnections`, warm-up engine. |
| `sidepanel.{html,js}` | Live dashboard (campaigns, connection status, activity, health, warm-up). |
| `popup.{html,js}` | Channel id/token config + "Open dashboard". |

**Concurrency control:** `dispatchBusy` lock — only one command executes at a
time. Otherwise overlapping 30s polls would navigate the shared tab mid-command
and time them out.

---

## 6. LinkedIn DOM-automation strategy

LinkedIn's UI is the hard part:
- **Hashed/obfuscated class names** that change.
- **Shadow DOM** — invite/compose modals live inside an open shadow root
  (`#interop-outlet`). Plain `document.querySelector` can't see them, so we use
  **`deepQuerySelector` / `deepQuerySelectorAll`** that walk known shadow roots.
- **Encoded vanities** — profile links inside chats use the member-id form
  (`/in/ACoAA…`), which never string-matches the readable profile URL.

**Self-healing:** when a registry selector fails, the content script sends a
shadow-DOM-aware snapshot to the backend, Claude returns replacement selectors,
and they're cached in `chrome.storage`. _(Note: AI healing is currently paused —
the Anthropic quota is exhausted until 2026-07-01 — but the direct selectors
don't depend on it.)_

### Connect flow (`executeConnect`)
1. Find the **top-card** Connect control by `vanityName` in its href (an `<a>` in
   the new UI), never a sidebar rail button.
2. **Already-invited/connected detection** — if the profile shows "Pending" or is
   already 1st-degree, report success immediately (no pointless retry).
3. Open the shadow-DOM invite modal → "Add a note" (insert note via the native
   value-setter so the controlled component registers it) → "Send invitation" /
   "Send without a note".
4. Verify the invite went out (modal closed / "Pending" / toast).

---

## 7. The DM recipient-targeting problem (the hard one)

**The problem:** when DMing several people in sequence, LinkedIn's **floating
chat windows persist and stack up**. Each click on "Message" can open/focus a
window, and leftover windows for *other* people stay open. If the code grabs the
wrong window's editor, it types one person's message into another person's chat
— the "all messages went to one profile" bug.

This took many iterations because the chat UI is in a shadow DOM with class names
we had to **inspect on the live page** rather than guess.

### What the live DOM inspection revealed
- **Chat windows** are `.msg-overlay-conversation-bubble` / `.msg-convo-wrapper`.
- The **recipient name** is reliably in `[class*="header"] [class*="title"]`
  (the encoded `/in/ACoAA…` links are useless for matching).
- The **message editor** is `.msg-form__contenteditable` — but its
  `contenteditable` attribute value is **not** reliably `"true"`, so it must be
  matched by **class**, not `[contenteditable="true"]`.
- The **Send button** is `button.msg-form__send-button` with `type="submit"`.
- The **close button** has **no aria-label** — only the **text** "Close your
  conversation…" (class `msg-overlay-bubble-header__control`). Matching on
  aria-label never closed anything; matching on text does.

### Current approach (`executeDm`, build `positivematch-v18`)
1. **Close all chat windows** — match buttons whose *text* contains
   "close your conversation", loop until none remain.
2. Click the **top-card** Message button (not a sidebar one).
3. **Find the target's OWN window** — iterate `.msg-overlay-conversation-bubble`/
   `.msg-convo-wrapper` that contain a `.msg-form__contenteditable`, and return
   the one whose **own header name positively matches the target**
   (`dmNamesMatch`).
4. **Fail-closed** — there is **no** "use the only open window" fallback. If no
   window's header positively matches the target, **abort**
   (`dm_target_window_not_found`). This makes a wrong-person send **structurally
   impossible** — worst case is "didn't send," never "sent to the wrong person."
5. Insert text into **that window's** editor (controlled-component-aware:
   paste / beforeinput with `composed:true`).
6. **Send via that window only** — click `msg-form__send-button` with a full
   mouse-event sequence, then `form.requestSubmit()` (honors `type=submit`),
   then Enter-to-send as fallbacks.
7. Verify the composer cleared/detached = sent.

### Why earlier versions failed (the saga)
| Build | Problem fixed |
|---|---|
| v8 | Fail-closed guard (stopped the first spam incident) |
| v9 | Send via mouse-event sequence |
| v10 | Conflict-aware guard (don't abort legit sends) |
| v11 | Close leftover windows (but matched wrong button) |
| v12 | Send via `form.requestSubmit()` |
| v13 | Match by name; ignore encoded `ACoAA…` vanities |
| v14 | **Window-scoped** — find the target's window, operate only inside it |
| v15 | **Real close button** (match on text, not aria — from DOM dump) |
| v16 | Safe single-window fallback |
| v17 | Editor matched by **class** (not `[contenteditable="true"]`) |
| v18 | **Removed the unsafe fallback** — positive name match only; reliable name extraction via `[class*="header"] [class*="title"]` |
| **v20** | **Cluttered-chat hardening** — minimized pills are expanded then closed (a collapsed pill has no close control, so cleanup used to leave one behind); if the target's window isn't found, one clean in-flow retry (clear overlays → re-click Message → re-search) before the fail-closed abort; target name resolved from 3 sources (`main h1` → Message/Invite aria-label → `document.title`) with a distinct `dm_target_name_unresolved` error; command timeout 25s → 45s |

---

## 8. Connection-status tracking

Every lead carries a **`li_status`** in `runtime_state`:
`not_sent → pending (invited) → connected (accepted) → dm_sent`.
Surfaced via `GET /api/extension/leads` and the side panel's "Connection status"
section. Already-1st-degree leads skip the acceptance wait and DM immediately.

---

## 9. Warm-up engine

Runs only on `/feed/`, never while a command is executing (`cmdBusyUntil`
flag), heavily jittered. Conservative daily caps: ~5 reactions, ~3 follows,
~8 feed sessions. Varied reactions (Celebrate/Support/Love/Insightful/Funny) +
occasional follows. Counts stored in `chrome.storage`, shown on the dashboard.

---

## 10. Windows startup agent

`agent/coherent_agent.py` (+ `install_agent.ps1`): at login it waits for real
internet, starts the backend if needed, launches Chrome in a **dedicated
profile** with the extension loaded + LinkedIn open, and relaunches anything
that dies. See `docs/AGENT_SETUP.md`.

---

## 11. Watchdog / self-healing

5 tiers (5 min → 24 h): backend/DB health, SMTP/IMAP + extension heartbeat,
stuck-state sweep (reclaim stuck commands, retry DOM-fragility failures,
pre-emptive selector heal), external-dependency verify, daily reset. Plus
real-time telemetry from the extension and an **acceptance-stall alarm** (leads
waiting but no accepts detected → likely the scanner broke or you haven't
browsed My Network).

---

## 12. Safety / anti-detection

- All actions run in **your real, logged-in browser session** (no API scraping).
- **Serialized** dispatch (one action at a time), **jitter**, **send windows**,
  and per-channel **daily caps** (connect/DM/email).
- Warm-up activity is **conservative** by default.
- **Fail-closed DM targeting** — never message the wrong person.

---

## 13. Current state & known issues

**Working / verified:**
- Connection requests send correctly (new `<a>` UI, shadow modal, notes).
- Acceptance gate + already-connected → immediate DM.
- Connection-status tracking + dashboards + warm-up + agent.
- DMs have been delivered correctly to the right person in several runs.

**Pipeline audit fixes (2026-06-11, same v20 build):**
- **LinkedIn URL matching was broken end-to-end**: leads store
  `…/in/slug` (no trailing slash) but the extension reports `…/in/slug/` —
  exact-equality matching in `/connections-seen` and `/replies` could NEVER
  match, so acceptance detection and LinkedIn reply-stop silently did nothing.
  Both endpoints now match on the lowercased `/in/` slug (regex-extracted on
  both sides). Verified against live DB rows.
- **Per-type LinkedIn daily caps now enforced** (`LI_DAILY_CAP_CONNECT=20`,
  `LI_DAILY_CAP_DM=25`): they existed in config but were checked nowhere — the
  only limit was the channel-level cap (default 100/day!), charged after the
  fact. The dispatcher now counts the rolling-24h commands per type at mint
  time and defers 3h when at cap.
- **Patrol page rotation**: the patrol only ever sat on `/feed/`, but
  scanInbox needs `/messaging/` and the connections scan needs `/mynetwork/…/
  connections/` — in the autonomous dedicated-profile setup, replies and
  accepts were never detected. The patrol now rotates
  feed → messaging → connections → feed → notifications (skipping navigation
  if a command is mid-flight).
- **Sequence activation validates channels**: activating a sequence whose
  steps have no matching active channel (email or LinkedIn) is now a 400 with
  a clear message, instead of an invisible hourly fail-loop.
- **`consecutive_failures` was silently never persisted** in the dispatcher's
  failure path (mutate-then-reassign of the same dict — the same SQLAlchemy
  trap as the old acceptance-flag bug). Fixed with a fresh dict; config-type
  failures (no channel / bad creds) additionally no longer count toward the
  errored threshold at all.
- **Pending `li_commands` older than 72h are expired** by the Tier-3 sweep
  (fail the run, reschedule the enrolment) instead of parking the lead forever.

**Hardened in v20 (2026-06-11):**
- **Cluttered chat state**: minimized pills are now expanded → closed during
  cleanup, and the DM flow retries once (clear overlays → re-click Message)
  before the fail-closed abort. Targeting remains positive-match-only.
- **Target-name resolution**: 3 independent sources; a page where none yield a
  name fails fast with `dm_target_name_unresolved` (auto-retried by watchdog).
- **Connect control**: rail "Invite/Connect" buttons can no longer be picked —
  matching prefers this profile's name in the aria-label, is scoped to `<main>`,
  and excludes `<aside>`.
- **Failure economics**: DOM-fragility failures need 10 consecutive hits (vs 5
  for genuine failures) before an enrolment is marked `errored`, since the
  watchdog actively retries them.

**External:**
- The Anthropic quota outage resolved itself — the selector healer was
  **observed working again on 2026-06-11** (live heals returning candidates).
  If it ever exhausts again, the backend fails heals fast (6h cooldown after a
  quota error) instead of blocking each command ~20s on a doomed API call.

---

## 14. How to run

1. **Backend:** `cd backend` → `uvicorn outreach.main:app --host 127.0.0.1 --port 8000 --app-dir src --reload`
2. **Frontend:** `cd frontend` → `npm run dev` (http://localhost:5173)
3. **Extension:** `chrome://extensions` → Load unpacked → `extension/`. Configure
   channel id + token in the popup; open the dashboard from the popup.
4. **Auto-start (optional):** `cd agent` → `.\install_agent.ps1`
5. **Verify state anytime:** `backend/.venv/Scripts/python.exe scripts/diagnose_state.py`

> After editing the extension, **reload it in `chrome://extensions` AND reload
> the LinkedIn tab** — the service worker and content script are separate; the
> build marker (`[coherent] background.js loaded — build …`) in the service-worker
> console confirms which version is live.
