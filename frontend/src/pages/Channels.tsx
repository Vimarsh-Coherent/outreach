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
  deleteChannel,
  getEmailPresets,
  listChannels,
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
        <h2 className="text-2xl font-semibold">Channels</h2>
        <p className="text-sm text-slate-500 mt-1">
          Connect any SMTP mailbox (Gmail, Outlook, Yahoo, Zoho, Microsoft 365, SendGrid/SES/Mailgun, or a custom server). IMAP is used to detect replies and bounces. Credentials are encrypted at rest (Fernet).
        </p>
      </div>

      <section className="rounded border bg-white p-6 space-y-5">
        <h3 className="font-semibold text-slate-800">Add email channel</h3>

        <div className="grid grid-cols-2 gap-4">
          <label className="text-sm">
            <span className="block text-slate-600 mb-1">Provider preset</span>
            <select
              value={presetKey}
              onChange={e => setPresetKey(e.target.value)}
              className="w-full border rounded px-2 py-1.5"
            >
              {presets?.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </label>
          <label className="text-sm">
            <span className="block text-slate-600 mb-1">Display label</span>
            <input
              value={displayLabel}
              onChange={e => setDisplayLabel(e.target.value)}
              className="w-full border rounded px-2 py-1.5"
              placeholder="e.g. Sales — Gmail"
            />
          </label>
        </div>

        {currentPreset?.notes && (
          <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2">{currentPreset.notes}</div>
        )}

        <div>
          <h4 className="font-medium text-slate-700 mb-2">SMTP (outbound)</h4>
          <div className="grid grid-cols-12 gap-3 text-sm">
            <label className="col-span-6"><span className="block text-slate-600 mb-1">Host</span>
              <input value={smtp.host} onChange={e => setSmtp({ ...smtp, host: e.target.value })} className="w-full border rounded px-2 py-1.5 font-mono" />
            </label>
            <label className="col-span-2"><span className="block text-slate-600 mb-1">Port</span>
              <input type="number" value={smtp.port} onChange={e => setSmtp({ ...smtp, port: Number(e.target.value) })} className="w-full border rounded px-2 py-1.5" />
            </label>
            <label className="col-span-4"><span className="block text-slate-600 mb-1">Security</span>
              <select value={smtp.security} onChange={e => setSmtp({ ...smtp, security: e.target.value as SecurityMode })} className="w-full border rounded px-2 py-1.5">
                <option value="starttls">STARTTLS</option>
                <option value="ssl_tls">SSL/TLS</option>
                <option value="none">None (plain)</option>
              </select>
            </label>
            <label className="col-span-6"><span className="block text-slate-600 mb-1">Username</span>
              <input value={smtp.username} onChange={e => setSmtp({ ...smtp, username: e.target.value })} className="w-full border rounded px-2 py-1.5 font-mono" autoComplete="off" />
            </label>
            <label className="col-span-6"><span className="block text-slate-600 mb-1">Password / App password</span>
              <input type="password" value={smtp.password} onChange={e => setSmtp({ ...smtp, password: e.target.value })} className="w-full border rounded px-2 py-1.5 font-mono" autoComplete="new-password" />
            </label>
            <label className="col-span-7"><span className="block text-slate-600 mb-1">From email</span>
              <input value={smtp.from_email} onChange={e => setSmtp({ ...smtp, from_email: e.target.value })} className="w-full border rounded px-2 py-1.5 font-mono" placeholder="me@example.com" />
            </label>
            <label className="col-span-5"><span className="block text-slate-600 mb-1">From name (optional)</span>
              <input value={smtp.from_name ?? ""} onChange={e => setSmtp({ ...smtp, from_name: e.target.value })} className="w-full border rounded px-2 py-1.5" placeholder="Vimarsh @ Coherent" />
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
                <label className="col-span-6"><span className="block text-slate-600 mb-1">Host</span>
                  <input value={imap.host} onChange={e => setImap({ ...imap, host: e.target.value })} className="w-full border rounded px-2 py-1.5 font-mono" />
                </label>
                <label className="col-span-2"><span className="block text-slate-600 mb-1">Port</span>
                  <input type="number" value={imap.port} onChange={e => setImap({ ...imap, port: Number(e.target.value) })} className="w-full border rounded px-2 py-1.5" />
                </label>
                <label className="col-span-4"><span className="block text-slate-600 mb-1">Security</span>
                  <select value={imap.security} onChange={e => setImap({ ...imap, security: e.target.value as SecurityMode })} className="w-full border rounded px-2 py-1.5">
                    <option value="ssl_tls">SSL/TLS</option>
                    <option value="starttls">STARTTLS</option>
                    <option value="none">None (plain)</option>
                  </select>
                </label>
                {!shareCreds && (
                  <>
                    <label className="col-span-6"><span className="block text-slate-600 mb-1">Username</span>
                      <input value={imap.username} onChange={e => setImap({ ...imap, username: e.target.value })} className="w-full border rounded px-2 py-1.5 font-mono" autoComplete="off" />
                    </label>
                    <label className="col-span-6"><span className="block text-slate-600 mb-1">Password</span>
                      <input type="password" value={imap.password} onChange={e => setImap({ ...imap, password: e.target.value })} className="w-full border rounded px-2 py-1.5 font-mono" autoComplete="new-password" />
                    </label>
                  </>
                )}
                <label className="col-span-6"><span className="block text-slate-600 mb-1">Mailbox</span>
                  <input value={imap.mailbox} onChange={e => setImap({ ...imap, mailbox: e.target.value })} className="w-full border rounded px-2 py-1.5 font-mono" />
                </label>
                <label className="col-span-6"><span className="block text-slate-600 mb-1">Daily send cap</span>
                  <input type="number" value={dailyCap} onChange={e => setDailyCap(Number(e.target.value))} className="w-full border rounded px-2 py-1.5" />
                </label>
              </div>
            </>
          )}
        </div>

        <div className="grid grid-cols-12 gap-3 items-end text-sm">
          <label className="col-span-8"><span className="block text-slate-600 mb-1">Send probe email to (optional)</span>
            <input value={probeTo} onChange={e => setProbeTo(e.target.value)} placeholder="yourself@example.com" className="w-full border rounded px-2 py-1.5 font-mono" />
          </label>
          <div className="col-span-4 flex gap-2 justify-end">
            <button
              disabled={testMut.isPending}
              onClick={() => testMut.mutate()}
              className="border rounded px-3 py-1.5 bg-slate-50 hover:bg-slate-100"
            >{testMut.isPending ? "Testing..." : "Test"}</button>
            <button
              disabled={saveMut.isPending}
              onClick={() => saveMut.mutate()}
              className="border rounded px-3 py-1.5 bg-emerald-600 text-white hover:bg-emerald-700"
            >{saveMut.isPending ? "Saving..." : "Save"}</button>
          </div>
        </div>

        {error && <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">{error}</div>}

        {testResult && (
          <div className="border rounded p-3 bg-slate-50 space-y-1.5">
            <StepBadge result={testResult.smtp_connect} label="SMTP connect" />
            <StepBadge result={testResult.smtp_auth} label="SMTP authenticate" />
            <StepBadge result={testResult.smtp_probe_send} label="SMTP probe send" />
            <StepBadge result={testResult.imap_connect} label="IMAP connect" />
            <StepBadge result={testResult.imap_auth} label="IMAP authenticate" />
          </div>
        )}
      </section>

      <section className="rounded border bg-white p-6">
        <h3 className="font-semibold text-slate-800 mb-3">Saved channels</h3>
        {!channels?.length ? (
          <p className="text-sm text-slate-500">No channels yet. Add one above.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-1 pr-3">Label</th>
                <th className="py-1 pr-3">Type</th>
                <th className="py-1 pr-3">SMTP</th>
                <th className="py-1 pr-3">IMAP</th>
                <th className="py-1 pr-3">Cap</th>
                <th className="py-1 pr-3">Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {channels.map((c: ChannelOut) => (
                <tr key={c.id} className="border-t">
                  <td className="py-2 pr-3 font-medium">{c.display_label}</td>
                  <td className="py-2 pr-3">{c.channel_type}</td>
                  <td className="py-2 pr-3 font-mono text-xs">{c.smtp_host}:{c.smtp_port}</td>
                  <td className="py-2 pr-3 font-mono text-xs">{c.imap_host ? `${c.imap_host}:${c.imap_port}` : "—"}</td>
                  <td className="py-2 pr-3">{c.sent_today}/{c.daily_cap}</td>
                  <td className="py-2 pr-3">{c.status}</td>
                  <td className="py-2">
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
      </section>
    </div>
  );
}
