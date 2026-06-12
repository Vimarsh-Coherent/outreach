# Phase 2 — AI Campaign Generator + One-Click Launch (backend)

Goal: `offering → complete draft sequence` and `draft → enrolled + active` as two
API calls. After this phase the whole product loop works via curl, before any UI.

Depends on: Phase 1. Blocks: Phase 4 (wizard calls these endpoints).

## 1. Deliverables
1. `services/campaign_generator.py`
2. `services/enrol_service.py` — factor the enrol logic out of
   `routes_sequences.py: POST /{id}/enrol` so launch can reuse it (routes keep
   their behaviour, now delegating).
3. `services/pacing.py` — days-to-complete projection.
4. `api/routes_campaigns.py` + `schemas/campaigns.py`.
5. Tests.

## 2. campaign_generator.py

```python
DEFAULT_MODEL = "claude-sonnet-4-6"

@dataclass
class GenerateOptions:
    num_emails: int = 3              # 1..5
    include_linkedin: bool | None = None   # None = auto (use if channel connected)
    spacing: str = "normal"          # 'tight'(1-2d) | 'normal'(2-4d) | 'relaxed'(4-7d)
    language: str = "en"
    extra_instructions: str = ""     # free text from the wizard

async def generate_campaign(session, user, offering, options) -> Sequence  # status='draft'
async def regenerate_step(session, user, step, instructions: str = "") -> SequenceStep
```

### Inputs assembled into the prompt
- `offering.extracted` (the only allowed source of product facts) + `offering.description`.
- Audience sample: up to 30 distinct `(title, company)` pairs from the user's leads
  (cheap SELECT) — lets the model match register to the audience.
- Connected channel types (email / linkedin) → which step kinds are allowed.
- Options + per-channel constraints (connect note ≤ 280 chars, DM ≤ 1200, subject ≤ 80).
- Available tokens: `{{first_name}} {{last_name}} {{company}} {{title}} {{email}}`
  (must match `services/template_render.py` exactly).

### Output via forced tool use
Tool `record_campaign` with schema:
```json
{
  "sequence_name": "str",
  "steps": [{
    "channel": "email | linkedin_connect | linkedin_dm",
    "delay_days": "int >= 0", "delay_hours": "int 0-23",
    "subject": "str|null (email only)",
    "body": "str (uses {{tokens}})",
    "charter": "connect | intro | dm | value | breakup",
    "rationale": "str <= 200 chars"
  }]
}
```

### Canonical shapes the prompt requests
- **Both channels**: linkedin_connect(d0) → email intro(d1) → linkedin_dm(d2; the
  existing acceptance gate parks it until the invite is accepted, skips on timeout)
  → email value/proof(d+3) → email breakup(d+4..7 by spacing).
- **Email-only**: intro(d0) → value(d+3) → objection/proof(d+3) → breakup(d+4).
  (num_emails controls count; charters assigned in that order.)
- **LinkedIn-only**: connect(d0) → dm(d2) → dm follow-up(d+4).

### Code-level validation (`_validate_plan`) — never trust the model
- channels ⊆ connected types; ≥1 step; ≤ 8 steps.
- linkedin_dm only after a linkedin_connect OR as step 1 (already-connected case is
  handled at runtime by routes_extension immediate-DM logic — allowed but warn).
- email steps have subject; linkedin steps have subject=None.
- char limits: connect ≤ 280, dm ≤ 1200, subject ≤ 80, email body ≤ 2500.
- every `{{token}}` used ∈ allowed set (regex scan; unknown token = hard fail).
- spam lint (warning, not failure): wordlist check (free!!, guarantee, act now, …) —
  returned as `warnings[]` for the UI.
- step 1 delay_days==0.
On validation failure: ONE retry appending the violation list to the prompt;
second failure → raise `GenerationInvalid` → 422 with the violations.

### Persistence
INSERT `Sequence(status='draft', offering_id, generated_by='ai', name,
timezone=user default 'Asia/Kolkata'?, send_window/days = current platform defaults)`
+ `SequenceStep` rows with `config={"ai_personalize": true (email+dm only),
"charter": ..., "rationale": ...}`. Returns the same payload shape as
`GET /api/sequences/{id}` so the existing SequenceEditor can also open it.

