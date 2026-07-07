import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  SelectorRegistryEntry,
  WatchdogEvent,
  WatchdogTier,
  getSelectorRegistry,
  getWatchdogState,
  resetBreaker,
  runTier,
} from "../api/watchdog";

const TIERS: { id: WatchdogTier; label: string; cadence: string; description: string }[] = [
  { id: "quick_check",        label: "Quick check",     cadence: "every 5 min",  description: "Backend health + DB reachability" },
  { id: "channel_patrol",     label: "Channel patrol",  cadence: "every 30 min", description: "Live SMTP / IMAP probes + extension heartbeat freshness" },
  { id: "stuck_state_sweep",  label: "Stuck-state sweep", cadence: "every 1 hour",  description: "Auto-heals stuck LinkedIn commands + re-queues unclassified replies" },
  { id: "deep_verify",        label: "Deep verify",     cadence: "every 6 hours", description: "Anthropic + OpenAI + Qdrant liveness" },
  { id: "daily_reset",        label: "Daily reset",     cadence: "every 24 hours", description: "Roll cap windows, daily summary" },
];

const STATUS_STYLE: Record<string, string> = {
  healthy:        "text-emerald-700 bg-emerald-50  border-emerald-200",
  issue:          "text-amber-800   bg-amber-50    border-amber-200",
  emergency:      "text-rose-800    bg-rose-50     border-rose-200",
  healed:         "text-violet-800  bg-violet-50   border-violet-200",
  pattern_change: "text-orange-900  bg-orange-50   border-orange-300",
};

