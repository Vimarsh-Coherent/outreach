import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getHubspotStatus,
  hubspotConnectUrl,
  hubspotDisconnect,
  hubspotSyncContacts,
} from "../api/crm";

export default function HubspotCard() {
  const qc = useQueryClient();
  const { data: s } = useQuery({ queryKey: ["hubspot-status"], queryFn: getHubspotStatus, refetchInterval: 5000 });

  const connectMut = useMutation({
    mutationFn: hubspotConnectUrl,
    onSuccess: (url) => { window.location.href = url; },  // off to HubSpot's OAuth
    onError: (e) => alert(String((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? e)),
  });
  const disconnectMut = useMutation({
    mutationFn: hubspotDisconnect,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["hubspot-status"] }),
  });
  const syncMut = useMutation({
    mutationFn: hubspotSyncContacts,
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["hubspot-status"] });
      alert(`Pulled ${r.pulled} contacts — ${r.inserted} new, ${r.updated} updated.`);
    },
    onError: (e) => alert(String((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? e)),
  });

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="font-semibold text-slate-900 flex items-center gap-2">
          <span className="text-orange-500">⬢</span> HubSpot
        </h3>
        {s?.connected
          ? <span className="badge-green">Connected</span>
          : s?.configured
          ? <span className="badge-slate">Not connected</span>
          : <span className="badge-amber">App not set up</span>}
      </div>
      <div className="card-pad space-y-3">
        <p className="text-sm text-slate-500">
          Direct two-way sync: pull HubSpot contacts in as leads, and log every reply (with sentiment) back onto the contact in HubSpot.
        </p>

        {!s?.configured && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 space-y-1">
            <p className="font-medium">To enable: create a HubSpot app, then set these in <code className="bg-white px-1 rounded">backend/.env</code> and restart:</p>
            <pre className="whitespace-pre-wrap font-mono">HUBSPOT_CLIENT_ID=...
HUBSPOT_CLIENT_SECRET=...</pre>
            <p>Set the app's redirect URL to <code className="bg-white px-1 rounded">http://localhost:8000/api/crm/hubspot/callback</code> and add scopes <code className="bg-white px-1 rounded">crm.objects.contacts.read/write</code>.</p>
          </div>
        )}

        {s?.connected ? (
          <div className="space-y-3">
            <p className="text-sm text-emerald-800">
              Connected{s.portal_name ? ` to ${s.portal_name}` : ""}.
              {s.last_synced_at && <span className="text-slate-500"> · last sync {new Date(s.last_synced_at).toLocaleString()}</span>}
            </p>
            <div className="flex items-center gap-2">
              <button disabled={syncMut.isPending} onClick={() => syncMut.mutate()} className="btn-primary btn-sm">
                {syncMut.isPending ? "Syncing…" : "Sync contacts in"}
              </button>
              <button disabled={disconnectMut.isPending} onClick={() => { if (confirm("Disconnect HubSpot?")) disconnectMut.mutate(); }} className="btn-ghost btn-sm">
                Disconnect
              </button>
            </div>
          </div>
        ) : s?.configured ? (
          <button disabled={connectMut.isPending} onClick={() => connectMut.mutate()} className="btn-primary">
            {connectMut.isPending ? "Redirecting…" : "Connect HubSpot"}
          </button>
        ) : null}
      </div>
    </section>
  );
}
