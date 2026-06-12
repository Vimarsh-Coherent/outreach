import { api } from "./client";

export interface DraftRequest {
  lead_id: number;
  sequence_id?: number;
  template_subject?: string;
  template_body?: string;
}

export interface DraftResponse {
  subject: string;
  body: string;
  notes: string;
  template_subject: string;
  template_body: string;
  similar_snippets: string[];
  prior_history_count: number;
  model: string;
}

export interface SendRequest {
  lead_id: number;
  to_email: string;
  subject: string;
  body: string;
  channel_id?: number;
}

export interface SendResponse {
  ok: boolean;
  detail: string;
  provider_message_id?: string | null;
}

export async function draftFollowup(req: DraftRequest): Promise<DraftResponse> {
  return (await api.post<DraftResponse>("/followups/draft", req)).data;
}

export async function sendFollowup(req: SendRequest): Promise<SendResponse> {
  return (await api.post<SendResponse>("/followups/send", req)).data;
}
