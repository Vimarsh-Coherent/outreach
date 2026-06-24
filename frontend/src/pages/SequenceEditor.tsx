import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import AbTestPanel from "../components/AbTestPanel";
import BranchEditor from "../components/BranchEditor";
import SequenceFlowCanvas from "../components/SequenceFlowCanvas";
import SpamCheckButton from "../components/SpamCheckButton";
import { reorderStepsWithTransitionDelays, sortSteps } from "../lib/sequenceSteps";
import { listLeads } from "../api/leads";
import {
  SequenceDetail,
  StepChannel,
  StepCreate,
  StepOut,
  addStep,
  deleteStep,
  enrolLeads,
  getSequence,
  getSequenceGrounding,
  reorderSteps,
  updateSequence,
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
  // When the user clicks "+" on a connector, the next added step is inserted at
  // that position instead of appended (add → reorder into place).
  const [insertIndex, setInsertIndex] = useState<number | null>(null);
  const addFormRef = useRef<HTMLDivElement>(null);

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
    onSuccess: async (newStep) => {
      // Insert-at-position: the new step appends at the end, so move it into
      // place by id order, then persist the reorder.
      if (insertIndex != null && seq) {
        const ids = sortSteps(seq.steps).map(s => s.id);
        ids.splice(insertIndex, 0, newStep.id);
        try { await reorderSteps(sequenceId, ids); } catch { /* fall back to appended order */ }
      }
      qc.invalidateQueries({ queryKey: ["sequence", sequenceId] });
      setSubject(""); setBody(""); setDelayDays(0); setDelayHours(0);
      setInsertIndex(null);
    },
  });

  const deleteStepMut = useMutation({
    mutationFn: async (stepId: number) => deleteStep(sequenceId, stepId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sequence", sequenceId] }),
    onError: (e: any) => alert(e?.response?.data?.detail || "Delete failed — try again."),
  });

  const reorderMut = useMutation({
    mutationFn: async (stepIds: number[]) => reorderSteps(sequenceId, stepIds),
    // Optimistic: renumber + re-thread waits immediately so the flow updates the
    // instant you drop a card, without waiting for the round-trip.
    onMutate: async (stepIds: number[]) => {
      await qc.cancelQueries({ queryKey: ["sequence", sequenceId] });
      const prev = qc.getQueryData<SequenceDetail>(["sequence", sequenceId]);
      if (prev) {
        qc.setQueryData<SequenceDetail>(["sequence", sequenceId], {
          ...prev,
          steps: reorderStepsWithTransitionDelays(prev.steps, stepIds),
        });
      }
      return { prev };
    },
    onError: (_e, _ids, ctx) => {
      if (ctx?.prev) qc.setQueryData(["sequence", sequenceId], ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["sequence", sequenceId] }),
  });

  const handleInsertAt = (index: number) => {
    setInsertIndex(index);
    setEditingId(null);
    addFormRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

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

  const trackMut = useMutation({
    mutationFn: async (patch: { track_opens?: boolean; track_clicks?: boolean }) =>
      updateSequence(sequenceId, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sequence", sequenceId] }),
  });

  if (!seq) return <div className="text-sm text-slate-500">Loading...</div>;

  const bodyCap = CHANNEL_BODY_CAP[channel];
  const bodyOver = body.length > bodyCap;

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="text-sm">
        <Link to="/sequences" className="text-brand-600 hover:underline">← back to sequences</Link>
      </div>
      <div className="flex items-end justify-between gap-4">
        <div className="min-w-0">
          <h2 className="page-title">{seq.name}</h2>
          <div className="text-sm text-slate-500 mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="badge-slate capitalize">{seq.status}</span>
            <span className="font-mono text-slate-600">{seq.timezone}</span>
            <span className="text-slate-300">·</span>
            <span>{seq.send_window_start.slice(0,5)}–{seq.send_window_end.slice(0,5)}</span>
            <span className="text-slate-300">·</span>
            <span>{DAYS_MASK_LABEL(seq.send_days_mask)}</span>
            {seq.ai_followups_enabled && <span className="badge-brand">AI follow-ups</span>}
          </div>
          {seq.description && <p className="text-sm text-slate-600 mt-2 max-w-2xl">{seq.description}</p>}
        </div>
        <button onClick={() => setEnrolOpen(o => !o)} disabled={seq.steps.length === 0} className="btn-primary shrink-0">
          {enrolOpen ? "Close" : "Enrol leads"}
        </button>
      </div>

      <section className="card card-pad">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">Email tracking</h3>
            <p className="text-xs text-slate-500 mt-0.5">
              Records opens/clicks on email steps. Needs a public tracking URL configured on the server.
            </p>
          </div>
          <div className="flex items-center gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={seq.track_opens}
                onChange={(e) => trackMut.mutate({ track_opens: e.target.checked })} />
              Track opens
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={seq.track_clicks}
                onChange={(e) => trackMut.mutate({ track_clicks: e.target.checked })} />
              Track clicks
            </label>
          </div>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h3 className="font-semibold text-slate-900">Steps <span className="text-slate-400 font-normal">({seq.steps.length})</span></h3>
          <button
            onClick={() => setGroundOn(true)}
            disabled={seq.steps.length === 0 || groundingLoading}
            className="btn-ghost btn-sm"
          >
            {groundingLoading ? "Scoring…" : grounding ? "Re-check grounding" : "Check document grounding"}
          </button>
        </div>
        <div className="card-pad space-y-4">

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
          <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-8 text-center">
            <p className="text-sm font-medium text-slate-700">No steps yet</p>
            <p className="text-xs text-slate-500 mt-1">Add the first one below to start building your flow.</p>
          </div>
        ) : (
          <>
            <SequenceFlowCanvas
              steps={seq.steps}
              selectedStepId={editingId}
              onSelect={startEdit}
              onDelete={(id) => deleteStepMut.mutate(id)}
              onInsert={handleInsertAt}
              onReorder={(ids) => reorderMut.mutate(ids)}
            />
            <p className="text-xs text-slate-400 mt-1">
              Drag a card to reorder · click ✏️ to edit · ＋ on a connector inserts a step · scroll/pinch to zoom
              {reorderMut.isPending && <span className="ml-2 text-sky-600 animate-pulse">saving order…</span>}
            </p>
          </>
        )}

        {(() => {
          const s = seq.steps.find(st => st.id === editingId);
          if (!s) return null;
          const editCap = CHANNEL_BODY_CAP[s.channel as StepChannel];
          const editOver = editBody.length > editCap;
          const editInvalid =
            !editBody.trim() || editOver || (s.channel === "email" && !editSubject.trim());
          const g = groundingByStep.get(s.id);
          const gstyle = g ? (VERDICT_STYLE[g.verdict] ?? VERDICT_STYLE.weak) : null;
          return (
            <div className="rounded-lg border border-sky-200 bg-sky-50/40 p-4 space-y-2">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-semibold text-slate-800">
                  Editing step {s.step_order} · {CHANNEL_LABEL[s.channel as StepChannel]}
                </h4>
                <div className="flex items-center gap-3 text-xs">
                  {g && gstyle && (
                    <span className={`px-2 py-0.5 rounded font-medium ${gstyle.chip}`} title={`cosine ${g.similarity.toFixed(3)}`}>
                      sim {g.similarity.toFixed(2)} · {gstyle.label}
                    </span>
                  )}
                  <button onClick={() => setEditingId(null)} className="text-slate-600 hover:underline">close</button>
                </div>
              </div>
              <div className="grid grid-cols-12 gap-2">
                <label className="col-span-3">
                  <span className="label">Delay days</span>
                  <input type="number" min={0} max={365} value={editDelayDays} onChange={e => setEditDelayDays(Number(e.target.value))} className="input" />
                </label>
                <label className="col-span-3">
                  <span className="label">Delay hours</span>
                  <input type="number" min={0} max={23} value={editDelayHours} onChange={e => setEditDelayHours(Number(e.target.value))} className="input" />
                </label>
              </div>
              {s.channel === "email" && (
                <label className="block">
                  <span className="label">Subject (max 250)</span>
                  <input value={editSubject} onChange={e => setEditSubject(e.target.value)} maxLength={250} className="input" />
                </label>
              )}
              <label className="block">
                <span className="label">
                  Body <span className={`ml-1 normal-case ${editOver ? "text-rose-600" : "text-slate-400"}`}>{editBody.length}/{editCap}</span>
                </span>
                <textarea value={editBody} onChange={e => setEditBody(e.target.value)} rows={8} className={`input font-mono text-xs ${editOver ? "border-rose-400 focus:border-rose-400 focus:ring-rose-100" : ""}`} />
                <div className="text-xs text-slate-400 mt-1">Tokens: <code className="bg-slate-100 px-1 rounded">{"{{first_name}}"}</code> <code className="bg-slate-100 px-1 rounded">{"{{company}}"}</code> <code className="bg-slate-100 px-1 rounded">{"{{sender_name}}"}</code></div>
              </label>

              <SpamCheckButton subject={s.channel === "email" ? editSubject : null} body={editBody} />

              <BranchEditor sequenceId={sequenceId} step={s} allSteps={seq.steps} />

              <AbTestPanel sequenceId={sequenceId} step={s} />

              <div className="flex gap-2">
                <button
                  disabled={editInvalid || updateStepMut.isPending}
                  onClick={() => updateStepMut.mutate(s)}
                  className="btn-primary btn-sm"
                >
                  {updateStepMut.isPending ? "Saving…" : "Save"}
                </button>
                <button onClick={() => setEditingId(null)} className="btn-ghost btn-sm">Cancel</button>
              </div>
            </div>
          );
        })()}

        <div ref={addFormRef} className="border-t border-slate-100 pt-4 space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-slate-700">
              {insertIndex != null ? `Insert step at position ${insertIndex + 1}` : "Add step"}
            </h4>
            {insertIndex != null && (
              <button onClick={() => setInsertIndex(null)} className="text-xs text-slate-500 hover:underline">
                cancel insert (append to end)
              </button>
            )}
          </div>
          <div className="grid grid-cols-12 gap-3 text-sm">
            <label className="col-span-4"><span className="label">Channel</span>
              <select value={channel} onChange={e => setChannel(e.target.value as StepChannel)} className="input">
                <option value="email">Email</option>
                <option value="linkedin_dm">LinkedIn DM</option>
                <option value="linkedin_connect">LinkedIn connect (note)</option>
                <option value="linkedin_like">LinkedIn visit + like posts</option>
                <option value="whatsapp">WhatsApp DM</option>
              </select>
            </label>
            <label className="col-span-4"><span className="label">Delay days</span>
              <input type="number" min={0} max={365} value={delayDays} onChange={e => setDelayDays(Number(e.target.value))} className="input" />
            </label>
            <label className="col-span-4"><span className="label">Delay hours</span>
              <input type="number" min={0} max={23} value={delayHours} onChange={e => setDelayHours(Number(e.target.value))} className="input" />
            </label>
            {channel === "email" && (
              <label className="col-span-12"><span className="label">Subject (max 250)</span>
                <input value={subject} onChange={e => setSubject(e.target.value)} maxLength={250} className="input" placeholder="Quick follow-up on {{company}}" />
              </label>
            )}
            {channel !== "linkedin_like" && (
              <label className="col-span-12">
                <span className="label">
                  Body
                  <span className={`ml-2 normal-case ${bodyOver ? "text-rose-600" : "text-slate-400"}`}>{body.length}/{bodyCap}</span>
                </span>
                <textarea value={body} onChange={e => setBody(e.target.value)} rows={6} className={`input font-mono text-xs ${bodyOver ? "border-rose-400 focus:border-rose-400 focus:ring-rose-100" : ""}`} placeholder="Hi {{first_name}}, ..." />
                <div className="text-xs text-slate-500 mt-1">Tokens: <code className="bg-slate-100 px-1 rounded">{"{{first_name}}"}</code> <code className="bg-slate-100 px-1 rounded">{"{{last_name}}"}</code> <code className="bg-slate-100 px-1 rounded">{"{{company}}"}</code> <code className="bg-slate-100 px-1 rounded">{"{{title}}"}</code> <code className="bg-slate-100 px-1 rounded">{"{{email}}"}</code></div>
              </label>
            )}
            {channel === "linkedin_like" && (
              <div className="col-span-12 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-700">
                No message needed — the extension will visit the profile and like up to 2 recent posts automatically.
              </div>
            )}
          </div>
          <button
            disabled={addMut.isPending || (channel !== "linkedin_like" && (!body.trim() || bodyOver)) || (channel === "email" && !subject.trim())}
            onClick={() => addMut.mutate()}
            className="btn-primary"
          >
            {addMut.isPending ? "Adding..." : insertIndex != null ? `Insert at position ${insertIndex + 1}` : "Add step"}
          </button>
        </div>
        </div>
      </section>

      {enrolOpen && (
        <section className="card">
          <div className="card-head">
            <h3 className="font-semibold text-slate-900">Enrol leads</h3>
            <button onClick={() => enrolMut.mutate()} disabled={selectedLeads.length === 0 || enrolMut.isPending} className="btn-primary btn-sm">
              {enrolMut.isPending ? "Enrolling..." : `Enrol ${selectedLeads.length} selected`}
            </button>
          </div>
          <div className="card-pad space-y-3">
          {!leads?.items.length ? (
            <p className="text-sm text-slate-500">No leads. <Link to="/leads" className="text-brand-600 underline">Upload some first.</Link></p>
          ) : (
            <>
              <label className="text-xs text-slate-600 flex items-center">
                <input type="checkbox" checked={selectedLeads.length === leads.items.length} onChange={e => setSelectedLeads(e.target.checked ? leads.items.map(l => l.id) : [])} className="mr-2" />
                select all on this page ({leads.items.length})
              </label>
              <div className="border border-slate-200 rounded-lg overflow-y-auto max-h-96">
                <table className="w-full">
                  <thead className="bg-slate-50 sticky top-0">
                    <tr><th className="th"></th><th className="th">Name</th><th className="th">Email</th><th className="th">Company</th></tr>
                  </thead>
                  <tbody>
                    {leads.items.map(l => (
                      <tr key={l.id} className="border-t border-slate-100">
                        <td className="td"><input type="checkbox" checked={selectedLeads.includes(l.id)} onChange={e => setSelectedLeads(prev => e.target.checked ? [...prev, l.id] : prev.filter(x => x !== l.id))} /></td>
                        <td className="td">{[l.first_name, l.last_name].filter(Boolean).join(" ")}</td>
                        <td className="td font-mono">{l.email}</td>
                        <td className="td">{l.company}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          </div>
        </section>
      )}
    </div>
  );
}
