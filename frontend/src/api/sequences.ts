import { api } from "./client";

export type SequenceStatus = "draft" | "active" | "paused" | "archived";
export type StepChannel =
  | "email"
  | "linkedin_dm"
  | "linkedin_connect"
  | "linkedin_like"
  | "call"
  | "sms"
  | "whatsapp";

export interface StepOut {
  id: number;
  sequence_id: number;
  step_order: number;
  channel: StepChannel;
  delay_days: number;
  delay_hours: number;
  subject: string | null;
  body: string;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SequenceOut {
  id: number;
  name: string;
  description: string | null;
  status: SequenceStatus;
  timezone: string;
  send_window_start: string; // "HH:MM:SS"
  send_window_end: string;
  send_days_mask: number;
  ai_followups_enabled: boolean;
  ai_knowledge_id?: string | null;
  step_count: number;
  active_enrolments: number;
  created_at: string;
  updated_at: string;
}

export interface SequenceDetail extends SequenceOut {
  steps: StepOut[];
}

export interface SequenceCreate {
  name: string;
  description?: string | null;
  timezone?: string;
  send_window_start?: string;
  send_window_end?: string;
  send_days_mask?: number;
  ai_followups_enabled?: boolean;
}

export interface StepCreate {
  channel: StepChannel;
  step_order?: number;
  delay_days?: number;
  delay_hours?: number;
  subject?: string | null;
  body: string;
  config?: Record<string, unknown>;
}

export interface EnrolmentResult {
  enrolled: number;
  deduped: number;
  skipped_no_identity: number;
}

export interface SequenceGenerateResponse {
  sequence: SequenceDetail;
  warnings: string[];
}

export type GroundingVerdict = "grounded" | "weak" | "possible_hallucination";

export interface GroundingDocument {
  id: number;
  filename: string;
  status: string;
  indexed: boolean;
}

export interface StepGrounding {
  step_id: number;
  step_order: number;
  channel: StepChannel;
  subject: string | null;
  similarity: number;
  mean_top_k: number;
  supported_ratio: number;
  verdict: GroundingVerdict;
  best_chunk_filename: string | null;
}

export interface SequenceGrounding {
  sequence_id: number;
  documents: GroundingDocument[];
  has_indexed_docs: boolean;
  chunk_count: number;
  avg_similarity: number;
  steps: StepGrounding[];
  note: string | null;
}

export async function getSequenceGrounding(id: number) {
  return (await api.get<SequenceGrounding>(`/sequences/${id}/grounding`)).data;
}

export async function listSequences() {
  return (await api.get<SequenceOut[]>("/sequences")).data;
}
export async function getSequence(id: number) {
  return (await api.get<SequenceDetail>(`/sequences/${id}`)).data;
}
export async function createSequence(dto: SequenceCreate) {
  return (await api.post<SequenceOut>("/sequences", dto)).data;
}
export async function generateSequenceFromPrompt(dto: {
  prompt: string;
  timezone?: string;
  document_ids?: number[];
}) {
  return (await api.post<SequenceGenerateResponse>("/sequences/generate", dto)).data;
}

export interface SequenceGenerateRequest {
  name: string;
  description: string;
  channels: ("email" | "linkedin")[];
  num_emails?: number;
  timezone?: string;
  test_mode?: boolean;
}
export async function generateSequence(dto: SequenceGenerateRequest) {
  return (await api.post<SequenceDetail>("/sequences/generate-quick", dto)).data;
}
export async function updateSequence(id: number, dto: Partial<SequenceCreate>) {
  return (await api.patch<SequenceOut>(`/sequences/${id}`, dto)).data;
}
export async function changeStatus(id: number, status: SequenceStatus) {
  return (await api.post<SequenceOut>(`/sequences/${id}/status`, { status })).data;
}

export interface TestNowResult { fired: number; activated?: boolean; enrolment_ids: number[]; }
export async function testNow(id: number) {
  return (await api.post<TestNowResult>(`/sequences/${id}/test-now`)).data;
}
export async function deleteSequence(id: number) {
  await api.delete(`/sequences/${id}`);
}

export async function addStep(sequenceId: number, dto: StepCreate) {
  return (await api.post<StepOut>(`/sequences/${sequenceId}/steps`, dto)).data;
}
export async function updateStep(sequenceId: number, stepId: number, dto: StepCreate) {
  return (await api.patch<StepOut>(`/sequences/${sequenceId}/steps/${stepId}`, dto)).data;
}
export async function deleteStep(sequenceId: number, stepId: number) {
  await api.delete(`/sequences/${sequenceId}/steps/${stepId}`);
}
export async function reorderSteps(sequenceId: number, stepIds: number[]) {
  return (await api.post<StepOut[]>(`/sequences/${sequenceId}/steps/reorder`, { step_ids: stepIds })).data;
}

export async function enrolLeads(sequenceId: number, leadIds: number[]) {
  return (await api.post<EnrolmentResult>(`/sequences/${sequenceId}/enrol`, { lead_ids: leadIds })).data;
}
