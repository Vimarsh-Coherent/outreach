# Phase 5 — Lifecycle Polish: Inbox, Campaign Detail

Goal: close the loop after launch — see replies in one place, act on them with the
existing AI drafter, and watch a campaign's progress without SQL.

Depends on: nothing new in backend data (events/sentiment already exist).
Phase 3's `sent_body` makes the views richer but isn't required.

## 1. Unified Inbox

### Backend — `api/routes_inbox.py`
`GET /api/inbox?sentiment=&channel=&days=30&unhandled=&page=&page_size=25`
- Source: `events` where event_type IN ('reply','auto_reply') joined to
  `reply_sentiments`, `enrolments` (→ sequence name), `leads` (name/company/email),
  ordered by occurred_at DESC, user-scoped via enrolment.user_id.
- Response item: `{event_id, occurred_at, channel, lead: {id, name, company,
  email}, sequence: {id, name}, snippet, subject, sentiment: {label, confidence},
  handled}`.
- `handled` flag: new `events.handled_at timestamptz NULL` (tiny migration) set by
  `POST /api/inbox/{event_id}/handled` (and automatically when a follow-up is sent
  to that lead via the existing `/api/followups/send` — wire in there).
- Counts endpoint for nav badge: `GET /api/inbox/counts` →
  `{unhandled_total, by_sentiment: {...}}`.

### Frontend — `pages/Inbox.tsx` (route `/inbox`)
- Sentiment filter chips with counts (positive / interested / objection / negative /
  neutral / auto_reply; unsubscribe shown but read-only — suppression already done).
- Row click → right-side drawer: full reply text, mini-timeline (reuse Timeline
  fetch), inline **FollowUpDrafter** (existing component) for email leads,
  "Open LinkedIn profile" link for DM replies (manual answer — we do NOT autosend
  LinkedIn replies), Mark handled.
- Nav badge with unhandled count (poll /counts every 60s via react-query).

## 2. Campaign detail — finish `Enrolments.tsx`
Route `/sequences/{id}` detail view (links from Sequences list, wizard success):
- Header: status, offering link (if any), channel mix, send window, caps note.
- KPI row: enrolled / in-progress / done / replied / positive / bounced / errored
  (existing dashboard per-sequence endpoint + one new
  `GET /api/sequences/{id}/stats` if granularity is missing).
- Enrolment table (existing `GET /api/sequences/{id}/enrolments`): lead, current
  step, status, next_send_at, last event; row actions pause/resume/stop (existing
  endpoints); bulk select for pause/stop → needs small backend batch endpoint
  `POST /api/enrolments/batch` `{ids, action}` (loops the existing single-ops in
  one transaction).
- Step funnel: per step_order → sent / skipped / failed counts (one GROUP BY query,
  `GET /api/sequences/{id}/step-stats`) — shows where leads drop off.

## 3. Small backend additions summary
- Migration: `events.handled_at`.
- `routes_inbox.py` (list, counts, handled).
- `POST /api/enrolments/batch`.
- `GET /api/sequences/{id}/step-stats` (and /stats if needed).
- Wire handled_at into followups send.

## 4. Tests
- Inbox query test: sentiment filter, pagination, user scoping, handled flag
  transitions (incl. auto-set on followup send).
- Batch enrolment ops: mixed-status input (already-stopped ids are no-ops, not
  errors).
- step-stats GROUP BY against a seeded fixture.

## 5. Acceptance criteria
1. A reply arriving via IMAP appears in /inbox within one poll cycle with its
   sentiment chip; drafting + sending a follow-up from the drawer marks it handled.
2. Campaign detail shows live per-step funnel and supports bulk-pausing 10
   enrolments in one action.
3. Dashboard "hot leads" links land on the inbox drawer for that lead.

## 6. Estimated effort
~1 session (backend endpoints are thin; FollowUpDrafter reuse keeps the drawer cheap).

---

# Phase 6 — Backlog (not scheduled)
Ordered by likely value:
1. Open/click tracking — pixel + redirect endpoints; Event types already modeled.
   Needs a public URL (tunnel) — localhost can't receive opens; defer until deployed.
2. A/B step variants (steps.config.variants + deterministic split by enrolment id).
3. Sequence template library / clone endpoint.
4. Multi-mailbox rotation + warmup ramp (real deliverability work).
5. LinkedIn withdraw-stale-invites command type (extension change — touch with care).
6. Multi-tenant auth (architectural; single-user by design until needed).