function intentLabel(key: string): string {
  return key.replace(/_/g, " ");
}

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
  const { data: selectorRegistry } = useQuery({
    queryKey: ["watchdog-selector-registry"],
    queryFn: getSelectorRegistry,
    refetchInterval: 15_000,
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
          <h2 className="text-2xl font-semibold">Watchdog</h2>
          <p className="text-sm text-slate-500 mt-1">
            5-tier patrol daemon that monitors the platform's health and auto-heals known failure modes (stuck LinkedIn commands, missing sentiment classifications, expired cap windows).
            Inspired by watchlink-main's selector-healing watchdog.
          </p>
        </div>
        {breakerOpen && (
          <button
            onClick={() => resetMut.mutate()}
            className="border rounded px-3 py-1.5 bg-rose-600 text-white hover:bg-rose-700"
          >
            Circuit breaker OPEN — click to reset
          </button>
        )}
      </div>

      <section className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <div className="rounded border bg-white p-3">
          <div className="text-xs text-slate-500 uppercase">Backend + DB</div>
          <div className="mt-2 flex items-center gap-3"><StatusDot ok={!!state?.db_alive} /><span className="text-sm">{state?.db_alive ? "Online" : "Down"}</span></div>
        </div>
        <div className="rounded border bg-white p-3">
          <div className="text-xs text-slate-500 uppercase">Anthropic</div>
          <div className="mt-2 flex items-center gap-3"><StatusDot ok={!!state?.anthropic_alive} /><span className="text-sm">{state?.anthropic_alive ? "Reachable" : "Unverified"}</span></div>
        </div>
        <div className="rounded border bg-white p-3">
          <div className="text-xs text-slate-500 uppercase">OpenAI</div>
          <div className="mt-2 flex items-center gap-3"><StatusDot ok={!!state?.openai_alive} /><span className="text-sm">{state?.openai_alive ? "Reachable" : "Unverified"}</span></div>
        </div>
        <div className="rounded border bg-white p-3">
          <div className="text-xs text-slate-500 uppercase">Qdrant</div>
          <div className="mt-2 flex items-center gap-3"><StatusDot ok={!!state?.qdrant_alive} /><span className="text-sm">{state?.qdrant_alive ? "Reachable" : "Unverified"}</span></div>
        </div>
        <div className="rounded border bg-white p-3">
          <div className="text-xs text-slate-500 uppercase">Consecutive fails</div>
          <div className="mt-2 text-xl font-semibold">{state?.consecutive_failures ?? 0} / 5</div>
        </div>
      </section>

      <section className="grid md:grid-cols-2 gap-4">
        <div className="rounded border bg-white p-4">
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

        <div className="rounded border bg-white p-4">
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

      <section className="rounded border bg-white p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold">LinkedIn selector health</h3>
          <span className="text-xs text-slate-400">
            Auto-detects when LinkedIn changes its page layout and rebuilds selectors via AI heal
          </span>
        </div>

        {!!state?.li_fragile_intents.length && (
          <div className="mb-3 rounded border border-orange-300 bg-orange-50 p-3 text-sm text-orange-900">
            <div className="font-semibold">⚠ Pattern change suspected</div>
            <div className="mt-1">
              These elements have failed repeatedly in the last 24h — the watchdog is auto-healing them and
              pushing fresh selectors to the extension:
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {state.li_fragile_intents.map(k => (
                <span key={k} className="inline-block rounded-full border border-orange-300 bg-white px-2 py-0.5 text-xs font-mono">
                  {intentLabel(k)}
                </span>
              ))}
            </div>
          </div>
        )}

        {!!state && Object.keys(state.li_failure_by_intent).length > 0 && (
          <div className="mb-4">
            <div className="text-xs text-slate-500 uppercase mb-1.5">Failure counts (rolling 24h)</div>
            <table className="w-full text-sm">
              <tbody>
                {Object.entries(state.li_failure_by_intent)
                  .sort(([, a], [, b]) => b - a)
                  .map(([key, count]) => (
                    <tr key={key} className="border-t">
                      <td className="py-1 text-slate-600 font-mono text-xs">{intentLabel(key)}</td>
                      <td className={`py-1 text-right font-mono ${count >= 2 ? "text-orange-700 font-semibold" : ""}`}>{count}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}

        <div>
          <div className="text-xs text-slate-500 uppercase mb-1.5">Current selector registry (server-persisted)</div>
          {!selectorRegistry?.length ? (
            <p className="text-sm text-slate-500">
              No selectors auto-healed yet. When LinkedIn changes its layout, healed selectors will appear here and sync
              to the extension automatically.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-slate-500">
                  <tr>
                    <th className="py-1 pr-3">Intent</th>
                    <th className="py-1 pr-3">Current selector</th>
                    <th className="py-1 pr-3">Source</th>
                    <th className="py-1 pr-3 text-right">Heal count</th>
                    <th className="py-1 pr-3">Last updated</th>
                  </tr>
                </thead>
                <tbody>
                  {selectorRegistry.map((r: SelectorRegistryEntry) => (
                    <tr key={`${r.channel_id}-${r.intent}`} className="border-t">
                      <td className="py-1.5 pr-3 font-medium">{r.intent}</td>
                      <td className="py-1.5 pr-3 font-mono text-xs text-slate-600">{r.selectors[0]}</td>
                      <td className="py-1.5 pr-3 text-xs">
                        <span className={`px-1.5 py-0.5 rounded ${r.source === "preemptive" ? "bg-violet-100 text-violet-800" : "bg-slate-100 text-slate-600"}`}>
                          {r.source}
                        </span>
                      </td>
                      <td className={`py-1.5 pr-3 text-right font-mono ${r.heal_count >= 2 ? "text-orange-700 font-semibold" : ""}`}>{r.heal_count}</td>
                      <td className="py-1.5 pr-3 text-xs font-mono text-slate-500">{timeAgo(r.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <section className="rounded border bg-white p-4">
        <h3 className="font-semibold mb-3">Patrol tiers</h3>
        <table className="w-full text-sm">
          <thead className="text-left text-slate-500">
            <tr>
              <th className="py-1 pr-3">Tier</th>
              <th className="py-1 pr-3">Cadence</th>
              <th className="py-1 pr-3">Last ran</th>
              <th className="py-1 pr-3">What it does</th>
              <th></th>
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
                <tr key={t.id} className="border-t">
                  <td className="py-2 pr-3 font-medium">{t.label}</td>
                  <td className="py-2 pr-3 text-xs text-slate-500">{t.cadence}</td>
                  <td className="py-2 pr-3 text-xs font-mono">{timeAgo(last)}</td>
                  <td className="py-2 pr-3 text-xs text-slate-500">{t.description}</td>
                  <td className="py-2 text-right">
                    <button
                      onClick={() => runMut.mutate(t.id)}
                      disabled={runMut.isPending}
                      className="border rounded px-2 py-1 text-xs bg-slate-50 hover:bg-slate-100"
                    >Run now</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <section className="rounded border bg-white p-4">
        <h3 className="font-semibold mb-3">Recent events ({state?.events.length ?? 0})</h3>
        {!state?.events.length ? (
          <p className="text-sm text-slate-500">No events yet. Click "Run now" on a tier above to trigger one, or wait for the next scheduled patrol.</p>
        ) : (
          <ul className="space-y-1.5">
            {state.events.map((e: WatchdogEvent, i: number) => (
              <li key={i} className={`border rounded p-2 text-xs ${STATUS_STYLE[e.status] ?? ""}`}>
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