### regenerate_step
Same model; prompt = offering facts + the FULL current sequence (for coherence)
+ the one step to rewrite + user instructions ("shorter", "less formal", …).
Validates the single step with the same rules, updates in place.

## 3. routes_campaigns.py

| Method/Path | Req → Resp |
|---|---|
| POST /api/campaigns/generate | `{offering_id, options?}` → `{sequence: <full seq+steps>, warnings: [..]}`. 409 if no channels connected at all; 503+cooldown_until if ai_guard cooling |
| POST /api/campaigns/steps/{step_id}/regenerate | `{instructions?}` → `{step, warnings}` |
| POST /api/campaigns/launch | `{sequence_id, lead_ids: [..]}` → `{enrolled, deduped, skipped_no_identity, skipped_no_channel_field, pacing}` |
| GET /api/campaigns/{sequence_id}/pacing | `?lead_count=N` → pacing preview (wizard shows before launch) |

### Launch semantics (atomic, all-or-nothing on validation)
1. Sequence must be status 'draft' or 'paused'; 409 otherwise.
2. Channel validation: reuse the existing activation validation from
   `routes_sequences.py POST /{id}/status` (factor into `enrol_service.validate_channels`):
   every distinct step channel type must have an active channel. 409 lists missing.
3. Enrol via `enrol_service.enrol_leads(...)` — identical dedupe/identity behaviour
   as today's enrol endpoint, plus: leads lacking the field a step needs
   (no email / no linkedin_url) are still enrolled (dispatcher already skips
   per-step with 'no email on contact' / 'no linkedin_url') but counted in a
   `partial_leads` warning so the UI can say "12 leads have no LinkedIn URL —
   email steps only".
4. `sequence.status = 'active'`; first `next_send_at` computed exactly as the
   existing enrol path does (next_valid_slot + jitter).
5. Response includes `pacing`.

## 4. pacing.py
```python
def project(lead_count: int, steps: list[StepLike], caps: CapsSnapshot) -> dict
# caps: email channel daily_cap, settings.li_daily_cap_connect / _dm
# returns {"days_to_first_touch_all": d1, "days_to_complete_est": d2,
#          "bottleneck": "linkedin_connect cap (20/day)", "per_day": {...}}
```
Pure function, no DB. Simple division + max over channels; documented as an
estimate (ignores acceptance-gate stalls, retries).

## 5. Tests
- `test_campaign_generator.py`: mocked tool-output → sequence+steps persisted with
  correct config flags; validation catches each rule (bad token, oversize note,
  dm-before-connect, wrong channel); retry-then-422 path; email-only and
  linkedin-only shapes.
- `test_enrol_service.py`: parity test — service result equals legacy endpoint
  behaviour on the same fixture set (dedupe, identity skip).
- `test_routes_campaigns.py`: generate 409 (no channels) / 503 (cooldown);
  launch happy path flips status + enrols + returns pacing; launch atomicity
  (missing linkedin channel → 409, zero enrolments created).
- `test_pacing.py`: cap math incl. mixed-channel bottleneck.

## 6. Acceptance criteria
1. With one SMTP channel + LinkedIn channel connected and an offering ready:
   `POST /api/campaigns/generate` → 5-step draft with connect note ≤280 chars and
   all tokens valid, in <30s.
2. `POST /api/campaigns/launch` with 50 lead ids → 50 enrolled (minus dupes),
   sequence active, and the next dispatcher tick mints/sends the first steps
   (verified on a test SMTP + the valid LinkedIn test target
   aditya-jha-36193a251 — NEVER vimarshdwivedi, that's the user's own account).
3. Killing the Anthropic key breaks generate (503) but breaks NOTHING in the
   running dispatcher.

## 7. Estimated effort
~1 session backend + prompt iteration. enrol_service factoring is the only
touch on existing behaviour — keep the parity test tight.
