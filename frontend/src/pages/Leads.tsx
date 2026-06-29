import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  LatestReply,
  LeadField,
  LeadOut,
  UploadCommitResponse,
  UploadPreviewResponse,
  commitUpload,
  createLead,
  deleteLead,
  formatAxiosError,
  listLeads,
  previewUpload,
} from "../api/leads";
import { DraftResponse, SendResponse, draftFollowup, sendFollowup, sendLinkedInDm } from "../api/followups";
import PageHero from "../components/PageHero";

const LEAD_FIELDS: { key: LeadField; label: string; required?: boolean }[] = [
  { key: "email", label: "Email" },
  { key: "first_name", label: "First name" },
  { key: "last_name", label: "Last name" },
  { key: "phone", label: "Phone" },
  { key: "linkedin_url", label: "LinkedIn URL" },
  { key: "company", label: "Company" },
  { key: "title", label: "Title" },
];

const PAGE_SIZE = 50;

function blankMapping(): Record<LeadField, string | null> {
  return Object.fromEntries(LEAD_FIELDS.map(f => [f.key, null])) as Record<LeadField, string | null>;
}

// ─── Channel reply meta ───────────────────────────────────────────────────────

const CHANNEL_REPLY_META: Record<string, { label: string }> = {
  email:    { label: "Email" },
  linkedin: { label: "LinkedIn" },
  whatsapp: { label: "WhatsApp" },
};
const CHANNEL_ORDER = ["email", "linkedin", "whatsapp"];

// ─── Sentiment badge ──────────────────────────────────────────────────────────

const SENTIMENT_COLORS: Record<string, string> = {
  positive:    "bg-emerald-100 text-emerald-800 border-emerald-200",
  interested:  "bg-sky-100 text-sky-800 border-sky-200",
  objection:   "bg-amber-100 text-amber-800 border-amber-200",
  negative:    "bg-rose-100 text-rose-800 border-rose-200",
  unsubscribe: "bg-red-200 text-red-900 border-red-300",
  auto_reply:  "bg-slate-100 text-slate-500 border-slate-200",
  neutral:     "bg-slate-100 text-slate-500 border-slate-200",
};

function SentimentBadge({ label, size = "sm" }: { label: string | null; size?: "sm" | "xs" }) {
  if (!label) return null;
  const colors = SENTIMENT_COLORS[label] ?? "bg-slate-100 text-slate-500 border-slate-200";
  const text = size === "xs" ? "text-xs px-1.5 py-0.5" : "text-xs px-2 py-0.5";
  return (
    <span className={`inline-block rounded-full border font-medium capitalize ${text} ${colors}`}>
      {label.replace("_", " ")}
    </span>
  );
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 2) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

// ─── Reply modal ─────────────────────────────────────────────────────────────

