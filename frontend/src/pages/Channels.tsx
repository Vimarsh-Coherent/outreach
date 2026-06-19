import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  ChannelOut,
  EmailPreset,
  IMAPConfig,
  SMTPConfig,
  SecurityMode,
  TestEmailChannelResponse,
  createEmailChannel,
  createWhatsAppChannel,
  deleteChannel,
  getEmailPresets,
  getWhatsAppQr,
  getWhatsAppStatus,
  listChannels,
  logoutWhatsApp,
  testEmailChannel,
} from "../api/channels";

const blankSmtp: SMTPConfig = {
  host: "",
  port: 587,
  security: "starttls",
  username: "",
  password: "",
  from_email: "",
  from_name: "",
  timeout_seconds: 30,
};

const blankImap: IMAPConfig = {
  host: "",
  port: 993,
  security: "ssl_tls",
  username: "",
  password: "",
  mailbox: "INBOX",
  timeout_seconds: 30,
};

function StepBadge({ result, label }: { result: { ok: boolean; detail: string; latency_ms?: number | null } | null | undefined; label: string }) {
  if (!result) return null;
  return (
    <div className={`flex items-start gap-2 text-sm ${result.ok ? "text-emerald-700" : "text-rose-700"}`}>
      <span className="font-mono text-xs mt-0.5">{result.ok ? "✓" : "✗"}</span>
      <div>
        <div className="font-medium">{label}{result.latency_ms != null && <span className="text-slate-500 font-normal"> · {result.latency_ms} ms</span>}</div>
        <div className="text-slate-600 font-mono text-xs break-all">{result.detail}</div>
      </div>
    </div>
  );
}

const WA_STATE_LABEL: Record<string, { text: string; cls: string }> = {
  connected: { text: "Connected", cls: "badge-green" },
  qr: { text: "Scan QR to link", cls: "badge-brand" },
  starting: { text: "Starting…", cls: "badge-slate" },
  disconnected: { text: "Reconnecting…", cls: "badge-amber" },
  logged_out: { text: "Logged out — re-scan", cls: "badge-amber" },
  unavailable: { text: "Sidecar offline", cls: "badge-rose" },
};

