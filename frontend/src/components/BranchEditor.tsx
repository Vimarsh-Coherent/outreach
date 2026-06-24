import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  BranchCondition,
  StepOut,
  StepTransition,
  setStepTransitions,
} from "../api/sequences";
import { getStepChannelMeta, sortSteps } from "../lib/sequenceSteps";

const CONDITIONS: { value: BranchCondition; label: string }[] = [
  { value: "replied", label: "If lead replies" },
  { value: "opened", label: "If email opened" },
  { value: "clicked", label: "If link clicked" },
  { value: "default", label: "Otherwise (default)" },
];

/** Edit a step's conditional branches. Reply/open/click are evaluated after a
 *  wait window; the first matching branch decides the next step (or stop). */
export default function BranchEditor({
  sequenceId,
  step,
  allSteps,
}: {
  sequenceId: number;
  step: StepOut;
  allSteps: StepOut[];
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState((step.transitions?.length ?? 0) > 0);
  const [rows, setRows] = useState<StepTransition[]>(step.transitions ?? []);

  // Possible targets: any OTHER step in the sequence.
  const targets = sortSteps(allSteps).filter(s => s.id !== step.id);

  const saveMut = useMutation({
    mutationFn: async () => setStepTransitions(sequenceId, step.id, rows),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sequence", sequenceId] }),
    onError: (e) => alert(String((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? e)),
  });

  const add = () => setRows(r => [...r, { on: "replied", to_step_id: null }]);
  const patch = (i: number, p: Partial<StepTransition>) =>
    setRows(r => r.map((x, idx) => (idx === i ? { ...x, ...p } : x)));
  const remove = (i: number) => setRows(r => r.filter((_, idx) => idx !== i));

  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button type="button" onClick={() => setOpen(o => !o)} className="flex w-full items-center justify-between px-3 py-2 text-left">
        <span className="text-xs font-semibold text-slate-700">
          Branching {rows.length > 0 ? `· ${rows.length} rule${rows.length > 1 ? "s" : ""}` : "(linear)"}
        </span>
        <span className="text-slate-400">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-slate-100 p-3">
          {rows.length === 0 && (
            <p className="text-xs text-slate-500">
              No branches — this step continues to the next step in order. Add a rule to send leads
              down different paths based on what they do.
            </p>
          )}

          {rows.map((t, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2 text-xs">
              <select value={t.on} onChange={e => patch(i, { on: e.target.value as BranchCondition })}
                className="input !w-auto py-1">
                {CONDITIONS.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
              <span className="text-slate-400">→</span>
              <select
                value={t.to_step_id ?? ""}
                onChange={e => patch(i, { to_step_id: e.target.value === "" ? null : Number(e.target.value) })}
                className="input !w-auto py-1"
              >
                <option value="">Stop sequence</option>
                {targets.map(s => {
                  const m = getStepChannelMeta(s.channel);
                  return <option key={s.id} value={s.id}>Go to step {s.step_order} · {m.label}</option>;
                })}
              </select>
              <button type="button" onClick={() => remove(i)} className="text-rose-600 hover:underline">remove</button>
            </div>
          ))}

          <div className="flex items-center gap-2">
            <button type="button" onClick={add} className="btn-ghost btn-sm">+ Add rule</button>
            <button type="button" disabled={saveMut.isPending} onClick={() => saveMut.mutate()} className="btn-primary btn-sm">
              {saveMut.isPending ? "Saving…" : "Save branches"}
            </button>
          </div>
          {rows.some(r => r.on !== "default") && (
            <p className="text-[11px] text-slate-400">
              Reply/open/click are checked after a wait window (default 48h); the first matching rule wins, else it falls through to the next step in order.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
