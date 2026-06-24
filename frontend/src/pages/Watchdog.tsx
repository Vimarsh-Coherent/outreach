import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  WatchdogEvent,
  WatchdogTier,
  getWatchdogState,
  resetBreaker,
  runTier,
} from "../api/watchdog";

const TIERS: { id: WatchdogTier; label: string; cadence: string; description: string }[] = [
  { id: "quick_check",        label: "Quick check",     cadence: "every 5 min",  description: "Backend health + DB reachability" },
  { id: "channel_patrol",     label: "Channel patrol",  cadence: "every 30 min", description: "Live SMTP / IMAP probes + extension heartbeat freshness" },
  { id: "stuck_state_sweep",  label: "Stuck-state sweep", cadence: "every 1 hour",  description: "Auto-heals stuck LinkedIn commands + re-queues unclassified replies" },
  { id: "deep_verify",        label: "Deep verify",     cadence: "every 6 hours", description: "LLM (active provider) + OpenAI + Qdrant liveness" },
  { id: "daily_reset",        label: "Daily reset",     cadence: "every 24 hours", description: "Roll cap windows, daily summary" },
];

const STATUS_STYLE: Record<string, string> = {
  healthy:   "text-emerald-700 bg-emerald-50  border-emerald-200",
  issue:     "text-amber-800   bg-amber-50    border-amber-200",
  emergency: "text-rose-800    bg-rose-50     border-rose-200",
  healed:    "text-violet-800  bg-violet-50   border-violet-200",
};

