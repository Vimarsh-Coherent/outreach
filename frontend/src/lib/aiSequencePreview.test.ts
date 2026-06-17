import { describe, expect, it } from "vitest";

import type { AIStepDraft } from "../api/aiSequences";
import type { StepOut } from "../api/sequences";
import { buildDetailedStepBreakdown, draftStepsToPreviewSteps } from "./aiSequencePreview";
import { buildSequenceStepBreakdown } from "./sequenceStepBreakdown";

const sampleSteps: AIStepDraft[] = [
  {
    channel: "linkedin_connect",
    delay_days: 0,
    delay_hours: 0,
    subject: null,
    body: "Hi {{first_name}}, would love to connect.",
    config: { ai_generated: true, step_label: "Initial Connect" },
  },
  {
    channel: "email",
    delay_days: 2,
    delay_hours: 0,
    subject: "Quick question for {{first_name}}",
    body: "Following up after connecting on LinkedIn.",
    config: { ai_generated: true, step_label: "Follow-up Email" },
  },
];

const savedStep = (partial: Partial<StepOut> & Pick<StepOut, "id" | "step_order" | "channel" | "body">): StepOut => ({
  sequence_id: 1,
  delay_days: 0,
  delay_hours: 0,
  subject: null,
  config: {},
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  ...partial,
});

describe("draftStepsToPreviewSteps", () => {
  it("maps draft steps to preview StepOut rows", () => {
    const preview = draftStepsToPreviewSteps(sampleSteps);
    expect(preview).toHaveLength(2);
    expect(preview[0].step_order).toBe(1);
    expect(preview[0].channel).toBe("linkedin_connect");
    expect(preview[1].delay_days).toBe(2);
  });
});

describe("buildDetailedStepBreakdown", () => {
  it("builds readable lines from generated steps", () => {
    const lines = buildDetailedStepBreakdown(sampleSteps);
    expect(lines[0]).toMatch(/^Step 1: Send a LinkedIn connection request/);
    expect(lines[1]).toMatch(/^Step 2: Send an email/);
    expect(lines[1]).toContain("Wait 2 days before this step.");
  });
});

describe("buildSequenceStepBreakdown", () => {
  it("builds readable lines from saved StepOut rows", () => {
    const lines = buildSequenceStepBreakdown([
      savedStep({
        id: 1,
        step_order: 1,
        channel: "call",
        body: "Call {{first_name}} about pricing.",
        config: { step_label: "Discovery call" },
      }),
    ]);
    expect(lines[0]).toMatch(/^Step 1: Assign a call task/);
    expect(lines[0]).toContain("Discovery call");
  });
});
