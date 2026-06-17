import type { StepOut } from "../api/sequences";
import { buildSequenceStepBreakdown } from "../lib/sequenceStepBreakdown";
import { sortSteps } from "../lib/sequenceSteps";

interface Props {
  steps: StepOut[];
  selectedStepId?: number | null;
  onSelectStep?: (step: StepOut) => void;
}

export default function SequenceDetailedSteps({
  steps,
  selectedStepId = null,
  onSelectStep,
}: Props) {
  const sorted = sortSteps(steps);
  const lines = buildSequenceStepBreakdown(sorted);

  if (lines.length === 0) {
    return null;
  }

  return (
    <section className="rounded-lg border bg-white shadow-sm overflow-hidden">
      <div className="border-b bg-slate-50 px-4 py-2.5">
        <h3 className="text-sm font-semibold text-slate-800">Detailed sequence</h3>
        <p className="text-xs text-slate-500 mt-0.5">
          Step-by-step description of this outreach flow.
        </p>
      </div>
      <ol className="divide-y divide-slate-100">
        {lines.map((line, index) => {
          const step = sorted[index];
          const selected = step != null && selectedStepId === step.id;
          const content = (
            <span className="text-sm text-slate-700 leading-relaxed">{line}</span>
          );

          return (
            <li key={step?.id ?? index}>
              {onSelectStep && step ? (
                <button
                  type="button"
                  onClick={() => onSelectStep(step)}
                  className={`w-full text-left px-4 py-3 transition-colors ${
                    selected ? "bg-sky-50" : "hover:bg-slate-50"
                  }`}
                >
                  {content}
                </button>
              ) : (
                <div className="px-4 py-3">{content}</div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
