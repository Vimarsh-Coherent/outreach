import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import {
  SequenceOut,
  changeStatus,
  createSequence,
  deleteSequence,
  listSequences,
  testNow,
} from "../api/sequences";

const STATUS_CHIP: Record<string, string> = {
  draft: "badge-slate",
  active: "badge-green",
  paused: "badge-amber",
  archived: "badge-rose",
};

export default function Sequences() {
  const qc = useQueryClient();
  const { data: sequences } = useQuery({ queryKey: ["sequences"], queryFn: listSequences });

  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tz, setTz] = useState("Asia/Kolkata");

  const createMut = useMutation({
    mutationFn: async () => createSequence({ name, description, timezone: tz }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sequences"] });
      setShowNew(false); setName(""); setDescription("");
    },
  });

  const [toast, setToast] = useState<string | null>(null);
  const flash = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 7000); };

  const statusMut = useMutation({
    mutationFn: async ({ id, status }: { id: number; status: any }) => changeStatus(id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sequences"] }),
    onError: (e: any) => flash(e?.response?.data?.detail || "Action failed."),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: number) => deleteSequence(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sequences"] }),
    onError: (e: any) => flash(e?.response?.data?.detail || "Delete failed."),
  });

  const testNowMut = useMutation({
    mutationFn: async (id: number) => testNow(id),
    onSuccess: (r) => {
      const act = r.activated ? "Activated. " : "";
      if (r.fired > 0) {
        flash(`${act}Firing ${r.fired} enrolment${r.fired === 1 ? "" : "s"} now — sends start within seconds.`);
      } else {
        flash(`${act}No enrolled leads to fire. Enrol leads into this sequence first, then Test now.`);
      }
      qc.invalidateQueries({ queryKey: ["sequences"] });
    },
    onError: (e: any) => flash(e?.response?.data?.detail || "Test-now failed."),
  });

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="page-title">Sequences</h2>
          <p className="text-sm text-slate-500 mt-1">Multi-step outreach cadences. Each sequence runs in its own timezone + business hours.</p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/sequences/generate"
            className="btn text-white bg-gradient-to-r from-violet-600 to-brand-600 hover:from-violet-700 hover:to-brand-700 shadow-sm"
          >
            ✨ AI Sequence
          </Link>
          <button
            onClick={() => setShowNew(s => !s)}
            className="btn-ghost"
          >
            {showNew ? "Cancel" : "+ New sequence"}
          </button>
        </div>
      </div>

      {toast && (
        <div className="rounded-lg border border-brand-200 bg-brand-50 text-brand-800 text-sm px-4 py-2.5 flex items-center gap-2">
          <span>{toast}</span>
        </div>
      )}

      {showNew && (
        <section className="card card-pad space-y-3">
          <div className="grid grid-cols-12 gap-3 text-sm">
            <label className="col-span-6"><span className="block text-slate-600 mb-1">Name</span>
              <input value={name} onChange={e => setName(e.target.value)} className="input" placeholder="Q3 Enterprise Outbound" />
            </label>
            <label className="col-span-6"><span className="block text-slate-600 mb-1">Timezone</span>
              <input value={tz} onChange={e => setTz(e.target.value)} className="input font-mono text-xs" placeholder="Asia/Kolkata" />
            </label>
            <label className="col-span-12"><span className="block text-slate-600 mb-1">Description (optional)</span>
              <textarea value={description} onChange={e => setDescription(e.target.value)} className="input" rows={2} />
            </label>
          </div>
          <button
            disabled={!name.trim() || createMut.isPending}
            onClick={() => createMut.mutate()}
            className="btn-primary"
          >{createMut.isPending ? "Creating..." : "Create"}</button>
        </section>
      )}

      <section className="card overflow-hidden">
        {!sequences?.length ? (
          <p className="text-sm text-slate-500 p-8 text-center">No sequences yet. Create one above, or ✨ generate with AI.</p>
        ) : (
          <table className="w-full">
            <thead className="bg-slate-50/80 border-b border-slate-200">
              <tr>
                <th className="th">Name</th>
                <th className="th">Status</th>
                <th className="th text-right">Steps</th>
                <th className="th text-right">Active</th>
                <th className="th">Timezone / window</th>
                <th className="th text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {sequences.map((s: SequenceOut) => (
                <tr key={s.id} className="hover:bg-slate-50/60 transition-colors">
                  <td className="td">
                    <Link to={`/sequences/${s.id}`} className="font-medium text-brand-700 hover:text-brand-800 hover:underline">{s.name}</Link>
                    {s.description && <div className="text-xs text-slate-400 line-clamp-1 max-w-md mt-0.5">{s.description}</div>}
                  </td>
                  <td className="td"><span className={STATUS_CHIP[s.status]}>{s.status}</span></td>
                  <td className="td text-right tabular-nums">{s.step_count}</td>
                  <td className="td text-right tabular-nums">{s.active_enrolments}</td>
                  <td className="td text-xs font-mono text-slate-500">{s.timezone} · {s.send_window_start.slice(0,5)}–{s.send_window_end.slice(0,5)}</td>
                  <td className="td text-right space-x-3 text-xs whitespace-nowrap">
                    {s.status !== "archived" && s.step_count > 0 ? (
                      <button onClick={() => testNowMut.mutate(s.id)} disabled={testNowMut.isPending} className="font-semibold text-brand-600 hover:text-brand-700 disabled:text-slate-300">⚡ Test now</button>
                    ) : null}
                    {s.status === "draft" || s.status === "paused" ? (
                      <button onClick={() => statusMut.mutate({ id: s.id, status: "active" })} className="font-medium text-emerald-600 hover:text-emerald-700">Activate</button>
                    ) : null}
                    {s.status === "active" ? (
                      <button onClick={() => statusMut.mutate({ id: s.id, status: "paused" })} className="font-medium text-amber-600 hover:text-amber-700">Pause</button>
                    ) : null}
                    {s.status !== "archived" ? (
                      <button onClick={() => { if (confirm("Archive this sequence? All active enrolments will be stopped.")) statusMut.mutate({ id: s.id, status: "archived" }); }} className="font-medium text-slate-400 hover:text-rose-600">Archive</button>
                    ) : (
                      <button onClick={() => { if (confirm("Permanently delete this sequence?")) deleteMut.mutate(s.id); }} className="font-medium text-rose-500 hover:text-rose-700">Delete</button>
                    )}
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
