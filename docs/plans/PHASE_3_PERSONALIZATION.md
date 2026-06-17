# Phase 3 — Send-Time Personalization (backend)

Goal: every outgoing email, connect note, and DM is tailored per lead using the
offering — without ever blocking or failing a send on AI availability.

IMPORTANT CONTEXT: the dispatcher ALREADY auto-drafts emails at send time for
steps ≥ 2 when `sequence.ai_followups_enabled` (dispatcher.py ~line 490 →
`followup_agent.draft_for_lead`, fail-open to template). Phase 3 EXTENDS that
hook; it does not build a parallel system. The actual gaps:
  (a) no offering/product context in any prompt,
  (b) step-1 cold emails are never personalized,
  (c) LinkedIn bodies (connect note / DM) are plain token-rendered,
  (d) the final sent text isn't persisted anywhere queryable (only subject in
      the Event payload), so timeline/vault see the template, not what was sent.

Depends on: Phase 1 (offering), Phase 2 (steps carry `config.ai_personalize`).

## 1. Deliverables
1. Migration: `step_runs.sent_subject TEXT NULL`, `step_runs.sent_body TEXT NULL`.
2. `services/personalizer.py` (Claude Haiku, via ai_guard).
3. `followup_agent.py` changes: offering context.
4. `dispatcher.process_one` changes: step-1 hook, LinkedIn hook, persist sent_*.
5. `vault.py` + `routes_timeline.py` switch to sent_body when present.
6. Tests.

## 2. personalizer.py

```python
DEFAULT_MODEL = "claude-haiku-4-5-20251001"   # cheap, high volume

@dataclass
class PersonalizedCopy:
    subject: str | None
    body: str
    used_ai: bool          # False on any fallback

async def personalize_email(*, rendered_subject, rendered_body, snapshot: dict,
                            offering: Offering | None) -> PersonalizedCopy
async def personalize_linkedin(*, kind: Literal["connect","dm"], rendered_body,
                               snapshot: dict, offering: Offering | None) -> PersonalizedCopy
```
Prompt rules (system):
- Input template defines structure/intent/CTA — adapt, don't rewrite the pitch.
- Personalize ONLY from lead snapshot fields (title, company) +
  `offering.extracted` facts. NEVER invent facts about the lead or product.
- Similar length to template (±25%). Keep signature lines verbatim.
- Output via forced tool use (`record_copy` tool: {subject?, body}) — no regex JSON.
Hard limits enforced in code AFTER the call: connect ≤ 280 chars (truncate at last
sentence boundary; still over → fallback), dm ≤ 1200, subject ≤ 200.
Timeout: `asyncio.wait_for(..., 10s)`. Any exception (AICooldownActive,
AIBudgetExceeded, timeout, validation) → return template copy with `used_ai=False`
and a log line. **No exception ever escapes personalize_*.**

Offering loading: `process_one` resolves `sequence.offering_id → Offering` once per
run (single extra SELECT; offerings are tiny). `offering=None` (manual sequences)
→ prompts simply omit the product block; personalization still uses lead fields.

## 3. followup_agent.py changes (steps ≥ 2 path — keep Sonnet)
- New optional param `offering: Offering | None` threaded from process_one.
- USER_TEMPLATE gains a PRODUCT CONTEXT block: `one_liner`, top 3 `value_props`,
  `cta`, `proof_points` + up to 2 `vault.get_offering_context(offering_id, query)`
  snippets (query = the hot reply signal already computed).
- Wrap its Anthropic call in `ai_guard.guarded_call(kind='followup')` (it already
  fail-opens internally — keep that; the guard just adds cooldown/budget).
- `FollowUpDraft` unchanged → `/api/followups/draft` and FollowUpDrafter.tsx keep
  working; the manual route passes offering when the lead's latest enrolment has one.

## 4. dispatcher.process_one changes (keep the diff small)

Email branch, after the existing render:
```
if step.step_order == 1 and step.config.get("ai_personalize"):
    copy = await personalizer.personalize_email(...)            # never raises
    rendered_subject, rendered_body = copy.subject, copy.body
elif step.step_order >= 2 and seq.ai_followups_enabled:
    ... existing auto-draft block, now passing offering ...
```
Finalise block: `run.sent_subject = rendered_subject; run.sent_body = rendered_body`
(set on failure path too — aids debugging what WOULD have gone out: store only on
success to keep semantics clean → decision: success only).

LinkedIn branch (mint time), replacing the bare `render(step.body, snapshot)`:
```
rendered_body = render(step.body, snapshot)
if step.config.get("ai_personalize"):
    copy = await personalizer.personalize_linkedin(kind=command_type, ...)
    rendered_body = copy.body
cmd = LinkedInCommand(..., body_text=rendered_body)
run.sent_body = rendered_body        # what was handed to the extension
```
Note: this adds up to ~10s (timeout bound) inside a tick worker. With
SEND_CONCURRENCY=8 and the 60s tick this is acceptable; the semaphore already
bounds parallel AI calls.

## 5. Read-side fixes (cheap but high value)
- `vault.index_lead._format_sent`: prefer `run.sent_subject/sent_body` over
  `step.subject/step.body` when present — the RAG history should reflect what the
  lead actually received (today it indexes the raw template).
- `routes_timeline.py`: include sent_subject/sent_body in 'sent' items so the UI
  shows real copy.
- `followup_agent._load_prior_history`: same substitution.

## 6. Config additions
```
personalize_model: str = "claude-haiku-4-5-20251001"
personalize_timeout_seconds: int = 10
ai_daily_call_budget: int = 500          # from Phase 1, now actually consumed
```

## 7. Tests
- `test_personalizer.py`: fallback on cooldown / timeout / oversize note;
  truncation at sentence boundary; offering=None path; mocked happy path keeps
  tokens-free output (no `{{` in result — add this validation).
- `test_dispatcher_personalize.py`: step-1 flag honored; flag absent → byte-identical
  legacy behaviour; AI exception → template sent (send still succeeds);
  sent_subject/sent_body persisted; LinkedIn command body_text personalized.
- Regression: full existing dispatcher test suite must pass untouched when no
  step has `ai_personalize` (manual sequences = zero behaviour change).

## 8. Acceptance criteria
1. Launch a generated campaign for 3 test leads with distinct titles/companies →
   the 3 step-1 emails differ meaningfully and reference each lead's company/title;
   `step_runs.sent_body` shows the final copy in the Timeline UI.
2. Set ai_guard into forced cooldown (test hook) → next tick still sends, bodies
   equal the rendered templates, zero failed runs attributable to AI.
3. Connect note for the LinkedIn test target arrives ≤280 chars, references the
   offering's one-liner.
4. Anthropic spend bounded: with budget=10, the 11th personalize call in a day
   falls back to template (verified in test, not live).

## 9. Estimated effort
~1 session. The dispatcher diff is ~30 lines; most work is personalizer prompts
+ the test matrix.
