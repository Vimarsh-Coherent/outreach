import { formatStepDelay, getStepChannelMeta } from "./sequenceSteps";

export interface StepBreakdownSource {
  channel: string;
  delay_days: number;
  delay_hours: number;
  subject: string | null;
  body: string;
  config?: Record<string, unknown>;
}

function stepLabel(step: StepBreakdownSource): string {
  const fromConfig = step.config?.step_label;
  if (typeof fromConfig === "string" && fromConfig.trim()) return fromConfig.trim();
  return getStepChannelMeta(step.channel).label;
}

function bodySummary(step: StepBreakdownSource): string {
  const text = (step.subject?.trim() || step.body.trim()).replace(/\s+/g, " ");
  if (!text) return "No content.";
  return text.length > 160 ? `${text.slice(0, 160)}…` : text;
}

function actionPhrase(step: StepBreakdownSource): string {
  const label = stepLabel(step);
  switch (step.channel) {
    case "email":
      return `Send an email — ${label}`;
    case "linkedin_connect":
      return `Send a LinkedIn connection request — ${label}`;
    case "linkedin_dm":
      return `Send a LinkedIn message — ${label}`;
    case "linkedin_like":
      return `Like the lead's most recent LinkedIn post — ${label}`;
    case "call":
      return `Assign a call task to the rep — ${label}`;
    case "sms":
      return `Send an SMS — ${label}`;
    case "whatsapp":
      return `Send a WhatsApp message — ${label}`;
    default:
      return `${getStepChannelMeta(step.channel).label} — ${label}`;
  }
}

/** Human-readable breakdown lines derived from sequence steps (persisted step data). */
export function buildSequenceStepBreakdown(steps: StepBreakdownSource[]): string[] {
  return steps.map((step, index) => {
    const wait = formatStepDelay(step.delay_days, step.delay_hours);
    const waitClause =
      wait && index === 0
        ? ` Wait ${wait} after enrolment before this step.`
        : wait
          ? ` Wait ${wait} before this step.`
          : "";

    return `Step ${index + 1}: ${actionPhrase(step)}.${waitClause} ${bodySummary(step)}`;
  });
}
