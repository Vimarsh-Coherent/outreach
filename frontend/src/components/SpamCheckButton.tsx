import { useMutation } from "@tanstack/react-query";

import { SpamFinding, spamCheck } from "../api/tools";

const VERDICT_BADGE: Record<string, string> = {
  good: "badge-green",
  warning: "badge-amber",
  poor: "badge-rose",
};
const SEV_COLOR: Record<SpamFinding["severity"], string> = {
  high: "text-rose-600",
  medium: "text-amber-600",
  low: "text-slate-500",
};

/** On-demand spam/deliverability score for the current subject+body draft. */
export default function SpamCheckButton({
  subject,
  body,
}: {
  subject: string | null;
  body: string;
}) {
  const mut = useMutation({ mutationFn: () => spamCheck(subject, body) });
  const r = mut.data;

  return (
    <div className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-slate-700">Spam / deliverability check</span>
        <button
          type="button"
          onClick={() => mut.mutate()}
          disabled={mut.isPending || !body.trim()}
          className="btn-ghost btn-sm"
        >
          {mut.isPending ? "Checking…" : "Run check"}
        </button>
      </div>

      {r && (
        <>
          <div className="flex items-center gap-2">
            <span className={VERDICT_BADGE[r.verdict] ?? "badge-slate"}>
              {r.score}/100 · {r.verdict}
            </span>
          </div>
          {r.findings.length === 0 ? (
            <p className="text-xs text-emerald-700">Looks clean — no deliverability flags.</p>
          ) : (
            <ul className="space-y-1 text-xs">
              {r.findings.map((f, i) => (
                <li key={i} className={SEV_COLOR[f.severity]}>
                  <span className="font-medium">{f.message}</span>
                  {f.suggestion && <span className="text-slate-400"> — {f.suggestion}</span>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
