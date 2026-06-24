import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { regenerateAIStep } from "../api/aiSequences";
import {
  StepChannel,
  StepCreate,
  StepOut,
  deleteStep,
  updateStep,
} from "../api/sequences";
import { getStepChannelMeta } from "../lib/sequenceSteps";

const CHANNEL_BODY_CAP: Record<StepChannel, number> = {
  email: 16000,
  linkedin_dm: 8000,
  linkedin_connect: 300,
  linkedin_like: 0,
  call: 4000,
  sms: 1600,
  whatsapp: 4000,
};

interface Props {
  sequenceId: number;
  step: StepOut;
  aiKnowledgeId?: string | null;
  onClose: () => void;
}

export default function StepEditModal({
  sequenceId,
  step,
  aiKnowledgeId,
  onClose,
}: Props) {
  const qc = useQueryClient();
  const [channel, setChannel] = useState<StepChannel>(step.channel as StepChannel);
  const [subject, setSubject] = useState(step.subject ?? "");
  const [body, setBody] = useState(step.body);
  const [delayDays, setDelayDays] = useState(step.delay_days);
  const [delayHours, setDelayHours] = useState(step.delay_hours);

  useEffect(() => {
    setChannel(step.channel as StepChannel);
    setSubject(step.subject ?? "");
    setBody(step.body);
    setDelayDays(step.delay_days);
    setDelayHours(step.delay_hours);
  }, [step]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const saveMut = useMutation({
    mutationFn: async () => {
      const payload: StepCreate = {
        channel,
        body,
        delay_days: delayDays,
        delay_hours: delayHours,
        subject: channel === "email" ? subject : null,
        config: step.config,
      };
      return updateStep(sequenceId, step.id, payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sequence", sequenceId] });
      onClose();
    },
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteStep(sequenceId, step.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sequence", sequenceId] });
      onClose();
    },
  });

  const regenerateMut = useMutation({
    mutationFn: () => regenerateAIStep(sequenceId, step.id),
    onSuccess: data => {
      qc.invalidateQueries({ queryKey: ["sequence", sequenceId] });
      const updated = data.step;
      setChannel(updated.channel as StepChannel);
      setSubject(updated.subject ?? "");
      setBody(updated.body);
      setDelayDays(updated.delay_days);
      setDelayHours(updated.delay_hours);
    },
  });

  const meta = getStepChannelMeta(channel);
  const bodyCap = CHANNEL_BODY_CAP[channel];
  const bodyOver = body.length > bodyCap;
  const canSave =
    body.trim().length > 0 &&
    !bodyOver &&
    (channel !== "email" || subject.trim().length > 0);
  const showRegenerate = Boolean(step.config?.ai_generated || aiKnowledgeId);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/40"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="w-full sm:max-w-lg max-h-[90vh] overflow-y-auto rounded-t-xl sm:rounded-xl border border-slate-200 bg-white shadow-xl"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="step-edit-title"
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b bg-white px-4 py-3">
          <div className="flex items-center gap-2 min-w-0">
            <span className={`flex h-9 w-9 items-center justify-center rounded-lg border text-lg ${meta.accent}`}>
              {meta.icon}
            </span>
            <div className="min-w-0">
              <h3 id="step-edit-title" className="text-sm font-semibold text-slate-800 truncate">
                Edit step {step.step_order}
              </h3>
              <p className="text-xs text-slate-500">{meta.label}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="p-4 space-y-3 text-sm">
          <label className="block">
            <span className="block text-slate-600 mb-1">Channel</span>
            <select
              value={channel}
              onChange={e => setChannel(e.target.value as StepChannel)}
              className="w-full border rounded px-2 py-1.5"
            >
              <option value="email">Email</option>
              <option value="linkedin_dm">LinkedIn DM</option>
              <option value="linkedin_connect">LinkedIn connect (note)</option>
              <option value="linkedin_like">LinkedIn visit + like posts</option>
              <option value="call">Call task</option>
              <option value="sms">SMS</option>
              <option value="whatsapp">WhatsApp</option>
            </select>
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="block text-slate-600 mb-1">Delay days</span>
              <input
                type="number"
                min={0}
                max={365}
                value={delayDays}
                onChange={e => setDelayDays(Number(e.target.value))}
                className="w-full border rounded px-2 py-1.5"
              />
            </label>
            <label className="block">
              <span className="block text-slate-600 mb-1">Delay hours</span>
              <input
                type="number"
                min={0}
                max={23}
                value={delayHours}
                onChange={e => setDelayHours(Number(e.target.value))}
                className="w-full border rounded px-2 py-1.5"
              />
            </label>
          </div>

          {channel === "email" && (
            <label className="block">
              <span className="block text-slate-600 mb-1">Subject (max 250)</span>
              <input
                value={subject}
                onChange={e => setSubject(e.target.value)}
                maxLength={250}
                className="w-full border rounded px-2 py-1.5"
              />
            </label>
          )}

          <label className="block">
            <span className="block text-slate-600 mb-1">
              Body
              <span className={`ml-2 text-xs ${bodyOver ? "text-rose-600" : "text-slate-400"}`}>
                {body.length}/{bodyCap}
              </span>
            </span>
            <textarea
              value={body}
              onChange={e => setBody(e.target.value)}
              rows={8}
              className={`w-full border rounded px-2 py-1.5 font-mono text-xs ${bodyOver ? "border-rose-400" : ""}`}
            />
          </label>
        </div>

        <div className="sticky bottom-0 flex flex-wrap items-center justify-between gap-2 border-t bg-slate-50 px-4 py-3">
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                if (confirm(`Delete step ${step.step_order}?`)) deleteMut.mutate();
              }}
              disabled={deleteMut.isPending}
              className="text-xs text-rose-600 hover:underline disabled:opacity-50"
            >
              Delete
            </button>
            {showRegenerate && (
              <button
                type="button"
                onClick={() => regenerateMut.mutate()}
                disabled={regenerateMut.isPending}
                className="text-xs text-violet-700 hover:underline disabled:opacity-50"
              >
                {regenerateMut.isPending ? "Regenerating…" : "Regenerate with AI"}
              </button>
            )}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="border rounded px-3 py-1.5 text-sm hover:bg-white"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={!canSave || saveMut.isPending}
              onClick={() => saveMut.mutate()}
              className="border rounded px-3 py-1.5 text-sm bg-sky-600 text-white hover:bg-sky-700 disabled:bg-slate-300"
            >
              {saveMut.isPending ? "Saving…" : "Save changes"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
