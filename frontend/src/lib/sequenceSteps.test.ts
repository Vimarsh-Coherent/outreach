import { describe, expect, it } from "vitest";

import type { StepOut } from "../api/sequences";
import { reorderStepsWithTransitionDelays } from "./sequenceSteps";

function mockStep(
  id: number,
  step_order: number,
  channel: StepOut["channel"],
  delay_days: number,
  delay_hours = 0,
): StepOut {
  return {
    id,
    sequence_id: 1,
    step_order,
    channel,
    delay_days,
    delay_hours,
    subject: null,
    body: "body",
    config: {},
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function delays(result: StepOut[]) {
  return result.map(s => ({ id: s.id, order: s.step_order, d: s.delay_days, h: s.delay_hours }));
}

describe("reorderStepsWithTransitionDelays", () => {
  it("linkedin before email — preserves [2d, 1d] transitions", () => {
    const steps = [
      mockStep(1, 1, "email", 0),
      mockStep(2, 2, "linkedin_connect", 2),
      mockStep(3, 3, "linkedin_dm", 0, 1),
    ];
    const result = reorderStepsWithTransitionDelays(steps, [2, 1, 3]);
    expect(delays(result)).toEqual([
      { id: 2, order: 1, d: 0, h: 0 },
      { id: 1, order: 2, d: 2, h: 0 },
      { id: 3, order: 3, d: 0, h: 1 },
    ]);
  });

  it("message to top — preserves [3d, 1d] transitions", () => {
    const steps = [
      mockStep(1, 1, "email", 0),
      mockStep(2, 2, "call", 3),
      mockStep(3, 3, "linkedin_dm", 0, 1),
    ];
    const result = reorderStepsWithTransitionDelays(steps, [3, 1, 2]);
    expect(delays(result)).toEqual([
      { id: 3, order: 1, d: 0, h: 0 },
      { id: 1, order: 2, d: 3, h: 0 },
      { id: 2, order: 3, d: 0, h: 1 },
    ]);
  });

  it("sms to top — four steps preserve [3d, 1d, 4d] transitions", () => {
    const steps = [
      mockStep(1, 1, "email", 0),
      mockStep(2, 2, "call", 3),
      mockStep(3, 3, "linkedin_dm", 0, 1),
      mockStep(4, 4, "sms", 4),
    ];
    const result = reorderStepsWithTransitionDelays(steps, [4, 1, 2, 3]);
    expect(delays(result)).toEqual([
      { id: 4, order: 1, d: 0, h: 0 },
      { id: 1, order: 2, d: 3, h: 0 },
      { id: 2, order: 3, d: 0, h: 1 },
      { id: 3, order: 4, d: 4, h: 0 },
    ]);
  });

  it("assigns delays by position not step identity", () => {
    const steps = [
      mockStep(1, 1, "email", 0),
      mockStep(2, 2, "call", 3),
      mockStep(3, 3, "linkedin_dm", 0, 1),
      mockStep(4, 4, "sms", 4),
    ];
    const result = reorderStepsWithTransitionDelays(steps, [1, 3, 4, 2]);
    const call = result.find(s => s.id === 2)!;
    expect(call.step_order).toBe(4);
    expect(call.delay_days).toBe(4);
    expect(call.delay_hours).toBe(0);
  });
});
