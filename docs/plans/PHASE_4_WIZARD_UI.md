# Phase 4 — Campaign Wizard UI (frontend)

Goal: the product experience — "connect channels → pick leads → describe offering →
review AI sequence → Launch" as a single guided flow. After launch the user lands
on the campaign's detail view and the system is already running.

Depends on: Phases 1–2 (endpoints). Phase 3 optional (wizard works without it;
previews just show templates).

## 1. Deliverables
1. `pages/NewCampaign.tsx` — 4-step wizard (route `/campaigns/new`).
2. `pages/Offerings.tsx` — manage saved offerings (route `/offerings`).
3. Components: `wizard/ChannelsStep.tsx`, `wizard/LeadsStep.tsx`,
   `wizard/OfferingStep.tsx`, `wizard/ReviewStep.tsx`, `wizard/StepCard.tsx`,
   `wizard/PacingPanel.tsx`, `wizard/WizardShell.tsx` (progress header, back/next).
4. API clients: `api/offerings.ts`, `api/campaigns.ts`.
5. Nav: "New Campaign" primary CTA in the sidebar + empty-state CTA on Dashboard
   and Sequences pages.

## 2. Wizard state machine

```
WizardState {
  step: 1|2|3|4
  channels: { emailChannelId?: number, linkedinChannelId?: number }
  leads:    { mode: 'upload'|'existing', selectedIds: number[], uploadCommitted: boolean }
  offering: { id?: number, name, description, tone, files: File[], extracted?: Extracted }
  campaign: { sequenceId?: number, steps: Step[], warnings: string[] }
}
```
- Persist to `sessionStorage` on every change (key `wizard-draft`) so an accidental
  refresh doesn't lose the description text. Clear on launch/cancel.
- Steps are revisitable (clicking the progress header); forward gated by per-step
  validity below.

## 3. Step specs

### Step 1 — Channels
- Cards for Email + LinkedIn with live status from `GET /api/channels`
  (+ LinkedIn `ext_last_heartbeat_at` freshness → "extension online/offline" pill).
- Email missing → inline render of the EXISTING SMTP form from `Channels.tsx`
  (extract it into `components/EmailChannelForm.tsx` rather than duplicating —
  presets, test button, IMAP block included). NOTE: Brevo creds were wiped in the
  2026-06-12 incident; this is where the user re-enters them.
- LinkedIn missing → mint-token flow (existing POST /api/channels/linkedin) with
  the copy-token-into-extension-popup instructions.
- Valid to proceed: ≥1 active channel. Both recommended (copy says what they'll get).

### Step 2 — Leads
- Tab A "Upload new": reuse the existing upload→preview→column-map→commit
  components from `Leads.tsx` (extract `components/LeadUploadFlow.tsx`).
  After commit, auto-select the just-imported lead ids (commit response already
  reports them; if it only reports counts, add `inserted_ids` to the commit
  response — small backend tweak).
- Tab B "Use existing": existing lead table w/ search + select-all-matching.
- Footer: "N selected · X with email · Y with LinkedIn URL" (drives Step 4 warnings).
- Valid: ≥1 selected.

### Step 3 — Offering
- Pick existing offering (dropdown w/ one_liner) OR create new:
  name, tone select (professional/friendly/direct/casual), big textarea
  ("Describe what you're pitching: what it is, who it's for, what problem it
  solves, pricing/CTA if relevant") + drag-drop multi-file upload (pdf/docx/txt/md,
  ≤20MB each).
- On blur/Continue: POST offering → docs → show **Extracted summary card**:
  one-liner, value props, pain points, ICP, CTA — each chip editable inline
  (PATCH writes back into `extracted` via a dedicated PATCH field; backend merges
  fresh-dict). "Re-extract" button. Extraction spinner state ("Reading your
  document…", typical 10–20s).
- ai_guard 503 → friendly banner with `cooldown_until` + "you can still write
  the sequence manually in Sequences" escape hatch.
- Valid: offering status === 'ready' (or user confirms proceeding with raw
  description only — allowed, generator can run from description alone).

### Step 4 — Review & Launch
- On entry: `POST /api/campaigns/generate` (spinner: "Designing your sequence…").
- Vertical timeline of StepCards: channel icon, day offset ("Day 0", "Day 3"),
  charter chip (intro/value/breakup/connect/dm), subject + body in an editable
  textarea (PATCH step on blur via existing sequences API), char counter with the
  280/1200 limits on LinkedIn cards, per-card **Regenerate** (popover for optional
  instruction text) and rationale tooltip.
- **Preview against real leads**: dropdown of 3 sample leads from selection →
  client-side token render (mirror template_render's `{{field}}` rules) so the
  user sees "Hi Priya," not "Hi {{first_name}},". Label clearly when Phase 3 is
  live: "final emails are additionally AI-tailored per lead".
- Right rail: `PacingPanel` (GET pacing w/ lead_count): projected days, bottleneck
  ("LinkedIn connects capped at 20/day"), send window + timezone editors
  (PATCH sequence), spam-lint `warnings[]`, partial-coverage warnings
  ("12 leads have no LinkedIn URL — they'll get email steps only").
- **Launch** button → POST /api/campaigns/launch → success screen with counts →
  navigate to the campaign detail (Phase 5 page; until then, SequenceEditor).
- Generate failure (422 violations / 503) → error card with retry + manual escape hatch.

## 4. API clients

`api/offerings.ts`: list/get/create/patch/delete, uploadDocument (multipart,
progress), deleteDocument, reExtract. Types mirror Phase 1 schemas incl.
`Extracted`.
`api/campaigns.ts`: generate, regenerateStep, launch, pacing. Reuse `Sequence`/
`Step` types from `api/sequences.ts`.

## 5. Refactors of existing pages (no behaviour change)
- `Channels.tsx` → extract `EmailChannelForm.tsx` (used by both).
- `Leads.tsx` → extract `LeadUploadFlow.tsx` (used by both).
- `Sequences.tsx`: AI-generated sequences get a "✨ AI" badge (generated_by) and
  link to their offering.

## 6. Edge/UX cases
- Wizard opened with everything already set up → steps 1–2 show green summaries,
  user clicks through in seconds.
- Leaving mid-wizard with a generated draft → draft sequence remains (status
  'draft', visible in Sequences as "Draft · AI"); re-entering wizard offers
  "resume draft" if sessionStorage has one.
- Double-click Launch → button disables on first click; backend 409 on
  non-draft status is the safety net.
- Extension offline at launch with LinkedIn steps → non-blocking warning
  ("LinkedIn steps will queue until the extension reconnects" — dispatcher's
  deferred_offline handles it).

## 7. Tests / verification
- Vitest component tests if test infra exists; otherwise a manual checklist:
  fresh-profile walkthrough (no channels, no leads) and a fast-path walkthrough
  (everything pre-existing) — both must reach a running campaign.
- Token preview parity: unit-test the client-side renderer against
  template_render fixtures (same input → same output).

## 8. Acceptance criteria
1. A new user with nothing configured reaches "campaign running" through the single
   wizard flow without visiting any other page.
2. Editing + regenerating a step in Review persists (reload shows the edit).
3. Launch produces enrolments and the Dashboard funnel moves on the next tick.

## 9. Estimated effort
1–2 sessions (the two extractions from Channels.tsx/Leads.tsx are the risky part —
do them first as standalone commits with the old pages still green).
