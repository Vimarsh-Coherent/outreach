import { api } from "./client";
import type { SequenceDetail, StepOut } from "./sequences";

export type AIStepChannel =
  | "email"
  | "linkedin_dm"
  | "linkedin_connect"
  | "linkedin_like"
  | "call"
  | "sms"
  | "whatsapp";

export interface KnowledgeUploadResult {
  knowledge_id: string;
  files_processed: number;
  chunks_indexed: number;
  filenames: string[];
}

export interface AIStepDraft {
  channel: AIStepChannel;
  delay_days: number;
  delay_hours: number;
  subject: string | null;
  body: string;
  config: Record<string, unknown>;
}

export interface AISequenceDraft {
  name: string;
  description: string | null;
  timezone: string;
  send_window_start: string;
  send_window_end: string;
  send_days_mask: number;
  ai_followups_enabled: boolean;
  steps: AIStepDraft[];
}

export interface AISequenceGenerateResponse {
  draft: AISequenceDraft;
  knowledge_snippets_used: number;
}

export async function uploadKnowledge(files: File[]): Promise<KnowledgeUploadResult> {
  const form = new FormData();
  files.forEach(f => form.append("files", f));
  return (await api.post<KnowledgeUploadResult>("/ai-sequences/knowledge/upload", form, {
    headers: { "Content-Type": "multipart/form-data" },
  })).data;
}

export async function generateAISequence(prompt: string, knowledgeId?: string | null) {
  return (await api.post<AISequenceGenerateResponse>(
    "/ai-sequences/generate",
    { prompt, knowledge_id: knowledgeId || null },
    { timeout: 120_000 },
  )).data;
}

export async function saveAISequence(draft: AISequenceDraft, knowledgeId?: string | null) {
  return (await api.post<SequenceDetail>("/ai-sequences/save", {
    draft,
    knowledge_id: knowledgeId || null,
  })).data;
}

export async function regenerateAIStep(
  sequenceId: number,
  stepId: number,
  prompt?: string | null,
) {
  return (await api.post<{ step: StepOut; sequence: SequenceDetail }>(
    `/ai-sequences/sequences/${sequenceId}/steps/${stepId}/regenerate`,
    { prompt: prompt || null },
  )).data;
}