function WhatsAppCard() {
  const qc = useQueryClient();
  const [label, setLabel] = useState("WhatsApp");
  const [cap, setCap] = useState(100);
  const [waError, setWaError] = useState<string | null>(null);

  const { data: status } = useQuery({
    queryKey: ["wa-status"],
    queryFn: getWhatsAppStatus,
    refetchInterval: 3000,
  });
  const connected = status?.state === "connected";
  // Poll the QR only while pairing (not connected, sidecar reachable).
  const { data: qr } = useQuery({
    queryKey: ["wa-qr"],
    queryFn: getWhatsAppQr,
    refetchInterval: 2500,
    enabled: !!status && !connected && status.state !== "unavailable",
  });

  const createMut = useMutation({
    mutationFn: async () => createWhatsAppChannel({ display_label: label, daily_cap: cap }),
    onSuccess: () => {
      setWaError(null);
      qc.invalidateQueries({ queryKey: ["channels"] });
      qc.invalidateQueries({ queryKey: ["wa-status"] });
    },
    onError: (e) => setWaError(String(e)),
  });

  const logoutMut = useMutation({
    mutationFn: async () => logoutWhatsApp(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["wa-status"] });
      qc.invalidateQueries({ queryKey: ["wa-qr"] });
    },
    onError: (e) => setWaError(String(e)),
  });

  const state = status?.state ?? "starting";
  const badge = WA_STATE_LABEL[state] ?? WA_STATE_LABEL.starting;

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="font-semibold text-slate-800 flex items-center gap-2">
          <span className="text-emerald-600">●</span> WhatsApp
        </h3>
        <span className={badge.cls}>{badge.text}</span>
      </div>
      <div className="card-pad space-y-4">
      <p className="text-sm text-slate-500">
        Links a real WhatsApp number via the local sidecar (no Business API). Open WhatsApp on your
        phone → <span className="font-medium">Settings → Linked devices → Link a device</span>, then scan the code below.
        Sends go through the same caps + send-window as email; keep volume conservative to avoid number bans.
      </p>

      {state === "unavailable" && (
        <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg p-3">
          The WhatsApp sidecar isn’t running. The boot agent starts it automatically when online —
          or run it manually: <code className="bg-white px-1 rounded">node whatsapp-sidecar/server.js</code>.
        </div>
      )}

      {!connected && state !== "unavailable" && (
        <div className="flex flex-col items-center gap-3 py-2">
          {qr?.qr ? (
            <img src={qr.qr} alt="WhatsApp QR" className="w-56 h-56 border rounded-lg bg-white" />
          ) : (
            <div className="w-56 h-56 border rounded-lg bg-slate-50 flex items-center justify-center text-sm text-slate-400">
              {state === "logged_out" ? "Generating fresh QR…" : "Waiting for QR…"}
            </div>
          )}
          <p className="text-xs text-slate-500">The code refreshes automatically until you scan it.</p>
        </div>
      )}

      {connected && (
        <div className="space-y-3">
          <div className="text-sm text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-lg p-3">
            Connected{status?.me ? ` as ${status.me.split(":")[0].split("@")[0]}` : ""}.
            {status?.channel_id
              ? " WhatsApp steps in your sequences will send through this number."
              : " Create the channel below to use WhatsApp in sequences."}
          </div>
          {!status?.channel_id && (
            <div className="grid grid-cols-12 gap-3 items-end text-sm">
              <label className="col-span-6"><span className="label">Display label</span>
                <input value={label} onChange={(e) => setLabel(e.target.value)} className="input" />
              </label>
              <label className="col-span-3"><span className="label">Daily cap</span>
                <input type="number" value={cap} onChange={(e) => setCap(Number(e.target.value))} className="input" />
              </label>
              <div className="col-span-3 flex justify-end">
                <button
                  disabled={createMut.isPending}
                  onClick={() => createMut.mutate()}
                  className="btn-primary btn-sm"
                >{createMut.isPending ? "Saving…" : "Create channel"}</button>
              </div>
            </div>
          )}
          <button
            disabled={logoutMut.isPending}
            onClick={() => { if (confirm("Disconnect this WhatsApp number? You’ll need to re-scan the QR.")) logoutMut.mutate(); }}
            className="text-rose-600 hover:underline text-xs"
          >{logoutMut.isPending ? "Disconnecting…" : "Disconnect / re-link a different number"}</button>
        </div>
      )}

      {waError && <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg p-2">{waError}</div>}
      </div>
    </section>
  );
}

