import { useMutation } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  AISequenceDraft,
  generateAISequence,
  saveAISequence,
  uploadKnowledge,
} from "../api/aiSequences";
import { apiErrorMessage } from "../api/errors";
import AISequenceStepBreakdown from "./AISequenceStepBreakdown";
import { SequenceFlowPreview } from "./SequenceFlowBuilder";
import { draftStepsToPreviewSteps } from "../lib/aiSequencePreview";

const CHANNEL_LABEL: Record<string, string> = {
  email: "Email",
  linkedin_dm: "LinkedIn Message",
  linkedin_connect: "LinkedIn Connection",
  linkedin_like: "LinkedIn Visit + Like",
  call: "Call Task",
  sms: "SMS",
  whatsapp: "WhatsApp",
};

interface Props {
  onClose: () => void;
}

export default function AISequenceModal({ onClose }: Props) {
  const navigate = useNavigate();
  const [files, setFiles] = useState<File[]>([]);
  const [knowledgeId, setKnowledgeId] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string>("");
  const [prompt, setPrompt] = useState("");
  const [draft, setDraft] = useState<AISequenceDraft | null>(null);
  const [selectedStepIndex, setSelectedStepIndex] = useState<number | null>(null);
  const [error, setError] = useState<string>("");
  const stepEditRefs = useRef<(HTMLLIElement | null)[]>([]);

  const previewSteps = useMemo(
    () => (draft ? draftStepsToPreviewSteps(draft.steps) : []),
    [draft],
  );

  const selectedPreviewStepId =
    selectedStepIndex != null && previewSteps[selectedStepIndex]
      ? previewSteps[selectedStepIndex].id
      : null;

  function selectStepByIndex(index: number) {
    setSelectedStepIndex(index);
    stepEditRefs.current[index]?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  const uploadMut = useMutation({
    mutationFn: () => uploadKnowledge(files),
    onSuccess: r => {
      setKnowledgeId(r.knowledge_id);
      setUploadStatus(
        `Indexed ${r.chunks_indexed} chunks from ${r.files_processed} file(s): ${r.filenames.join(", ")}`,
      );
      setError("");
    },
    onError: (e: unknown) => setError(apiErrorMessage(e, "Upload failed")),
  });

  const generateMut = useMutation({
    mutationFn: () => generateAISequence(prompt, knowledgeId),
    onMutate: () => setError(""),
    onSuccess: r => {
      setDraft(r.draft);
      setSelectedStepIndex(null);
      setError("");
    },
    onError: (e: unknown) => setError(apiErrorMessage(e, "Generation failed")),
  });

  const saveMut = useMutation({
    mutationFn: () => {
      if (!draft) throw new Error("No draft to save");
      return saveAISequence(draft, knowledgeId);
    },
    onSuccess: seq => {
      navigate(`/sequences/${seq.id}`);
    },
    onError: (e: unknown) => setError(apiErrorMessage(e, "Save failed")),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className={`bg-white rounded-lg shadow-xl w-full max-h-[90vh] overflow-y-auto ${draft ? "max-w-5xl" : "max-w-3xl"}`}>
        <div className="sticky top-0 bg-white border-b px-6 py-4 flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold">AI Sequence</h2>
            <p className="text-sm text-slate-500 mt-0.5">
              Upload knowledge, describe your campaign, and let AI build the sequence.
            </p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-2xl leading-none">&times;</button>
        </div>

        <div className="p-6 space-y-6">
          {error && (
            <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded px-3 py-2">{error}</div>
          )}

          {/* Knowledge upload */}
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-800">1. Knowledge Base (optional)</h3>
            <p className="text-xs text-slate-500">PDF, DOCX, TXT, or CSV — content is embedded in Qdrant for context.</p>
            <input
              type="file"
              multiple
              accept=".pdf,.docx,.txt,.csv"
              onChange={e => setFiles(Array.from(e.target.files || []))}
              className="text-sm w-full"
            />
            {files.length > 0 && (
              <div className="text-xs text-slate-600">{files.map(f => f.name).join(", ")}</div>
            )}
            <button
              disabled={!files.length || uploadMut.isPending}
              onClick={() => uploadMut.mutate()}
              className="border rounded px-3 py-1.5 text-sm bg-slate-100 hover:bg-slate-200 disabled:opacity-50"
            >
              {uploadMut.isPending ? "Uploading..." : "Upload & index"}
            </button>
            {uploadStatus && <p className="text-xs text-emerald-700">{uploadStatus}</p>}
          </section>

          {/* Prompt */}
          <section className="space-y-2">
            <h3 className="text-sm font-semibold text-slate-800">2. Campaign Prompt</h3>
            <textarea
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              rows={5}
              className="w-full border rounded px-3 py-2 text-sm"
              placeholder='Example: "Create a cold outreach sequence for SaaS founders to book demo meetings. First send LinkedIn connection, wait 2 days, send LinkedIn message, then email."'
            />
            <button
              disabled={prompt.trim().length < 10 || generateMut.isPending}
              onClick={() => generateMut.mutate()}
              className="border rounded px-4 py-1.5 text-sm bg-violet-600 text-white hover:bg-violet-700 disabled:bg-slate-300"
            >
              {generateMut.isPending ? "Generating..." : "Generate sequence"}
            </button>
          </section>

          {/* Preview */}
          {draft && (
            <section className="space-y-3 border-t pt-4">
              <h3 className="text-sm font-semibold text-slate-800">3. Preview & edit</h3>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <label className="col-span-2">
                  <span className="block text-slate-600 mb-1">Sequence name</span>
                  <input
                    value={draft.name}
                    onChange={e => setDraft({ ...draft, name: e.target.value })}
                    className="w-full border rounded px-2 py-1.5"
                  />
                </label>
                <label className="col-span-2">
                  <span className="block text-slate-600 mb-1">Description</span>
                  <textarea
                    value={draft.description || ""}
                    onChange={e => setDraft({ ...draft, description: e.target.value })}
                    className="w-full border rounded px-2 py-1.5"
                    rows={2}
                  />
                </label>
                <label>
                  <span className="block text-slate-600 mb-1">Timezone</span>
                  <input
                    value={draft.timezone}
                    onChange={e => setDraft({ ...draft, timezone: e.target.value })}
                    className="w-full border rounded px-2 py-1.5 font-mono text-xs"
                  />
                </label>
                <label className="flex items-end gap-2 pb-1.5">
                  <input
                    type="checkbox"
                    checked={draft.ai_followups_enabled}
                    onChange={e => setDraft({ ...draft, ai_followups_enabled: e.target.checked })}
                  />
                  <span className="text-slate-600">AI follow-ups enabled</span>
                </label>
              </div>

              <SequenceFlowPreview
                steps={previewSteps}
                selectedStepId={selectedPreviewStepId}
                onSelectStep={step => {
                  const index = previewSteps.findIndex(s => s.id === step.id);
                  if (index >= 0) selectStepByIndex(index);
                }}
              />

              <AISequenceStepBreakdown
                steps={draft.steps}
                selectedIndex={selectedStepIndex}
                onSelectStep={selectStepByIndex}
              />

              <div>
                <h4 className="text-sm font-semibold text-slate-800 mb-2">Edit steps</h4>
              </div>

              <ol className="space-y-3">
                {draft.steps.map((step, idx) => (
                  <li
                    key={idx}
                    ref={el => {
                      stepEditRefs.current[idx] = el;
                    }}
                    className={`border rounded p-3 space-y-2 transition-colors ${
                      selectedStepIndex === idx ? "bg-violet-50 border-violet-300" : "bg-slate-50"
                    }`}
                  >
                    <div className="flex items-center justify-between text-xs text-slate-500">
                      <span className="font-medium text-slate-700">
                        Step {idx + 1} · {CHANNEL_LABEL[step.channel] || step.channel}
                      </span>
                      <span>delay {step.delay_days}d {step.delay_hours}h</span>
                    </div>
                    <div className="grid grid-cols-4 gap-2 text-xs">
                      <label className="col-span-2">
                        Channel
                        <select
                          value={step.channel}
                          onChange={e => {
                            const steps = [...draft.steps];
                            steps[idx] = { ...step, channel: e.target.value as typeof step.channel };
                            setDraft({ ...draft, steps });
                          }}
                          className="w-full border rounded px-1 py-1 mt-0.5"
                        >
                          <option value="email">Email</option>
                          <option value="linkedin_connect">LinkedIn Connect</option>
                          <option value="linkedin_dm">LinkedIn Message</option>
                          <option value="call">Call</option>
                          <option value="sms">SMS</option>
                          <option value="whatsapp">WhatsApp</option>
                        </select>
                      </label>
                      <label>
                        Delay days
                        <input
                          type="number" min={0} max={365}
                          value={step.delay_days}
                          onChange={e => {
                            const steps = [...draft.steps];
                            steps[idx] = { ...step, delay_days: Number(e.target.value) };
                            setDraft({ ...draft, steps });
                          }}
                          className="w-full border rounded px-1 py-1 mt-0.5"
                        />
                      </label>
                      <label>
                        Delay hours
                        <input
                          type="number" min={0} max={23}
                          value={step.delay_hours}
                          onChange={e => {
                            const steps = [...draft.steps];
                            steps[idx] = { ...step, delay_hours: Number(e.target.value) };
                            setDraft({ ...draft, steps });
                          }}
                          className="w-full border rounded px-1 py-1 mt-0.5"
                        />
                      </label>
                    </div>
                    {step.channel === "email" && (
                      <label className="block text-xs">
                        Subject
                        <input
                          value={step.subject || ""}
                          onChange={e => {
                            const steps = [...draft.steps];
                            steps[idx] = { ...step, subject: e.target.value };
                            setDraft({ ...draft, steps });
                          }}
                          className="w-full border rounded px-2 py-1 mt-0.5"
                        />
                      </label>
                    )}
                    <label className="block text-xs">
                      Content
                      <textarea
                        value={step.body}
                        onChange={e => {
                          const steps = [...draft.steps];
                          steps[idx] = { ...step, body: e.target.value };
                          setDraft({ ...draft, steps });
                        }}
                        rows={4}
                        className="w-full border rounded px-2 py-1 mt-0.5 font-mono"
                      />
                    </label>
                  </li>
                ))}
              </ol>

              <div className="flex gap-3 pt-2">
                <button
                  disabled={saveMut.isPending}
                  onClick={() => saveMut.mutate()}
                  className="border rounded px-4 py-1.5 bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-300"
                >
                  {saveMut.isPending ? "Saving..." : "Save sequence"}
                </button>
                <button
                  onClick={() => generateMut.mutate()}
                  disabled={generateMut.isPending}
                  className="border rounded px-4 py-1.5 text-violet-700 hover:bg-violet-50"
                >
                  Regenerate all
                </button>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
