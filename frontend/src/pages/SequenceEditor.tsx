import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { listLeads } from "../api/leads";
import {
  StepChannel,
  StepCreate,
  StepOut,
  addStep,
  deleteStep,
  enrolLeads,
  getSequence,
  getSequenceGrounding,
  updateStep,
} from "../api/sequences";

const CHANNEL_LABEL: Record<StepChannel, string> = {
  email: "Email",
  linkedin_dm: "LinkedIn DM",
  linkedin_connect: "LinkedIn Connect (note)",
  linkedin_like: "LinkedIn Visit + Like",
  call: "Call (task)",
  sms: "SMS (task)",
  whatsapp: "WhatsApp (task)",
};

const CHANNEL_BODY_CAP: Record<StepChannel, number> = {
  email: 16000,
  linkedin_dm: 8000,
  linkedin_connect: 300,
  linkedin_like: 0,
  call: 4000,
  sms: 1600,
  whatsapp: 4000,
};

const VERDICT_STYLE: Record<string, { chip: string; label: string }> = {
  grounded: { chip: "bg-emerald-100 text-emerald-800", label: "grounded" },
  weak: { chip: "bg-amber-100 text-amber-800", label: "weak" },
  possible_hallucination: { chip: "bg-rose-100 text-rose-800", label: "possible hallucination" },
};

function DAYS_MASK_LABEL(mask: number): string {
  const names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  return names.filter((_, i) => mask & (1 << i)).join("·");
}

