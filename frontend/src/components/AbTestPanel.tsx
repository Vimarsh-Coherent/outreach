import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  AbVariant,
  StepOut,
  getAbStats,
  promoteVariant,
  updateStep,
} from "../api/sequences";

/** A/B variants live in step.config.variants. This panel edits them and shows
 *  live per-variant performance (sent / open / click / reply rates) + promote. */
export default function AbTestPanel({
  sequenceId,
  step,
}: {
  sequenceId: number;
  step: StepOut;
}) {
  const qc = useQueryClient();
  const isEmail = step.channel === "email";

  const initial: AbVariant[] = Array.isArray((step.config as { variants?: AbVariant[] })?.variants)
    ? ((step.config as { variants?: AbVariant[] }).variants as AbVariant[])
    : [];
  const [variants, setVariants] = useState<AbVariant[]>(initial);
  const [open, setOpen] = useState(initial.length > 0);

  const { data: stats } = useQuery({
    queryKey: ["ab-stats", sequenceId, step.id],
    queryFn: () => getAbStats(sequenceId, step.id),
    enabled: open && initial.length >= 2,
    refetchInterval: 15_000,
  });

  const saveMut = useMutation({
    mutationFn: async (next: AbVariant[]) =>
      updateStep(sequenceId, step.id, {
        channel: step.channel,
        subject: step.subject,
        body: step.body,
        delay_days: step.delay_days,
        delay_hours: step.delay_hours,
        config: { ...(step.config ?? {}), variants: next.length >= 2 ? next : undefined },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sequence", sequenceId] }),
  });

  const promoteMut = useMutation({
    mutationFn: async (label: string) => promoteVariant(sequenceId, step.id, label),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sequence", sequenceId] });
      qc.invalidateQueries({ queryKey: ["ab-stats", sequenceId, step.id] });
    },
  });

  const addVariant = () => {
    const label = String.fromCharCode(65 + variants.length); // A, B, C…
    setVariants(v => [...v, { label, subject: isEmail ? step.subject ?? "" : null, body: step.body, weight: 1 }]);
    setOpen(true);
  };
  const patch = (i: number, p: Partial<AbVariant>) =>
    setVariants(v => v.map((x, idx) => (idx === i ? { ...x, ...p } : x)));
  const remove = (i: number) => setVariants(v => v.filter((_, idx) => idx !== i));

  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center justify-between px-3 py-2 text-left"
      >
        <span className="text-xs font-semibold text-slate-700">
          A/B test {initial.length >= 2 ? `· ${initial.length} variants live` : "(optional)"}
        </span>
        <span className="text-slate-400">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-slate-100 p-3">
          {variants.length === 0 && (
            <p className="text-xs text-slate-500">
              Add a second message to split-test it. Leads are split deterministically by weight;
              opens/clicks/replies are tracked per variant.
            </p>
          )}

          {variants.map((v, i) => {
            const st = stats?.stats.find(s => s.label === v.label);
            const isWinner = stats?.winner === v.label;
            return (
              <div key={i} className={`rounded-md border p-2 ${isWinner ? "border-emerald-300 bg-emerald-50/40" : "border-slate-200"}`}>
                <div className="mb-1 flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-600">
                    Variant {v.label}{isWinner && <span className="badge-green ml-2">winner</span>}
                  </span>
                  <div className="flex items-center gap-2">
                    <label className="text-[11px] text-slate-500">weight
                      <input type="number" min={1} value={v.weight}
                        onChange={e => patch(i, { weight: Math.max(1, Number(e.target.value)) })}
                        className="ml-1 w-12 rounded border px-1 py-0.5 text-xs" />
                    </label>
                    {st && st.sent > 0 && (
                      <button type="button" onClick={() => promoteMut.mutate(v.label)}
                        className="btn-ghost btn-sm" title="Lock this variant in as the step message">
                        Promote
                      </button>
                    )}
                    <button type="button" onClick={() => remove(i)} className="text-rose-600 text-xs hover:underline">remove</button>
                  </div>
                </div>
                {isEmail && (
                  <input value={v.subject ?? ""} onChange={e => patch(i, { subject: e.target.value })}
                    placeholder="Subject" className="input mb-1 text-xs" />
                )}
                <textarea value={v.body} onChange={e => patch(i, { body: e.target.value })}
                  rows={3} className="input font-mono text-xs" placeholder="Variant body" />
                {st && (
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
                    <span>sent <b className="text-slate-700">{st.sent}</b></span>
                    <span>open <b className="text-slate-700">{st.open_rate}%</b></span>
                    <span>click <b className="text-slate-700">{st.click_rate}%</b></span>
                    <span>reply <b className="text-slate-700">{st.reply_rate}%</b></span>
                    <span>positive <b className="text-emerald-700">{st.positive_rate}%</b></span>
                  </div>
                )}
              </div>
            );
          })}

          <div className="flex items-center gap-2">
            <button type="button" onClick={addVariant} className="btn-ghost btn-sm">+ Add variant</button>
            <button
              type="button"
              disabled={saveMut.isPending}
              onClick={() => saveMut.mutate(variants)}
              className="btn-primary btn-sm"
            >
              {saveMut.isPending ? "Saving…" : variants.length >= 2 ? "Save A/B test" : "Save (disable A/B)"}
            </button>
            {stats && stats.winner == null && initial.length >= 2 && (
              <span className="text-[11px] text-slate-400">collecting data… (winner shown after ~20 sends/variant)</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