export default function Channels() {
  const qc = useQueryClient();

  const { data: presets } = useQuery({ queryKey: ["email-presets"], queryFn: getEmailPresets, staleTime: Infinity });
  const { data: channels } = useQuery({ queryKey: ["channels"], queryFn: listChannels });

  const [presetKey, setPresetKey] = useState<string>("gmail");
  const [displayLabel, setDisplayLabel] = useState("My Gmail");
  const [smtp, setSmtp] = useState<SMTPConfig>(blankSmtp);
  const [imap, setImap] = useState<IMAPConfig>(blankImap);
  const [imapEnabled, setImapEnabled] = useState(true);
  const [shareCreds, setShareCreds] = useState(true);
  const [probeTo, setProbeTo] = useState("");
  const [dailyCap, setDailyCap] = useState(100);
  const [testResult, setTestResult] = useState<TestEmailChannelResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const currentPreset = useMemo<EmailPreset | undefined>(
    () => presets?.find(p => p.key === presetKey),
    [presets, presetKey],
  );

  useEffect(() => {
    if (!currentPreset) return;
    setSmtp(s => ({
      ...s,
      host: currentPreset.smtp_host,
      port: currentPreset.smtp_port,
      security: currentPreset.smtp_security,
    }));
    setImap(i => ({
      ...i,
      host: currentPreset.imap_host,
      port: currentPreset.imap_port,
      security: currentPreset.imap_security,
    }));
    setImapEnabled(currentPreset.imap_host !== "");
  }, [currentPreset]);

  useEffect(() => {
    if (shareCreds) {
      setImap(i => ({ ...i, username: smtp.username, password: smtp.password }));
    }
  }, [shareCreds, smtp.username, smtp.password]);

  const testMut = useMutation({
    mutationFn: async () => {
      setError(null);
      return testEmailChannel({
        smtp,
        imap: imapEnabled ? imap : null,
        send_probe_to: probeTo || null,
      });
    },
    onSuccess: r => setTestResult(r),
    onError: e => { setError(String(e)); setTestResult(null); },
  });

  const saveMut = useMutation({
    mutationFn: async () => {
      setError(null);
      return createEmailChannel({
        display_label: displayLabel,
        smtp,
        imap: imapEnabled ? imap : null,
        daily_cap: dailyCap,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["channels"] });
      setTestResult(null);
    },
    onError: e => setError(String(e)),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: number) => deleteChannel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["channels"] }),
  });

  return (
    <div className="space-y-8 max-w-5xl">
      <div>
        <h2 className="page-title">Channels</h2>
        <p className="text-sm text-slate-500 mt-1">
          Connect any SMTP mailbox (Gmail, Outlook, Yahoo, Zoho, Microsoft 365, SendGrid/SES/Mailgun, or a custom server). IMAP is used to detect replies and bounces. Credentials are encrypted at rest (Fernet).
        </p>
      </div>

      <section className="card">
        <div className="card-head">
          <h3 className="font-semibold text-slate-800">Add email channel</h3>
        </div>
        <div className="card-pad space-y-5">

        <div className="grid grid-cols-2 gap-4">
          <label className="text-sm">
            <span className="label">Provider preset</span>
            <select
              value={presetKey}
              onChange={e => setPresetKey(e.target.value)}
              className="input"
            >
              {presets?.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </label>
          <label className="text-sm">
            <span className="label">Display label</span>
            <input
              value={displayLabel}
              onChange={e => setDisplayLabel(e.target.value)}
              className="input"
              placeholder="e.g. Sales — Gmail"
            />
          </label>
        </div>

        {currentPreset?.notes && (
          <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-2">{currentPreset.notes}</div>
        )}

        <div>
          <h4 className="font-medium text-slate-700 mb-2">SMTP (outbound)</h4>
          <div className="grid grid-cols-12 gap-3 text-sm">
            <label className="col-span-6"><span className="label">Host</span>
              <input value={smtp.host} onChange={e => setSmtp({ ...smtp, host: e.target.value })} className="input font-mono" />
            </label>
            <label className="col-span-2"><span className="label">Port</span>
              <input type="number" value={smtp.port} onChange={e => setSmtp({ ...smtp, port: Number(e.target.value) })} className="input" />
            </label>
            <label className="col-span-4"><span className="label">Security</span>
              <select value={smtp.security} onChange={e => setSmtp({ ...smtp, security: e.target.value as SecurityMode })} className="input">
                <option value="starttls">STARTTLS</option>
                <option value="ssl_tls">SSL/TLS</option>
                <option value="none">None (plain)</option>
              </select>
            </label>
            <label className="col-span-6"><span className="label">Username</span>
              <input value={smtp.username} onChange={e => setSmtp({ ...smtp, username: e.target.value })} className="input font-mono" autoComplete="off" />
            </label>
            <label className="col-span-6"><span className="label">Password / App password</span>
              <input type="password" value={smtp.password} onChange={e => setSmtp({ ...smtp, password: e.target.value })} className="input font-mono" autoComplete="new-password" />
            </label>
            <label className="col-span-7"><span className="label">From email</span>
              <input value={smtp.from_email} onChange={e => setSmtp({ ...smtp, from_email: e.target.value })} className="input font-mono" placeholder="me@example.com" />
            </label>
            <label className="col-span-5"><span className="label">From name (optional)</span>
              <input value={smtp.from_name ?? ""} onChange={e => setSmtp({ ...smtp, from_name: e.target.value })} className="input" placeholder="Vimarsh @ Coherent" />
            </label>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <h4 className="font-medium text-slate-700">IMAP (reply + bounce detection)</h4>
            <label className="text-xs text-slate-600 flex items-center gap-2">
              <input type="checkbox" checked={imapEnabled} onChange={e => setImapEnabled(e.target.checked)} />
              enable
            </label>
          </div>
          {imapEnabled && (
            <>
              <label className="text-xs text-slate-600 flex items-center gap-2 mt-2 mb-3">
                <input type="checkbox" checked={shareCreds} onChange={e => setShareCreds(e.target.checked)} />
                use same username/password as SMTP
              </label>
              <div className="grid grid-cols-12 gap-3 text-sm">
                <label className="col-span-6"><span className="label">Host</span>
                  <input value={imap.host} onChange={e => setImap({ ...imap, host: e.target.value })} className="input font-mono" />
                </label>
                <label className="col-span-2"><span className="label">Port</span>
                  <input type="number" value={imap.port} onChange={e => setImap({ ...imap, port: Number(e.target.value) })} className="input" />
                </label>
                <label className="col-span-4"><span className="label">Security</span>
                  <select value={imap.security} onChange={e => setImap({ ...imap, security: e.target.value as SecurityMode })} className="input">
                    <option value="ssl_tls">SSL/TLS</option>
                    <option value="starttls">STARTTLS</option>
                    <option value="none">None (plain)</option>
                  </select>
                </label>
                {!shareCreds && (
                  <>
                    <label className="col-span-6"><span className="label">Username</span>
                      <input value={imap.username} onChange={e => setImap({ ...imap, username: e.target.value })} className="input font-mono" autoComplete="off" />
                    </label>
                    <label className="col-span-6"><span className="label">Password</span>
                      <input type="password" value={imap.password} onChange={e => setImap({ ...imap, password: e.target.value })} className="input font-mono" autoComplete="new-password" />
                    </label>
                  </>
                )}
                <label className="col-span-6"><span className="label">Mailbox</span>
                  <input value={imap.mailbox} onChange={e => setImap({ ...imap, mailbox: e.target.value })} className="input font-mono" />
                </label>
                <label className="col-span-6"><span className="label">Daily send cap</span>
                  <input type="number" value={dailyCap} onChange={e => setDailyCap(Number(e.target.value))} className="input" />
                </label>
              </div>
            </>
          )}
        </div>

        <div className="grid grid-cols-12 gap-3 items-end text-sm">
          <label className="col-span-8"><span className="label">Send probe email to (optional)</span>
            <input value={probeTo} onChange={e => setProbeTo(e.target.value)} placeholder="yourself@example.com" className="input font-mono" />
          </label>
          <div className="col-span-4 flex gap-2 justify-end">
            <button
              disabled={testMut.isPending}
              onClick={() => testMut.mutate()}
              className="btn-ghost btn-sm"
            >{testMut.isPending ? "Testing..." : "Test"}</button>
            <button
              disabled={saveMut.isPending}
              onClick={() => saveMut.mutate()}
              className="btn-primary btn-sm"
            >{saveMut.isPending ? "Saving..." : "Save"}</button>
          </div>
        </div>

        {error && <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-lg p-2">{error}</div>}

        {testResult && (
          <div className="border rounded-lg p-3 bg-slate-50 space-y-1.5">
            <StepBadge result={testResult.smtp_connect} label="SMTP connect" />
            <StepBadge result={testResult.smtp_auth} label="SMTP authenticate" />
            <StepBadge result={testResult.smtp_probe_send} label="SMTP probe send" />
            <StepBadge result={testResult.imap_connect} label="IMAP connect" />
            <StepBadge result={testResult.imap_auth} label="IMAP authenticate" />
          </div>
        )}
        </div>
      </section>

      <WhatsAppCard />

      <section className="card">
        <div className="card-head">
          <h3 className="font-semibold text-slate-800">Saved channels</h3>
        </div>
        <div className="card-pad">
        {!channels?.length ? (
          <p className="text-sm text-slate-500">No channels yet. Add one above.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th">Label</th>
                <th className="th">Type</th>
                <th className="th">SMTP</th>
                <th className="th">IMAP</th>
                <th className="th">Cap</th>
                <th className="th">Status</th>
                <th className="th"></th>
              </tr>
            </thead>
            <tbody>
              {channels.map((c: ChannelOut) => (
                <tr key={c.id} className="border-t border-slate-100">
                  <td className="td font-medium">{c.display_label}</td>
                  <td className="td">{c.channel_type}</td>
                  <td className="td font-mono text-xs">{c.smtp_host}:{c.smtp_port}</td>
                  <td className="td font-mono text-xs">{c.imap_host ? `${c.imap_host}:${c.imap_port}` : "—"}</td>
                  <td className="td">{c.sent_today}/{c.daily_cap}</td>
                  <td className="td">
                    <span className={c.status === "active" || c.status === "connected" ? "badge-green" : c.status === "invalid" || c.status === "error" ? "badge-rose" : c.status === "pending" ? "badge-amber" : "badge-slate"}>{c.status}</span>
                  </td>
                  <td className="td">
                    <button
                      onClick={() => { if (confirm(`Delete "${c.display_label}"?`)) deleteMut.mutate(c.id); }}
                      className="text-rose-600 hover:underline text-xs"
                    >delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        </div>
      </section>
    </div>
  );
}
