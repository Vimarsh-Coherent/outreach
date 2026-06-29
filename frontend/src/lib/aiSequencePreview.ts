import type { AIStepDraft } from "../api/aiSequences";
import type { StepOut } from "../api/sequences";

import { buildSequenceStepBreakdown } from "./sequenceStepBreakdown";

/** Map AI draft steps to StepOut-shaped rows for the flow diagram preview. */
export function draftStepsToPreviewSteps(steps: AIStepDraft[]): StepOut[] {
  const now = new Date().toISOString();
  return steps.map((step, index) => ({
    id: -(index + 1),
    sequence_id: 0,
    step_order: index + 1,
    channel: step.channel,
    delay_days: step.delay_days,
    delay_hours: step.delay_hours,
    delay_minutes: (step as { delay_minutes?: number }).delay_minutes ?? 0,
    subject: step.subject,
    body: step.body,
    config: step.config,
    created_at: now,
    updated_at: now,
  }));
}

/** Human-readable breakdown lines derived from AI draft steps. */
export function buildDetailedStepBreakdown(steps: AIStepDraft[]): string[] {
  return buildSequenceStepBreakdown(steps);
}
