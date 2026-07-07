import type { StepChannel, StepOut } from "../api/sequences";

export interface StepChannelMeta {
  label: string;
  icon: string;
  accent: string;
  ring: string;
}

export const STEP_CHANNEL_META: Record<StepChannel, StepChannelMeta> = {
  email: {
    label: "Email",
    icon: "📧",
    accent: "bg-sky-50 text-sky-800 border-sky-200",
    ring: "ring-sky-200",
  },
  linkedin_connect: {
    label: "LinkedIn Connection Request",
    icon: "🔗",
    accent: "bg-blue-50 text-blue-800 border-blue-200",
    ring: "ring-blue-200",
  },
  linkedin_dm: {
    label: "LinkedIn Message",
    icon: "💬",
    accent: "bg-indigo-50 text-indigo-800 border-indigo-200",
    ring: "ring-indigo-200",
  },
  linkedin_like: {
    label: "LinkedIn Post Like",
    icon: "👍",
    accent: "bg-cyan-50 text-cyan-800 border-cyan-200",
    ring: "ring-cyan-200",
  },
  call: {
    label: "Call Task",
    icon: "📞",
    accent: "bg-amber-50 text-amber-900 border-amber-200",
    ring: "ring-amber-200",
  },
  sms: {
    label: "SMS",
    icon: "📱",
    accent: "bg-emerald-50 text-emerald-800 border-emerald-200",
    ring: "ring-emerald-200",
  },
  whatsapp: {
    label: "WhatsApp",
    icon: "💚",
    accent: "bg-green-50 text-green-800 border-green-200",
    ring: "ring-green-200",
  },
};

const FALLBACK_META: StepChannelMeta = {
  label: "Custom Task",
  icon: "📋",
  accent: "bg-slate-50 text-slate-800 border-slate-200",
  ring: "ring-slate-200",
};

export function getStepChannelMeta(channel: string): StepChannelMeta {
  return STEP_CHANNEL_META[channel as StepChannel] ?? {
    ...FALLBACK_META,
    label: channel.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()),
  };
}

export function formatStepDelay(days: number, hours: number): string | null {
  if (days === 0 && hours === 0) return null;
  const parts: string[] = [];
  if (days > 0) parts.push(`${days} day${days === 1 ? "" : "s"}`);
  if (hours > 0) parts.push(`${hours} hour${hours === 1 ? "" : "s"}`);
  return parts.join(" ");
}

export function sortSteps(steps: StepOut[]): StepOut[] {
  return [...steps].sort((a, b) => a.step_order - b.step_order);
}

export function hasStepDelay(days: number, hours: number): boolean {
  return days > 0 || hours > 0;
}

/** Reorder steps and preserve transition delays (waits before steps 2..N). */
export function reorderStepsWithTransitionDelays(
  steps: StepOut[],
  orderedStepIds: number[],
): StepOut[] {
  const ordered = sortSteps(steps);
  const transitionDelays = ordered.slice(1).map(s => ({
    delay_days: s.delay_days,
    delay_hours: s.delay_hours,
  }));
  const byId = new Map(ordered.map(s => [s.id, s]));
  return orderedStepIds.map((id, i) => {
    const delay =
      i === 0
        ? { delay_days: 0, delay_hours: 0 }
        : transitionDelays[i - 1];
    return {
      ...byId.get(id)!,
      step_order: i + 1,
      ...delay,
    };
  });
}