function StatusDot({ ok }: { ok: boolean }) {
  return <span className={`inline-block w-2.5 h-2.5 rounded-full ${ok ? "bg-emerald-500" : "bg-rose-500"}`} />;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export default function Watchdog() {
  const qc = useQueryClient();
  const { data: state } = useQuery({
    queryKey: ["watchdog-state"],
    queryFn: getWatchdogState,
    refetchInterval: 10_000,
  });

  const runMut = useMutation({
    mutationFn: async (tier: WatchdogTier) => runTier(tier),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchdog-state"] }),
  });
  const resetMut = useMutation({
    mutationFn: async () => resetBreaker(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchdog-state"] }),
  });

  const breakerOpen = !!state?.disabled_until && new Date(state.disabled_until) > new Date();

  return (
    <div className="space-y-6 max-w-7xl">
      <div className="flex items-end justify-between">
        <div>
          <h2 className="page-title">Watchdog</h2>
          <p className="text-sm text-slate-500 mt-1">
            5-tier patrol daemon that monitors the platform's health and auto-heals known failure modes (stuck LinkedIn commands, missing sentiment classifications, expired cap windows).
            Inspired by watchlink-main's selector-healing watchdog.
          </p>
        </div>
        {breakerOpen && (
          <button
            onClick={() => resetMut.mutate()}
            className="btn-primary btn-sm bg-rose-600 hover:bg-rose-700"
          >
            Circuit breaker OPEN — click to reset
          </button>
        )}
      </div>

      <section className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="card card-pad">
          <div className="stat-label">Backend + DB</div>
          <div className="mt-2 flex items-center gap-3"><StatusDot ok={!!state?.db_alive} /><span className="text-sm">{state?.db_alive ? "Online" : "Down"}</span></div>
        </div>
        <div className="card card-pad">
          <div className="stat-label">LLM (active provider)</div>
          <div className="mt-2 flex items-center gap-3"><StatusDot ok={!!state?.anthropic_alive} /><span className="text-sm">{state?.anthropic_alive ? "Reachable" : "Unverified"}</span></div>
        </div>
        <div className="card card-pad">
          <div className="stat-label">OpenAI</div>
          <div className="mt-2 flex items-center gap-3"><StatusDot ok={!!state?.openai_alive} /><span className="text-sm">{state?.openai_alive ? "Reachable" : "Unverified"}</span></div>
        </div>
        <div className="card card-pad">
          <div className="stat-label">Qdrant</div>
          <div className="mt-2 flex items-center gap-3"><StatusDot ok={!!state?.qdrant_alive} /><span className="text-sm">{state?.qdrant_alive ? "Reachable" : "Unverified"}</span></div>
        </div>
        <div className="card card-pad">
          <div className="stat-label">Consecutive fails</div>
          <div className="mt-2 text-xl font-semibold">{state?.consecutive_failures ?? 0} / 5</div>
        </div>
      </section>

      <section className="grid md:grid-cols-2 gap-4">
        <div className="card card-pad">
          <h3 className="font-semibold mb-3">Channels</h3>
          <table className="w-full text-sm">
            <tbody>
              <tr><td className="py-1 text-slate-500">Email — healthy</td><td className="py-1 text-right font-mono">{state?.email_channels_healthy ?? 0}</td></tr>
              <tr><td className="py-1 text-slate-500">Email — broken</td><td className="py-1 text-right font-mono text-rose-700">{state?.email_channels_broken ?? 0}</td></tr>
              <tr><td className="py-1 text-slate-500">LinkedIn — extension alive</td><td className="py-1 text-right font-mono">{state?.linkedin_channels_alive ?? 0}</td></tr>
              <tr><td className="py-1 text-slate-500">LinkedIn — heartbeat stale</td><td className="py-1 text-right font-mono text-amber-700">{state?.linkedin_channels_stale ?? 0}</td></tr>
              <tr><td className="py-1 text-slate-500">LinkedIn — command failures today</td><td className={`py-1 text-right font-mono ${(state?.linkedin_command_failures_today ?? 0) > 0 ? 'text-rose-700' : ''}`}>{state?.linkedin_command_failures_today ?? 0}</td></tr>
            </tbody>
          </table>
        </div>

        <div className="card card-pad">
          <h3 className="font-semibold mb-3">Backlog & stuck state</h3>
          <table className="w-full text-sm">
            <tbody>
              <tr><td className="py-1 text-slate-500">step_runs stuck in reserving &gt;10m</td><td className="py-1 text-right font-mono">{state?.stuck_step_runs ?? 0}</td></tr>
              <tr><td className="py-1 text-slate-500">li_commands stuck claimed &gt;10m (auto-heals)</td><td className="py-1 text-right font-mono">{state?.stuck_li_commands ?? 0}</td></tr>
              <tr><td className="py-1 text-slate-500">enrolments stuck &gt;24h</td><td className="py-1 text-right font-mono text-amber-700">{state?.stuck_enrolments ?? 0}</td></tr>
              <tr><td className="py-1 text-slate-500">events without sentiment (auto-requeues)</td><td className="py-1 text-right font-mono">{state?.unclassified_events ?? 0}</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section className="card card-pad">
        <h3 className="font-semibold mb-3">Patrol tiers</h3>
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th">Tier</th>
              <th className="th">Cadence</th>
              <th className="th">Last ran</th>
              <th className="th">What it does</th>
              <th className="th"></th>
            </tr>
          </thead>
          <tbody>
            {TIERS.map(t => {
              const lastKey = ({
                quick_check: "last_quick_check",
                channel_patrol: "last_channel_patrol",
                stuck_state_sweep: "last_stuck_sweep",
                deep_verify: "last_deep_verify",
                daily_reset: "last_daily_reset",
              } as const)[t.id];
              const last = state ? (state as any)[lastKey] : null;
              return (
                <tr key={t.id} className="border-t border-slate-100">
                  <td className="td font-medium">{t.label}</td>
                  <td className="td text-xs text-slate-500">{t.cadence}</td>
                  <td className="td text-xs font-mono">{timeAgo(last)}</td>
                  <td className="td text-xs text-slate-500">{t.description}</td>
                  <td className="td text-right">
                    <button
                      onClick={() => runMut.mutate(t.id)}
                      disabled={runMut.isPending}
                      className="btn-ghost btn-sm"
                    >Run now</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <section className="card card-pad">
        <h3 className="font-semibold mb-3">Recent events ({state?.events.length ?? 0})</h3>
        {!state?.events.length ? (
          <p className="text-sm text-slate-500">No events yet. Click "Run now" on a tier above to trigger one, or wait for the next scheduled patrol.</p>
        ) : (
          <ul className="space-y-1.5">
            {state.events.map((e: WatchdogEvent, i: number) => (
              <li key={i} className={`border rounded-lg p-2 text-xs ${STATUS_STYLE[e.status] ?? ""}`}>
                <div className="flex items-center justify-between">
                  <div className="font-medium">{e.tier} · <span className="uppercase">{e.status}</span></div>
                  <div className="text-slate-500 font-mono">{timeAgo(e.at)}</div>
                </div>
                <div className="mt-1">{e.message}</div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
