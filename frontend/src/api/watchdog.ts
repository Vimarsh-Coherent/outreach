import { api } from "./client";

export interface WatchdogEvent {
  tier: string;
  status: "healthy" | "issue" | "emergency" | "healed";
  message: string;
  at: string;
  detail: Record<string, unknown>;
}

export interface WatchdogState {
  last_quick_check: string | null;
  last_channel_patrol: string | null;
  last_stuck_sweep: string | null;
  last_deep_verify: string | null;
  last_daily_reset: string | null;

  backend_alive: boolean;
  db_alive: boolean;
  anthropic_alive: boolean;
  openai_alive: boolean;
  chroma_alive: boolean;

  email_channels_healthy: number;
  email_channels_broken: number;
  linkedin_channels_alive: number;
  linkedin_channels_stale: number;

  stuck_step_runs: number;
  stuck_li_commands: number;
  stuck_enrolments: number;
  unclassified_events: number;

  detections_today: number;
  linkedin_command_failures_today: number;
  consecutive_failures: number;
  disabled_until: string | null;

  events: WatchdogEvent[];
}

export type WatchdogTier =
  | "quick_check" | "channel_patrol" | "stuck_state_sweep" | "deep_verify" | "daily_reset";

export async function getWatchdogState() {
  return (await api.get<WatchdogState>("/watchdog/state")).data;
}

export async function runTier(tier: WatchdogTier) {
  return (await api.post<{ tier: string; result: any }>(`/watchdog/run/${tier}`)).data;
}

export async function resetBreaker() {
  return (await api.post<{ ok: boolean }>("/watchdog/circuit-breaker/reset")).data;
}