function ReplyModal({ lead, channel, onClose }: { lead: LeadOut; channel: string; onClose: () => void }) {
  const reply = (lead.channel_replies?.[channel] ?? lead.latest_reply) as LatestReply;
  const channelMeta = CHANNEL_REPLY_META[channel] ?? { label: channel };
  const [tab, setTab] = useState<"email" | "linkedin">("email");

  // Email draft state
  const [emailDraft, setEmailDraft] = useState<DraftResponse | null>(null);
  const [draftingEmail, setDraftingEmail] = useState(false);
  const [draftEmailErr, setDraftEmailErr] = useState<string | null>(null);
  const [emailSubject, setEmailSubject] = useState("");
  const [emailBody, setEmailBody] = useState("");
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<SendResponse | null>(null);

  // LinkedIn draft state
  const [liMessage, setLiMessage] = useState("");
  const [draftingLi, setDraftingLi] = useState(false);
  const [draftLiErr, setDraftLiErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [sendingLi, setSendingLi] = useState(false);
  const [sendLiResult, setSendLiResult] = useState<{ ok: boolean; detail: string } | null>(null);

  async function generateEmailDraft() {
    setDraftingEmail(true);
    setDraftEmailErr(null);
    setSendResult(null);
    try {
      const d = await draftFollowup({ lead_id: lead.id });
      setEmailDraft(d);
      setEmailSubject(d.subject);
      setEmailBody(d.body);
    } catch (e) {
      setDraftEmailErr(formatAxiosError(e));
    } finally {
      setDraftingEmail(false);
    }
  }

  async function generateLinkedInDraft() {
    setDraftingLi(true);
    setDraftLiErr(null);
    try {
      const d = await draftFollowup({
        lead_id: lead.id,
        template_subject: "LinkedIn follow-up",
        template_body:
          "Hi {{first_name}}, I saw your recent reply and wanted to connect on LinkedIn. " +
          "[One short sentence referencing the reply context. Propose a clear next step. " +
          "Keep under 200 words. No subject line needed — this is a LinkedIn DM.]",
      });
      // For LinkedIn we only use the body, trim subject noise
      setLiMessage(d.body);
    } catch (e) {
      setDraftLiErr(formatAxiosError(e));
    } finally {
      setDraftingLi(false);
    }
  }

  async function handleSendEmail() {
    if (!lead.email) return;
    setSending(true);
    setSendResult(null);
    try {
      const r = await sendFollowup({
        lead_id: lead.id,
        to_email: lead.email,
        subject: emailSubject,
        body: emailBody,
      });
      setSendResult(r);
    } catch (e) {
      setSendResult({ ok: false, detail: formatAxiosError(e) });
    } finally {
      setSending(false);
    }
  }

  function handleCopyLi() {
    navigator.clipboard.writeText(liMessage).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  async function handleSendLi() {
    setSendingLi(true);
    setSendLiResult(null);
    try {
      const r = await sendLinkedInDm(lead.id, liMessage);
      setSendLiResult({ ok: r.ok, detail: r.ok ? "Queued — extension will send it shortly." : "Failed to queue." });
    } catch {
      setSendLiResult({ ok: false, detail: "Request failed." });
    } finally {
      setSendingLi(false);
    }
  }

  const leadName = [lead.first_name, lead.last_name].filter(Boolean).join(" ") || lead.email || "Lead";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-3 border-b shrink-0">
          <div>
            <h2 className="font-semibold text-slate-900 text-lg">Reply from {leadName}</h2>
            <p className="text-xs text-slate-500 mt-0.5">{channelMeta.label} · {lead.email} · {lead.company || "—"}</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-2xl leading-none">&times;</button>
        </div>

        <div className="overflow-y-auto flex-1 px-6 py-4 space-y-4">
          {/* Reply card */}
          
          <div className="rounded-lg border bg-slate-50 p-4 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <SentimentBadge label={reply.sentiment_label} />
              {reply.sentiment_confidence != null && (
                <span className="text-xs text-slate-500">
                  {Math.round(reply.sentiment_confidence * 100)}% confidence
                </span>
              )}
              <span className="text-xs text-slate-400 ml-auto">{timeAgo(reply.occurred_at)}</span>
            </div>
            {reply.sentiment_reasoning && (
              <p className="text-xs text-slate-500 italic">{reply.sentiment_reasoning}</p>
            )}
            <div className="text-sm text-slate-800 whitespace-pre-wrap border-t pt-2">
              {reply.body || <span className="text-slate-400">(no body captured)</span>}
            </div>
          </div>

          {/* Tabs */}
          <div className="flex gap-1 border-b">
            <button
              onClick={() => setTab("email")}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                tab === "email"
                  ? "border-sky-600 text-sky-700"
                  : "border-transparent text-slate-500 hover:text-slate-700"
              }`}
            >
              Email Reply
            </button>
            <button
              onClick={() => setTab("linkedin")}
              disabled={!lead.linkedin_url}
              className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                tab === "linkedin"
                  ? "border-sky-600 text-sky-700"
                  : "border-transparent text-slate-500 hover:text-slate-700"
              }`}
            >
              LinkedIn Message
              {!lead.linkedin_url && <span className="ml-1 text-xs">(no URL)</span>}
            </button>
          </div>

          {/* Email tab */}
          {tab === "email" && (
            <div className="space-y-3">
              {!emailDraft && !draftingEmail && (
                <button
                  onClick={generateEmailDraft}
                  className="w-full border-2 border-dashed border-sky-200 rounded-lg py-4 text-sky-700 hover:bg-sky-50 text-sm font-medium"
                >
                  Generate AI Draft
                </button>
              )}
              {draftingEmail && (
                <div className="text-center text-sm text-slate-500 py-6">
                  <span className="inline-block animate-spin mr-2">⟳</span> Generating draft...
                </div>
              )}
              {draftEmailErr && (
                <div className="text-sm text-rose-700 bg-rose-50 rounded p-2">{draftEmailErr}</div>
              )}
              {emailDraft && (
                <>
                  {emailDraft.notes && (
                    <div className="text-xs text-slate-500 bg-slate-50 border rounded p-2 italic">
                      {emailDraft.notes}
                    </div>
                  )}
                  <label className="block">
                    <span className="text-xs text-slate-500 font-medium">Subject</span>
                    <input
                      value={emailSubject}
                      onChange={e => setEmailSubject(e.target.value)}
                      className="mt-1 w-full border rounded px-3 py-1.5 text-sm"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-500 font-medium">Body</span>
                    <textarea
                      value={emailBody}
                      onChange={e => setEmailBody(e.target.value)}
                      rows={8}
                      className="mt-1 w-full border rounded px-3 py-2 text-sm font-mono resize-y"
                    />
                  </label>
                  <div className="flex items-center gap-3">
                    <button
                      onClick={handleSendEmail}
                      disabled={sending || !lead.email || !emailSubject.trim() || !emailBody.trim()}
                      className="px-4 py-1.5 rounded bg-emerald-600 text-white text-sm hover:bg-emerald-700 disabled:bg-slate-300"
                    >
                      {sending ? "Sending..." : "Send Email"}
                    </button>
                    <button
                      onClick={generateEmailDraft}
                      disabled={draftingEmail}
                      className="px-4 py-1.5 rounded border text-sm text-slate-600 hover:bg-slate-50"
                    >
                      Regenerate
                    </button>
                    {!lead.email && (
                      <span className="text-xs text-rose-600">No email address on this lead</span>
                    )}
                  </div>
                  {sendResult && (
                    <div className={`text-sm rounded p-2 ${sendResult.ok ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-800"}`}>
                      {sendResult.ok ? "Email sent successfully." : `Failed: ${sendResult.detail}`}
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* LinkedIn tab */}
          {tab === "linkedin" && lead.linkedin_url && (
            <div className="space-y-3">
              {!liMessage && !draftingLi && (
                <button
                  onClick={generateLinkedInDraft}
                  className="w-full border-2 border-dashed border-sky-200 rounded-lg py-4 text-sky-700 hover:bg-sky-50 text-sm font-medium"
                >
                  Generate LinkedIn Message
                </button>
              )}
              {draftingLi && (
                <div className="text-center text-sm text-slate-500 py-6">
                  <span className="inline-block animate-spin mr-2">⟳</span> Generating message...
                </div>
              )}
              {draftLiErr && (
                <div className="text-sm text-rose-700 bg-rose-50 rounded p-2">{draftLiErr}</div>
              )}
              {liMessage && (
                <>
                  <label className="block">
                    <span className="text-xs text-slate-500 font-medium">LinkedIn Message (edit before sending)</span>
                    <textarea
                      value={liMessage}
                      onChange={e => setLiMessage(e.target.value)}
                      rows={7}
                      maxLength={300}
                      className="mt-1 w-full border rounded px-3 py-2 text-sm resize-y"
                    />
                    <span className="text-xs text-slate-400">{liMessage.length}/300 chars</span>
                  </label>
                  <div className="flex items-center gap-3 flex-wrap">
                    <button
                      onClick={handleSendLi}
                      disabled={sendingLi || !!sendLiResult?.ok}
                      className="px-4 py-1.5 rounded bg-sky-600 text-white text-sm hover:bg-sky-700 disabled:opacity-60"
                    >
                      {sendingLi ? "Sending…" : sendLiResult?.ok ? "Queued!" : "Send via Extension"}
                    </button>
                    <button
                      onClick={handleCopyLi}
                      className="px-4 py-1.5 rounded border text-sm text-slate-600 hover:bg-slate-50"
                    >
                      {copied ? "Copied!" : "Copy"}
                    </button>
                    <a
                      href={lead.linkedin_url}
                      target="_blank"
                      rel="noreferrer"
                      className="px-4 py-1.5 rounded border text-sm text-slate-600 hover:bg-slate-50"
                    >
                      Open Profile ↗
                    </a>
                    <button
                      onClick={generateLinkedInDraft}
                      disabled={draftingLi}
                      className="px-4 py-1.5 rounded border text-sm text-slate-500 hover:bg-slate-50"
                    >
                      Regenerate
                    </button>
                  </div>
                  {sendLiResult && (
                    <p className={`text-xs ${sendLiResult.ok ? "text-emerald-600" : "text-rose-600"}`}>
                      {sendLiResult.detail}
                    </p>
                  )}
                </>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t shrink-0 flex justify-end">
          <button onClick={onClose} className="px-4 py-1.5 rounded border text-sm text-slate-600 hover:bg-slate-50">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Main Leads page ──────────────────────────────────────────────────────────

export default function Leads() {
  const qc = useQueryClient();

  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const { data: leadsData } = useQuery({
    queryKey: ["leads", { search, page }],
    queryFn: () => listLeads({ search: search || undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
  });

  const [preview, setPreview] = useState<UploadPreviewResponse | null>(null);
  const [mapping, setMapping] = useState<Record<LeadField, string | null>>(blankMapping());
  const [commitResult, setCommitResult] = useState<UploadCommitResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Manual single-lead add
  const [manualOpen, setManualOpen] = useState(false);
  const [manual, setManual] = useState<Record<LeadField, string>>({
    email: "", first_name: "", last_name: "", phone: "",
    linkedin_url: "", company: "", title: "",
  });
  const [manualStatus, setManualStatus] = useState<string | null>(null);

  // Reply modal
  const [replyLead, setReplyLead] = useState<LeadOut | null>(null);
  const [replyChannel, setReplyChannel] = useState<string>("email");

  const manualMut = useMutation({
    mutationFn: async () => {
      setManualStatus(null);
      const payload = Object.fromEntries(
        Object.entries(manual).filter(([_, v]) => v.trim() !== "")
      );
      return createLead(payload);
    },
    onSuccess: lead => {
      setManualStatus(`Added: ${lead.first_name ?? ""} ${lead.last_name ?? ""}`.trim() + ` (#${lead.id})`);
      setManual({ email: "", first_name: "", last_name: "", phone: "", linkedin_url: "", company: "", title: "" });
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: e => setManualStatus("Error: " + formatAxiosError(e)),
  });

  const previewMut = useMutation({
    mutationFn: async (file: File) => previewUpload(file),
    onSuccess: r => {
      setPreview(r);
      setMapping(r.suggested_mapping);
      setCommitResult(null);
      setError(null);
    },
    onError: e => { setError(formatAxiosError(e)); setPreview(null); },
  });

  const commitMut = useMutation({
    mutationFn: async () => {
      if (!preview) throw new Error("no preview");
      return commitUpload({ token: preview.token, mapping });
    },
    onSuccess: r => {
      setCommitResult(r);
      setPreview(null);
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: e => setError(formatAxiosError(e)),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: number) => deleteLead(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["leads"] }),
  });

  const totalPages = leadsData ? Math.ceil(leadsData.total / PAGE_SIZE) : 0;
  const hasIdentityMapped = !!(mapping.email || mapping.phone || mapping.linkedin_url);

  return (
    <div className="space-y-8 max-w-6xl">
      {replyLead && <ReplyModal lead={replyLead} channel={replyChannel} onClose={() => setReplyLead(null)} />}

      <PageHero
        eyebrow="Contacts"
        title="Leads"
        subtitle="Upload CSV / TSV / Excel. Columns are auto-mapped; you can override them before committing. Dedupe is by canonical identity (email → phone → LinkedIn slug)."
      />

      <section className="card card-pad space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-800">Add a single lead manually</h3>
          <button onClick={() => setManualOpen(o => !o)} className="btn-ghost btn-sm">
            {manualOpen ? "Hide" : "+ Add manually"}
          </button>
        </div>
        {manualOpen && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
              {LEAD_FIELDS.map(f => (
                <label key={f.key}>
                  <span className="label">{f.label}</span>
                  <input
                    value={manual[f.key]}
                    onChange={e => setManual({ ...manual, [f.key]: e.target.value })}
                    placeholder={f.key === "email" ? "alex@acme.io"
                      : f.key === "phone" ? "+1 415 555 1234"
                      : f.key === "linkedin_url" ? "https://www.linkedin.com/in/alex"
                      : ""}
                    className="input font-mono text-xs"
                  />
                </label>
              ))}
            </div>
            <div className="text-xs text-slate-500">
              At least one of <strong>email</strong>, <strong>phone</strong>, or <strong>LinkedIn URL</strong> is required.
              Phones are normalised to E.164; LinkedIn URLs to <code>https://www.linkedin.com/in/&lt;slug&gt;</code>.
              If a lead with the same canonical identifier already exists, this updates it instead of duplicating.
            </div>
            <button
              disabled={manualMut.isPending || (!manual.email.trim() && !manual.phone.trim() && !manual.linkedin_url.trim())}
              onClick={() => manualMut.mutate()}
              className="btn-primary"
            >
              {manualMut.isPending ? "Saving..." : "Save lead"}
            </button>
            {manualStatus && (
              <div className={`text-sm rounded p-2 ${manualStatus.startsWith("Error") ? "bg-rose-50 text-rose-800" : "bg-emerald-50 text-emerald-800"}`}>
                {manualStatus}
              </div>
            )}
          </>
        )}
      </section>

      <section className="card card-pad space-y-4">
        <h3 className="font-semibold text-slate-800">Or upload a CSV / Excel file</h3>
        <input
          type="file"
          accept=".csv,.tsv,.xlsx,.xls"
          disabled={previewMut.isPending}
          onChange={e => {
            const f = e.target.files?.[0];
            if (f) previewMut.mutate(f);
          }}
          className="input text-sm"
        />
        {previewMut.isPending && <div className="text-sm text-slate-500">Parsing...</div>}
        {error && <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">{error}</div>}

        {preview && (
          <div className="space-y-4">
            <div className="text-sm text-slate-700">
              <strong>{preview.row_count}</strong> rows · <strong>{preview.columns.length}</strong> columns detected
            </div>

            <div>
              <h4 className="font-medium text-slate-700 mb-2">Map columns</h4>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
                {LEAD_FIELDS.map(f => (
                  <label key={f.key}>
                    <span className="label">{f.label}</span>
                    <select
                      value={mapping[f.key] ?? ""}
                      onChange={e => setMapping({ ...mapping, [f.key]: e.target.value || null })}
                      className="input"
                    >
                      <option value="">— ignore —</option>
                      {preview.columns.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </label>
                ))}
              </div>
              {!hasIdentityMapped && (
                <div className="mt-3 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2">
                  Map at least one of <strong>email</strong>, <strong>phone</strong>, or <strong>linkedin_url</strong>. Rows without any of these are skipped.
                </div>
              )}
            </div>

            <div>
              <h4 className="font-medium text-slate-700 mb-2">Preview (first 10 rows)</h4>
              <div className="card overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr>{preview.columns.map(c => <th key={c} className="th">{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {preview.sample_rows.map((row, i) => (
                      <tr key={i} className="border-t border-slate-100">
                        {preview.columns.map(c => <td key={c} className="td font-mono">{String(row[c] ?? "")}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <button
                onClick={() => commitMut.mutate()}
                disabled={!hasIdentityMapped || commitMut.isPending}
                className="btn-primary"
              >
                {commitMut.isPending ? "Importing..." : `Import ${preview.row_count} rows`}
              </button>
              <button
                onClick={() => { setPreview(null); setMapping(blankMapping()); }}
                className="btn-ghost"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {commitResult && (
          <div className="rounded border border-emerald-200 bg-emerald-50 p-3 text-sm">
            <div className="font-semibold text-emerald-900 mb-1">Import complete</div>
            <div className="grid grid-cols-5 gap-3 text-emerald-900">
              <div><div className="text-2xl font-bold">{commitResult.inserted}</div><div className="text-xs">inserted</div></div>
              <div><div className="text-2xl font-bold">{commitResult.updated}</div><div className="text-xs">updated</div></div>
              <div><div className="text-2xl font-bold">{commitResult.merged_within_upload}</div><div className="text-xs">merged in file</div></div>
              <div><div className="text-2xl font-bold">{commitResult.skipped_no_identity}</div><div className="text-xs">skipped — no identity</div></div>
              <div><div className="text-2xl font-bold">{commitResult.skipped_invalid}</div><div className="text-xs">skipped — invalid</div></div>
            </div>
          </div>
        )}
      </section>

      <section className="card card-pad">
        <div className="flex items-end gap-4 mb-4">
          <h3 className="font-semibold text-slate-800 flex-1">
            Your leads {leadsData && <span className="text-slate-500 font-normal text-sm">({leadsData.total})</span>}
          </h3>
          <input
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") { setSearch(searchInput); setPage(0); } }}
            placeholder="search email, name, company..."
            className="input w-72"
          />
          <button onClick={() => { setSearch(searchInput); setPage(0); }} className="btn-ghost">Search</button>
        </div>
        {!leadsData?.items.length ? (
          <p className="text-sm text-slate-500">No leads yet.</p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr>
                    <th className="th">Name</th>
                    <th className="th">Email</th>
                    <th className="th">Company / Title</th>
                    <th className="th">Phone</th>
                    <th className="th">LinkedIn</th>
                    <th className="th">Source</th>
                    <th className="th">Last Reply</th>
                    <th className="th"></th>
                  </tr>
                </thead>
                <tbody>
                  {leadsData.items.map(l => (
                    <tr key={l.id} className="border-t border-slate-100">
                      <td className="td">{[l.first_name, l.last_name].filter(Boolean).join(" ") || "—"}</td>
                      <td className="td font-mono text-xs">{l.email || "—"}</td>
                      <td className="td">{l.company || "—"}{l.title ? <div className="text-xs text-slate-500">{l.title}</div> : null}</td>
                      <td className="td font-mono text-xs">{l.phone || "—"}</td>
                      <td className="td">{l.linkedin_url ? <a className="text-brand-600 hover:underline text-xs" href={l.linkedin_url} target="_blank" rel="noreferrer">profile</a> : "—"}</td>
                      <td className="td text-xs text-slate-500">{l.source}</td>
                      <td className="td">
                        {Object.keys(l.channel_replies ?? {}).length > 0 ? (
                          <div className="flex flex-col gap-1.5">
                            {CHANNEL_ORDER.map(ch => {
                              const r = l.channel_replies?.[ch];
                              if (!r) return null;
                              const meta = CHANNEL_REPLY_META[ch];
                              return (
                                <button
                                  key={ch}
                                  onClick={() => { setReplyChannel(ch); setReplyLead(l); }}
                                  className="grid items-center gap-x-2 group text-left hover:bg-slate-50 rounded px-1 -mx-1 py-0.5 transition-colors"
                                  style={{ gridTemplateColumns: "64px 72px 48px" }}
                                  title={r.body?.slice(0, 100) ?? "View reply"}
                                >
                                  <span className="text-xs text-slate-500 truncate">{meta.label}</span>
                                  <span><SentimentBadge label={r.sentiment_label} size="xs" /></span>
                                  <span className="text-xs text-slate-400 group-hover:text-slate-600 text-right whitespace-nowrap">{timeAgo(r.occurred_at)}</span>
                                </button>
                              );
                            })}
                          </div>
                        ) : (
                          <span className="text-slate-300 text-xs">—</span>
                        )}
                      </td>
                      <td className="td">
                        <button onClick={() => { if (confirm("Delete this lead?")) deleteMut.mutate(l.id); }} className="text-rose-600 hover:underline text-xs">delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between mt-4 text-sm">
              <div className="text-slate-500">Page {page + 1} of {totalPages}</div>
              <div className="flex gap-2">
                <button disabled={page === 0} onClick={() => setPage(p => Math.max(0, p - 1))} className="btn-ghost btn-sm disabled:text-slate-300">Prev</button>
                <button disabled={page + 1 >= totalPages} onClick={() => setPage(p => p + 1)} className="btn-ghost btn-sm disabled:text-slate-300">Next</button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
