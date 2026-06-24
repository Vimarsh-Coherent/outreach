import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import HubspotCard from "../components/HubspotCard";
import {
  createApiKey,
  createWebhook,
  deleteApiKey,
  deleteWebhook,
  getWebhookEventTypes,
  listApiKeys,
  listWebhooks,
  testWebhook,
} from "../api/integrations";

export default function Integrations() {
  const qc = useQueryClient();
  const oauthResult = new URLSearchParams(window.location.search).get("hubspot");

  // ── Webhooks ──────────────────────────────────────────────────────────────
  const { data: eventTypes } = useQuery({ queryKey: ["wh-event-types"], queryFn: getWebhookEventTypes, staleTime: Infinity });
  const { data: webhooks } = useQuery({ queryKey: ["webhooks"], queryFn: listWebhooks });
  const [url, setUrl] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [testResult, setTestResult] = useState<Record<number, string>>({});

  const createWhMut = useMutation({
    mutationFn: async () => createWebhook({ url, event_types: selected }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["webhooks"] }); setUrl(""); setSelected([]); },
  });
  const delWhMut = useMutation({ mutationFn: deleteWebhook, onSuccess: () => qc.invalidateQueries({ queryKey: ["webhooks"] }) });
  const testWhMut = useMutation({
    mutationFn: testWebhook,
    onSuccess: (r, id) => setTestResult(t => ({ ...t, [id]: r.ok ? `OK (${r.status})` : `failed: ${r.error ?? r.status}` })),
  });

  // ── API keys ──────────────────────────────────────────────────────────────
  const { data: keys } = useQuery({ queryKey: ["api-keys"], queryFn: listApiKeys });
  const [keyLabel, setKeyLabel] = useState("");
  const [newKey, setNewKey] = useState<string | null>(null);
  const createKeyMut = useMutation({
    mutationFn: async () => createApiKey(keyLabel),
    onSuccess: (r) => { setNewKey(r.raw_key); setKeyLabel(""); qc.invalidateQueries({ queryKey: ["api-keys"] }); },
  });
  const delKeyMut = useMutation({ mutationFn: deleteApiKey, onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }) });

  const toggle = (t: string) => setSelected(s => s.includes(t) ? s.filter(x => x !== t) : [...s, t]);

  return (
    <div className="space-y-8 max-w-5xl">
      <div>
        <h2 className="page-title">Integrations</h2>
        <p className="text-sm text-slate-500 mt-1">
          Connect a CRM directly (HubSpot), sync events to any CRM via webhooks (Zapier / Make / n8n), or use the public API.
        </p>
      </div>

      {oauthResult === "connected" && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">HubSpot connected ✓</div>
      )}
      {oauthResult === "error" && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">HubSpot connection failed — check the app config and try again.</div>
      )}

      {/* Native CRM */}
      <HubspotCard />

      {/* Webhooks */}
      <section className="card">
        <div className="card-head"><h3 className="font-semibold text-slate-900">Webhooks</h3></div>
        <div className="card-pad space-y-4">
          <div className="grid grid-cols-12 gap-3 items-end">
            <label className="col-span-8"><span className="label">Endpoint URL</span>
              <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://hooks.zapier.com/…" className="input" />
            </label>
            <div className="col-span-4 flex justify-end">
              <button disabled={!url.startsWith("http") || selected.length === 0 || createWhMut.isPending}
                onClick={() => createWhMut.mutate()} className="btn-primary">
                {createWhMut.isPending ? "Adding…" : "Add webhook"}
              </button>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {(eventTypes ?? []).map(t => (
              <button key={t} type="button" onClick={() => toggle(t)}
                className={`badge ${selected.includes(t) ? "badge-brand" : "badge-slate"}`}>{t}</button>
            ))}
          </div>

          {!webhooks?.length ? (
            <p className="text-sm text-slate-500">No webhooks yet. Add one above and we'll POST signed events to it.</p>
          ) : (
            <table className="w-full">
              <thead><tr><th className="th">URL</th><th className="th">Events</th><th className="th">Signing secret</th><th className="th"></th></tr></thead>
              <tbody>
                {webhooks.map(w => (
                  <tr key={w.id} className="border-t border-slate-100">
                    <td className="td font-mono text-xs break-all">{w.url}</td>
                    <td className="td text-xs">{w.event_types.join(", ")}</td>
                    <td className="td font-mono text-[11px] text-slate-500 break-all">{w.secret}</td>
                    <td className="td whitespace-nowrap">
                      <button onClick={() => testWhMut.mutate(w.id)} className="text-brand-600 hover:underline text-xs mr-3">test</button>
                      <button onClick={() => delWhMut.mutate(w.id)} className="text-rose-600 hover:underline text-xs">delete</button>
                      {testResult[w.id] && <span className="ml-2 text-xs text-slate-500">{testResult[w.id]}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-xs text-slate-400">
            Each POST is signed: <code className="bg-slate-100 px-1 rounded">X-Coherent-Signature: sha256=HMAC(secret, body)</code>. Verify it on your receiver.
          </p>
        </div>
      </section>

      {/* API keys */}
      <section className="card">
        <div className="card-head"><h3 className="font-semibold text-slate-900">API keys</h3></div>
        <div className="card-pad space-y-4">
          <div className="grid grid-cols-12 gap-3 items-end">
            <label className="col-span-8"><span className="label">Label</span>
              <input value={keyLabel} onChange={e => setKeyLabel(e.target.value)} placeholder="e.g. Zapier" className="input" />
            </label>
            <div className="col-span-4 flex justify-end">
              <button disabled={createKeyMut.isPending} onClick={() => createKeyMut.mutate()} className="btn-primary">
                {createKeyMut.isPending ? "Creating…" : "Create key"}
              </button>
            </div>
          </div>

          {newKey && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
              <p className="font-medium text-amber-900">Copy this key now — it won't be shown again:</p>
              <code className="mt-1 block break-all font-mono text-xs text-amber-900">{newKey}</code>
            </div>
          )}

          {!keys?.length ? (
            <p className="text-sm text-slate-500">No API keys yet.</p>
          ) : (
            <table className="w-full">
              <thead><tr><th className="th">Label</th><th className="th">Prefix</th><th className="th">Last used</th><th className="th"></th></tr></thead>
              <tbody>
                {keys.map(k => (
                  <tr key={k.id} className="border-t border-slate-100">
                    <td className="td">{k.label || <span className="text-slate-400">—</span>}</td>
                    <td className="td font-mono text-xs">{k.prefix}…</td>
                    <td className="td text-xs text-slate-500">{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "never"}</td>
                    <td className="td"><button onClick={() => delKeyMut.mutate(k.id)} className="text-rose-600 hover:underline text-xs">revoke</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-xs text-slate-400">
            Use with <code className="bg-slate-100 px-1 rounded">X-Api-Key</code> against <code className="bg-slate-100 px-1 rounded">/api/v1/leads</code> and <code className="bg-slate-100 px-1 rounded">/api/v1/events</code>.
          </p>
        </div>
      </section>
    </div>
  );
}
