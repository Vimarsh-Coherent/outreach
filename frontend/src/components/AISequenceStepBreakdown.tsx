import type { AIStepDraft } from "../api/aiSequences";
import { buildDetailedStepBreakdown } from "../lib/aiSequencePreview";

interface Props {
  steps: AIStepDraft[];
  selectedIndex: number | null;
  onSelectStep: (index: number) => void;
}

export default function AISequenceStepBreakdown({ steps, selectedIndex, onSelectStep }: Props) {
  const lines = buildDetailedStepBreakdown(steps);

  return (
    <section className="space-y-2">
      <div>
        <h4 className="text-sm font-semibold text-slate-800">Detailed steps</h4>
        <p className="text-xs text-slate-500 mt-0.5">
          Step-by-step breakdown of the generated outreach flow.
        </p>
      </div>
      <ol className="space-y-2">
        {lines.map((line, index) => (
          <li key={index}>
            <button
              type="button"
              onClick={() => onSelectStep(index)}
              className={`w-full text-left rounded-lg border px-3 py-2.5 text-sm transition-colors ${
                selectedIndex === index
                  ? "border-violet-300 bg-violet-50 text-slate-800"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50"
              }`}
            >
              {line}
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}
