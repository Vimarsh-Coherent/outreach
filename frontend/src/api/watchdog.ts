import { api } from "./client";

export interface WatchdogEvent {
  tier: string;
  status: "healthy" | "issue" | "emergency" | "healed" | "pattern_change";
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
  qdrant_alive: boolean;

  email_channels_healthy: number;
  email_channels_broken: number;
  linkedin_channels_alive: number;
  linkedin_channels_stale: number;

  stuck_step_runs: number;
  stuck_li_commands: number;
  stuck_enrolments: number;
  unclassified_events: number;

  awaiting_acceptance: number;
  awaiting_past_deadline: number;
  acceptance_detection_stalled: boolean;
  connection_accepts_today: number;
  last_connection_accept_at: string | null;

  detections_today: number;
  linkedin_command_failures_today: number;
  linkedin_command_successes_today: number;
  li_last_success_at: string | null;
  li_last_failure_at: string | null;
  // Per-intent DOM-failure counters (rolling 24h) — e.g. "messageButton_not_found_even_after_heal": 3.
  // An intent appearing in li_fragile_intents means LinkedIn's DOM has likely
  // changed pattern for that element and the watchdog is auto-healing it.
  li_failure_by_intent: Record<string, number>;
  li_fragile_intents: string[];

  consecutive_failures: number;
  disabled_until: string | null;

  events: WatchdogEvent[];
}

export interface SelectorRegistryEntry {
  channel_id: number;
  intent: string;
  selectors: string[];
  source: string;
  heal_count: number;
  updated_at: string;
}

export type WatchdogTier =
  | "quick_check" | "channel_patrol" | "stuck_state_sweep" | "deep_verify" | "daily_reset";

export async function getWatchdogState() {
  return (await api.get<WatchdogState>("/watchdog/state")).data;
}

export async function getSelectorRegistry() {
  return (await api.get<SelectorRegistryEntry[]>("/watchdog/selector-registry")).data;
}

export async function runTier(tier: WatchdogTier) {
  return (await api.post<{ tier: string; result: any }>(`/watchdog/run/${tier}`)).data;
}

export async function resetBreaker() {
  return (await api.post<{ ok: boolean }>("/watchdog/circuit-breaker/reset")).data;
}
