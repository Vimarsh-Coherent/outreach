import axios from "axios";

import { api } from "./client";

export type LeadField = "email" | "first_name" | "last_name" | "phone" | "linkedin_url" | "company" | "title";

export interface LatestReply {
  event_id: number;
  occurred_at: string;
  body: string | null;
  channel: string;
  sentiment_label: string | null;
  sentiment_confidence: number | null;
  sentiment_reasoning: string | null;
}

export interface LeadOut {
  id: number;
  email: string | null;
  phone: string | null;
  linkedin_url: string | null;
  first_name: string | null;
  last_name: string | null;
  company: string | null;
  title: string | null;
  source: string | null;
  created_at: string;
  updated_at: string;
  latest_reply: LatestReply | null;
  channel_replies: Record<string, LatestReply>;
}

export interface LeadListResponse {
  items: LeadOut[];
  total: number;
  limit: number;
  offset: number;
}

export interface UploadPreviewResponse {
  token: string;
  row_count: number;
  columns: string[];
  sample_rows: Record<string, string>[];
  suggested_mapping: Record<LeadField, string | null>;
}

export interface UploadCommitResponse {
  inserted: number;
  updated: number;
  merged_within_upload: number;
  skipped_no_identity: number;
  skipped_invalid: number;
}

export async function previewUpload(file: File): Promise<UploadPreviewResponse> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await api.post<UploadPreviewResponse>("/leads/upload/preview", fd, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return r.data;
}

export async function commitUpload(body: {
  token: string;
  mapping: Record<LeadField, string | null>;
  source?: string;
}): Promise<UploadCommitResponse> {
  const r = await api.post<UploadCommitResponse>("/leads/upload/commit", body);
  return r.data;
}

export async function listLeads(params: { search?: string; limit?: number; offset?: number } = {}): Promise<LeadListResponse> {
  const r = await api.get<LeadListResponse>("/leads", { params });
  return r.data;
}

export async function createLead(dto: Partial<Omit<LeadOut, "id" | "created_at" | "updated_at" | "source">>): Promise<LeadOut> {
  const r = await api.post<LeadOut>("/leads", dto);
  return r.data;
}

export async function updateLead(id: number, dto: Partial<Omit<LeadOut, "id" | "created_at" | "updated_at" | "source">>): Promise<LeadOut> {
  const r = await api.patch<LeadOut>(`/leads/${id}`, dto);
  return r.data;
}

export async function deleteLead(id: number): Promise<void> {
  await api.delete(`/leads/${id}`);
}

export function formatAxiosError(e: unknown): string {
  if (axios.isAxiosError(e)) {
    return e.response?.data?.detail ? `${e.response.status}: ${JSON.stringify(e.response.data.detail)}` : e.message;
  }
  return String(e);
}