export default function SequenceEditor() {
  const { id } = useParams();
  const sequenceId = Number(id);
  const qc = useQueryClient();

  const { data: seq } = useQuery({
    queryKey: ["sequence", sequenceId],
    queryFn: () => getSequence(sequenceId),
    enabled: !Number.isNaN(sequenceId),
  });

  // Document grounding (cosine similarity per step) — loaded lazily on demand,
  // since it embeds every message and queries the vector store.
  const [groundOn, setGroundOn] = useState(false);
  const {
    data: grounding,
    isFetching: groundingLoading,
    error: groundingError,
  } = useQuery({
    queryKey: ["grounding", sequenceId],
    queryFn: () => getSequenceGrounding(sequenceId),
    enabled: groundOn && !Number.isNaN(sequenceId),
  });
  const groundingByStep = new Map((grounding?.steps ?? []).map(g => [g.step_id, g]));

  // Add-step form
  const [channel, setChannel] = useState<StepChannel>("email");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [delayDays, setDelayDays] = useState(0);
  const [delayHours, setDelayHours] = useState(0);

  const addMut = useMutation({
    mutationFn: async () => {
      const payload: StepCreate = {
        channel,
        body: channel === "linkedin_like" ? "(visit + like)" : body,
        delay_days: delayDays,
        delay_hours: delayHours,
        subject: channel === "email" ? subject : null,
      };
      return addStep(sequenceId, payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sequence", sequenceId] });
      setSubject(""); setBody(""); setDelayDays(0); setDelayHours(0);
    },
  });

  const deleteStepMut = useMutation({
    mutationFn: async (stepId: number) => deleteStep(sequenceId, stepId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sequence", sequenceId] }),
    onError: (e: any) => alert(e?.response?.data?.detail || "Delete failed — try again."),
  });

  // Inline edit of an existing step's content (subject / body / delay)
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editSubject, setEditSubject] = useState("");
  const [editBody, setEditBody] = useState("");
  const [editDelayDays, setEditDelayDays] = useState(0);
  const [editDelayHours, setEditDelayHours] = useState(0);

  const startEdit = (s: StepOut) => {
    setEditingId(s.id);
    setEditSubject(s.subject ?? "");
    setEditBody(s.body);
    setEditDelayDays(s.delay_days);
    setEditDelayHours(s.delay_hours);
  };

  const updateStepMut = useMutation({
    mutationFn: async (s: StepOut) => {
      const payload: StepCreate = {
        channel: s.channel,
        body: editBody,
        delay_days: editDelayDays,
        delay_hours: editDelayHours,
        subject: s.channel === "email" ? editSubject : null,
      };
      return updateStep(sequenceId, s.id, payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sequence", sequenceId] });
      // content changed → previously computed grounding is stale
      qc.invalidateQueries({ queryKey: ["grounding", sequenceId] });
      setEditingId(null);
    },
  });

  // Enrol panel
  const [enrolOpen, setEnrolOpen] = useState(false);
  const [selectedLeads, setSelectedLeads] = useState<number[]>([]);
  const { data: leads } = useQuery({
    queryKey: ["leads", { search: "", page: 0 }],
    queryFn: () => listLeads({ limit: 100, offset: 0 }),
    enabled: enrolOpen,
  });
  const enrolMut = useMutation({
    mutationFn: async () => enrolLeads(sequenceId, selectedLeads),
    onSuccess: r => {
      qc.invalidateQueries({ queryKey: ["sequence", sequenceId] });
      alert(`Enrolled: ${r.enrolled}\nDeduped: ${r.deduped}\nSkipped (no identity): ${r.skipped_no_identity}`);
      setSelectedLeads([]);
      setEnrolOpen(false);
    },
  });

  if (!seq) return <div className="text-sm text-slate-500">Loading...</div>;

  const bodyCap = CHANNEL_BODY_CAP[channel];
  const bodyOver = body.length > bodyCap;

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="text-sm">
        <Link to="/sequences" className="text-sky-600 hover:underline">← back to sequences</Link>
      </div>
      <div className="flex items-end justify-between">
        <div>
          <h2 className="text-2xl font-semibold">{seq.name}</h2>
          <div className="text-sm text-slate-500 mt-1">
            <span className="inline-block px-2 py-0.5 rounded text-xs bg-slate-100 mr-2">{seq.status}</span>
            <span className="font-mono">{seq.timezone}</span> · {seq.send_window_start.slice(0,5)}–{seq.send_window_end.slice(0,5)} · {DAYS_MASK_LABEL(seq.send_days_mask)}
            {seq.ai_followups_enabled && <span className="ml-2 text-xs px-2 py-0.5 bg-violet-100 text-violet-700 rounded">AI follow-ups</span>}
          </div>
          {seq.description && <p className="text-sm text-slate-700 mt-2 max-w-2xl">{seq.description}</p>}
        </div>
        <button onClick={() => setEnrolOpen(o => !o)} disabled={seq.steps.length === 0} className="border rounded px-3 py-1.5 bg-sky-600 text-white hover:bg-sky-700 disabled:bg-slate-300">
          {enrolOpen ? "Close" : "Enrol leads"}
        </button>
      </div>

      <section className="rounded border bg-white p-4 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-800">Steps ({seq.steps.length})</h3>
          <button
            onClick={() => setGroundOn(true)}
            disabled={seq.steps.length === 0 || groundingLoading}
            className="border rounded px-3 py-1.5 text-xs bg-violet-600 text-white hover:bg-violet-700 disabled:bg-slate-300"
          >
            {groundingLoading ? "Scoring…" : grounding ? "Re-check grounding" : "Check document grounding"}
          </button>
        </div>

        {groundOn && (
          <div className="rounded border bg-violet-50 p-3 text-xs space-y-2">
            {groundingError ? (
              <p className="text-rose-700">Failed to score grounding — is the backend running?</p>
            ) : groundingLoading ? (
              <p className="text-slate-600">Embedding each message and comparing to the document chunks…</p>
            ) : grounding ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-slate-600">Source documents:</span>
                  {grounding.documents.length === 0 ? (
                    <span className="text-slate-500">none linked to this sequence</span>
                  ) : (
                    grounding.documents.map(d => (
                      <span
                        key={d.id}
                        title={d.indexed ? "indexed" : d.status}
                        className={`px-2 py-0.5 rounded ${d.indexed ? "bg-white border" : "bg-slate-200 text-slate-500"}`}
                      >
                        {d.filename}{!d.indexed && ` (${d.status})`}
                      </span>
                    ))
                  )}
                </div>
                {grounding.note ? (
                  <p className="text-amber-700">{grounding.note}</p>
                ) : (
                  <p className="text-slate-600">
                    Avg cosine similarity{" "}
                    <span className="font-semibold">{grounding.avg_similarity.toFixed(3)}</span>{" "}
                    across {grounding.steps.length} message(s) vs {grounding.chunk_count} chunk(s).
                    <span className="ml-1 text-slate-400">Higher = more grounded in the documents.</span>
                  </p>
                )}
              </>
            ) : null}
          </div>
        )}

        {seq.steps.length === 0 ? (
          <p className="text-sm text-slate-500">No steps yet. Add the first one below.</p>
        ) : (
          <ol className="space-y-2">
            {seq.steps.map((s: StepOut) => {
              const editing = editingId === s.id;
              const editCap = CHANNEL_BODY_CAP[s.channel as StepChannel];
              const editOver = editBody.length > editCap;
              const editInvalid =
                !editBody.trim() || editOver || (s.channel === "email" && !editSubject.trim());
              return (
              <li key={s.id} className="border rounded p-3 bg-slate-50">
                <div className="flex items-center justify-between mb-1">
                  <div className="text-sm font-medium">
                    Step {s.step_order} · {CHANNEL_LABEL[s.channel as StepChannel]}
                  </div>
                  <div className="flex items-center gap-3 text-xs text-slate-500">
                    {(() => {
                      const g = groundingByStep.get(s.id);
                      if (!g) return null;
                      const st = VERDICT_STYLE[g.verdict] ?? VERDICT_STYLE.weak;
                      return (
                        <span
                          className={`px-2 py-0.5 rounded font-medium ${st.chip}`}
                          title={`cosine ${g.similarity.toFixed(3)} vs ${g.best_chunk_filename ?? "document"} · ${Math.round(g.supported_ratio * 100)}% of sentences supported`}
                        >
                          sim {g.similarity.toFixed(2)} · {st.label}
                        </span>
                      );
                    })()}
                    <span>delay {s.delay_days}d {s.delay_hours}h</span>
                    {editing ? (
                      <button onClick={() => setEditingId(null)} className="text-slate-600 hover:underline">cancel</button>
                    ) : (
                      <button onClick={() => startEdit(s)} className="text-sky-600 hover:underline">edit</button>
                    )}
                    <button onClick={() => { if (confirm(`Delete step ${s.step_order}?`)) deleteStepMut.mutate(s.id); }} className="text-rose-600 hover:underline">delete</button>
                  </div>
                </div>

                {editing ? (
                  <div className="mt-2 space-y-2">
                    <div className="grid grid-cols-12 gap-2 text-xs">
                      <label className="col-span-3">
                        <span className="block text-slate-500 mb-1">Delay days</span>
                        <input type="number" min={0} max={365} value={editDelayDays} onChange={e => setEditDelayDays(Number(e.target.value))} className="w-full border rounded px-2 py-1" />
                      </label>
                      <label className="col-span-3">
                        <span className="block text-slate-500 mb-1">Delay hours</span>
                        <input type="number" min={0} max={23} value={editDelayHours} onChange={e => setEditDelayHours(Number(e.target.value))} className="w-full border rounded px-2 py-1" />
                      </label>
                    </div>
                    {s.channel === "email" && (
                      <label className="block text-xs">
                        <span className="block text-slate-500 mb-1">Subject (max 250)</span>
                        <input value={editSubject} onChange={e => setEditSubject(e.target.value)} maxLength={250} className="w-full border rounded px-2 py-1" />
                      </label>
                    )}
                    <label className="block text-xs">
                      <span className="block text-slate-500 mb-1">
                        Body <span className={`ml-1 ${editOver ? "text-rose-600" : "text-slate-400"}`}>{editBody.length}/{editCap}</span>
                      </span>
                      <textarea value={editBody} onChange={e => setEditBody(e.target.value)} rows={8} className={`w-full border rounded px-2 py-1 font-mono ${editOver ? "border-rose-400" : ""}`} />
                      <div className="text-slate-400 mt-1">Tokens: <code className="bg-slate-100 px-1">{"{{first_name}}"}</code> <code className="bg-slate-100 px-1">{"{{company}}"}</code> <code className="bg-slate-100 px-1">{"{{sender_name}}"}</code></div>
                    </label>
                    <div className="flex gap-2">
                      <button
                        disabled={editInvalid || updateStepMut.isPending}
                        onClick={() => updateStepMut.mutate(s)}
                        className="border rounded px-3 py-1 text-xs bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300"
                      >
                        {updateStepMut.isPending ? "Saving…" : "Save"}
                      </button>
                      <button onClick={() => setEditingId(null)} className="border rounded px-3 py-1 text-xs hover:bg-slate-100">Cancel</button>
                    </div>
                  </div>
                ) : (
                  <>
                    {s.subject && <div className="text-xs text-slate-700 mt-1"><span className="text-slate-400">Subject:</span> {s.subject}</div>}
                    <pre className="text-xs whitespace-pre-wrap mt-1 text-slate-700">{s.body}</pre>
                  </>
                )}
              </li>
              );
            })}
          </ol>
        )}

        <div className="border-t pt-4 space-y-3">
          <h4 className="text-sm font-semibold text-slate-700">Add step</h4>
          <div className="grid grid-cols-12 gap-3 text-sm">
            <label className="col-span-4"><span className="block text-slate-600 mb-1">Channel</span>
              <select value={channel} onChange={e => setChannel(e.target.value as StepChannel)} className="w-full border rounded px-2 py-1.5">
                <option value="email">Email</option>
                <option value="linkedin_dm">LinkedIn DM</option>
                <option value="linkedin_connect">LinkedIn connect (note)</option>
                <option value="linkedin_like">LinkedIn visit + like posts</option>
                <option value="whatsapp">WhatsApp DM</option>
              </select>
            </label>
            <label className="col-span-4"><span className="block text-slate-600 mb-1">Delay days</span>
              <input type="number" min={0} max={365} value={delayDays} onChange={e => setDelayDays(Number(e.target.value))} className="w-full border rounded px-2 py-1.5" />
            </label>
            <label className="col-span-4"><span className="block text-slate-600 mb-1">Delay hours</span>
              <input type="number" min={0} max={23} value={delayHours} onChange={e => setDelayHours(Number(e.target.value))} className="w-full border rounded px-2 py-1.5" />
            </label>
            {channel === "email" && (
              <label className="col-span-12"><span className="block text-slate-600 mb-1">Subject (max 250)</span>
                <input value={subject} onChange={e => setSubject(e.target.value)} maxLength={250} className="w-full border rounded px-2 py-1.5" placeholder="Quick follow-up on {{company}}" />
              </label>
            )}
            {channel !== "linkedin_like" && (
              <label className="col-span-12">
                <span className="block text-slate-600 mb-1">
                  Body
                  <span className={`ml-2 text-xs ${bodyOver ? "text-rose-600" : "text-slate-400"}`}>{body.length}/{bodyCap}</span>
                </span>
                <textarea value={body} onChange={e => setBody(e.target.value)} rows={6} className={`w-full border rounded px-2 py-1.5 font-mono text-xs ${bodyOver ? "border-rose-400" : ""}`} placeholder="Hi {{first_name}}, ..." />
                <div className="text-xs text-slate-500 mt-1">Tokens: <code className="bg-slate-100 px-1">{"{{first_name}}"}</code> <code className="bg-slate-100 px-1">{"{{last_name}}"}</code> <code className="bg-slate-100 px-1">{"{{company}}"}</code> <code className="bg-slate-100 px-1">{"{{title}}"}</code> <code className="bg-slate-100 px-1">{"{{email}}"}</code></div>
              </label>
            )}
            {channel === "linkedin_like" && (
              <div className="col-span-12 rounded border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-700">
                No message needed — the extension will visit the profile and like up to 2 recent posts automatically.
              </div>
            )}
          </div>
          <button
            disabled={addMut.isPending || (channel !== "linkedin_like" && (!body.trim() || bodyOver)) || (channel === "email" && !subject.trim())}
            onClick={() => addMut.mutate()}
            className="border rounded px-4 py-1.5 bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300"
          >
            {addMut.isPending ? "Adding..." : "Add step"}
          </button>
        </div>
      </section>

      {enrolOpen && (
        <section className="rounded border bg-white p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-slate-800">Enrol leads</h3>
            <button onClick={() => enrolMut.mutate()} disabled={selectedLeads.length === 0 || enrolMut.isPending} className="border rounded px-3 py-1.5 bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300">
              {enrolMut.isPending ? "Enrolling..." : `Enrol ${selectedLeads.length} selected`}
            </button>
          </div>
          {!leads?.items.length ? (
            <p className="text-sm text-slate-500">No leads. <Link to="/leads" className="text-sky-600 underline">Upload some first.</Link></p>
          ) : (
            <>
              <label className="text-xs text-slate-600">
                <input type="checkbox" checked={selectedLeads.length === leads.items.length} onChange={e => setSelectedLeads(e.target.checked ? leads.items.map(l => l.id) : [])} className="mr-2" />
                select all on this page ({leads.items.length})
              </label>
              <div className="border rounded overflow-y-auto max-h-96">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50 text-left sticky top-0">
                    <tr><th></th><th>Name</th><th>Email</th><th>Company</th></tr>
                  </thead>
                  <tbody>
                    {leads.items.map(l => (
                      <tr key={l.id} className="border-t">
                        <td className="px-2 py-1"><input type="checkbox" checked={selectedLeads.includes(l.id)} onChange={e => setSelectedLeads(prev => e.target.checked ? [...prev, l.id] : prev.filter(x => x !== l.id))} /></td>
                        <td className="px-2 py-1">{[l.first_name, l.last_name].filter(Boolean).join(" ")}</td>
                        <td className="px-2 py-1 font-mono">{l.email}</td>
                        <td className="px-2 py-1">{l.company}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}
