import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import {
  SequenceOut,
  changeStatus,
  createSequence,
  deleteSequence,
  listSequences,
} from "../api/sequences";
import AISequenceModal from "../components/AISequenceModal";

const STATUS_CHIP: Record<string, string> = {
  draft: "bg-slate-100 text-slate-700",
  active: "bg-emerald-100 text-emerald-800",
  paused: "bg-amber-100 text-amber-800",
  archived: "bg-rose-100 text-rose-800",
};

export default function Sequences() {
  const qc = useQueryClient();
  const { data: sequences } = useQuery({ queryKey: ["sequences"], queryFn: listSequences });

  const [showNew, setShowNew] = useState(false);
  const [showAI, setShowAI] = useState(false);
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

  const statusMut = useMutation({
    mutationFn: async ({ id, status }: { id: number; status: any }) => changeStatus(id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sequences"] }),
  });

  const deleteMut = useMutation({
    mutationFn: async (id: number) => deleteSequence(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sequences"] }),
  });

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold">Sequences</h2>
          <p className="text-sm text-slate-500 mt-1">Multi-step outreach cadences. Each sequence runs in its own timezone + business hours.</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => { setShowAI(true); setShowNew(false); }}
            className="border rounded px-3 py-1.5 bg-violet-600 text-white hover:bg-violet-700"
          >
            AI Sequence
          </button>
          <Link
            to="/sequences/generate"
            className="border rounded px-3 py-1.5 bg-sky-600 text-white hover:bg-sky-700"
          >
            + AI sequence (RAG)
          </Link>
          <button onClick={() => { setShowNew(s => !s); setShowAI(false); }} className="border rounded px-3 py-1.5 bg-emerald-600 text-white hover:bg-emerald-700">

            {showNew ? "Cancel" : "+ New sequence"}
          </button>
        </div>
      </div>

      {showNew && (
        <section className="rounded border bg-white p-4 space-y-3">
          <div className="grid grid-cols-12 gap-3 text-sm">
            <label className="col-span-6"><span className="block text-slate-600 mb-1">Name</span>
              <input value={name} onChange={e => setName(e.target.value)} className="w-full border rounded px-2 py-1.5" placeholder="Q3 Enterprise Outbound" />
            </label>
            <label className="col-span-6"><span className="block text-slate-600 mb-1">Timezone</span>
              <input value={tz} onChange={e => setTz(e.target.value)} className="w-full border rounded px-2 py-1.5 font-mono" placeholder="Asia/Kolkata" />
            </label>
            <label className="col-span-12"><span className="block text-slate-600 mb-1">Description (optional)</span>
              <textarea value={description} onChange={e => setDescription(e.target.value)} className="w-full border rounded px-2 py-1.5" rows={2} />
            </label>
          </div>
          <button
            disabled={!name.trim() || createMut.isPending}
            onClick={() => createMut.mutate()}
            className="border rounded px-4 py-1.5 bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300"
          >{createMut.isPending ? "Creating..." : "Create"}</button>
        </section>
      )}

      {showAI && <AISequenceModal onClose={() => setShowAI(false)} />}

      <section className="rounded border bg-white p-4">
        {!sequences?.length ? (
          <p className="text-sm text-slate-500">No sequences yet. Create one above.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-1 pr-3">Name</th>
                <th className="py-1 pr-3">Status</th>
                <th className="py-1 pr-3">Steps</th>
                <th className="py-1 pr-3">Active enrolments</th>
                <th className="py-1 pr-3">Timezone / window</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sequences.map((s: SequenceOut) => (
                <tr key={s.id} className="border-t">
                  <td className="py-2 pr-3">
                    <Link to={`/sequences/${s.id}`} className="font-medium text-sky-700 hover:underline">{s.name}</Link>
                    {s.description && <div className="text-xs text-slate-500 line-clamp-1 max-w-md">{s.description}</div>}
                  </td>
                  <td className="py-2 pr-3">
                    <span className={`px-2 py-0.5 rounded text-xs ${STATUS_CHIP[s.status]}`}>{s.status}</span>
                  </td>
                  <td className="py-2 pr-3">{s.step_count}</td>
                  <td className="py-2 pr-3">{s.active_enrolments}</td>
                  <td className="py-2 pr-3 text-xs font-mono">{s.timezone} {s.send_window_start.slice(0,5)}–{s.send_window_end.slice(0,5)}</td>
                  <td className="py-2 space-x-2 text-xs">
                    {s.status === "draft" || s.status === "paused" ? (
                      <button onClick={() => statusMut.mutate({ id: s.id, status: "active" })} className="text-emerald-700 hover:underline">activate</button>
                    ) : null}
                    {s.status === "active" ? (
                      <button onClick={() => statusMut.mutate({ id: s.id, status: "paused" })} className="text-amber-700 hover:underline">pause</button>
                    ) : null}
                    {s.status !== "archived" ? (
                      <button onClick={() => { if (confirm("Archive this sequence? All active enrolments will be stopped.")) statusMut.mutate({ id: s.id, status: "archived" }); }} className="text-rose-700 hover:underline">archive</button>
                    ) : (
                      <button onClick={() => { if (confirm("Permanently delete this sequence?")) deleteMut.mutate(s.id); }} className="text-rose-700 hover:underline">delete</button>
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
