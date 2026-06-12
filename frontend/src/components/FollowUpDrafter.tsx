import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { DraftResponse, draftFollowup, sendFollowup } from "../api/followups";

interface Props {
  lead: {
    id: number;
    email: string | null;
    name?: string | null;
    company?: string | null;
    title?: string | null;
  };
  onClose: () => void;
}

export default function FollowUpDrafter({ lead, onClose }: Props) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [editing, setEditing] = useState(false);
  const [sendStatus, setSendStatus] = useState<string | null>(null);

  const draftMut = useMutation({
    mutationFn: async () => draftFollowup({ lead_id: lead.id }),
    onSuccess: r => {
      setDraft(r);
      setSubject(r.subject);
      setBody(r.body);
      setEditing(false);
    },
  });

  const sendMut = useMutation({
    mutationFn: async () => sendFollowup({
      lead_id: lead.id,
      to_email: lead.email!,
      subject, body,
    }),
    onSuccess: r => setSendStatus(r.ok ? "Sent" : `Failed: ${r.detail}`),
    onError: e => setSendStatus(`Error: ${String(e)}`),
  });

  useEffect(() => {
    if (!draft && !draftMut.isPending) draftMut.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="fixed inset-0 bg-slate-900/40 z-50 flex items-center justify-center p-6">
      <div className="bg-white rounded-lg shadow-xl max-w-3xl w-full max-h-[90vh] overflow-auto">
        <div className="px-5 py-3 border-b flex items-center justify-between">
          <div>
            <h3 className="font-semibold text-slate-800">Draft follow-up</h3>
            <div className="text-xs text-slate-500">
              for {lead.name || lead.email}
              {lead.title ? ` · ${lead.title}` : ""}{lead.company ? ` @ ${lead.company}` : ""}
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700">close</button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {draftMut.isPending && <div className="text-sm text-slate-500">Drafting...</div>}
          {!draftMut.isPending && draft && (
            <>
              <div className="text-xs text-violet-700 bg-violet-50 border border-violet-200 rounded p-2">
                <strong>Why this works:</strong> {draft.notes || "(no notes)"}
                <div className="text-slate-500 mt-1">
                  model: {draft.model} · prior events: {draft.prior_history_count} · similar snippets retrieved: {draft.similar_snippets.length}
                </div>
              </div>

              {draft.similar_snippets.length > 0 && (
                <details className="text-xs text-slate-600 bg-slate-50 rounded p-2">
                  <summary className="cursor-pointer">Retrieved vault context ({draft.similar_snippets.length})</summary>
                  <ul className="space-y-2 mt-2">
                    {draft.similar_snippets.map((s, i) => (
                      <li key={i} className="bg-white border rounded p-2 font-mono whitespace-pre-wrap">{s.slice(0, 600)}</li>
                    ))}
                  </ul>
                </details>
              )}

              <div className="grid md:grid-cols-2 gap-3">
                <div className="rounded border bg-slate-50 p-3">
                  <div className="text-xs text-slate-500 uppercase mb-1">Template</div>
                  <div className="text-sm font-medium mb-1">{draft.template_subject}</div>
                  <pre className="text-xs whitespace-pre-wrap text-slate-700">{draft.template_body}</pre>
                </div>
                <div className="rounded border bg-emerald-50 p-3">
                  <div className="text-xs text-emerald-700 uppercase mb-1">AI draft</div>
                  {editing ? (
                    <>
                      <input value={subject} onChange={e => setSubject(e.target.value)} className="w-full border rounded px-2 py-1 text-sm mb-2" />
                      <textarea value={body} onChange={e => setBody(e.target.value)} rows={8} className="w-full border rounded px-2 py-1 text-xs font-mono" />
                    </>
                  ) : (
                    <>
                      <div className="text-sm font-medium mb-1">{subject}</div>
                      <pre className="text-xs whitespace-pre-wrap text-slate-700">{body}</pre>
                    </>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-2">
                <button onClick={() => setEditing(e => !e)} className="border rounded px-3 py-1.5 text-sm">
                  {editing ? "Done editing" : "Edit"}
                </button>
                <button onClick={() => draftMut.mutate()} className="border rounded px-3 py-1.5 text-sm">
                  Re-draft
                </button>
                <div className="flex-1" />
                {!lead.email && <span className="text-xs text-rose-600">No email on this lead — can't send.</span>}
                <button
                  disabled={!lead.email || sendMut.isPending}
                  onClick={() => sendMut.mutate()}
                  className="border rounded px-4 py-1.5 text-sm bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300"
                >
                  {sendMut.isPending ? "Sending..." : "Send now"}
                </button>
              </div>

              {sendStatus && (
                <div className={`text-sm rounded p-2 ${sendStatus.startsWith("Sent") ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-800"}`}>
                  {sendStatus}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
