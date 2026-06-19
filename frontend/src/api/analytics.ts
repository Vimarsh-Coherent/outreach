import { api } from "./client";

export interface AnalyticsMessage {
  occurred_at: string | null;
  direction: "sent" | "received";
  channel: string;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  contact_li_url: string | null;
  body: string;
  subject: string | null;
  sentiment_label: string | null;
  sentiment_confidence: number | null;
  ref_id: number;
  ref_type: string;
}

export interface AnalyticsStats {
  [channel: string]: { sent: number; received: number };
}

export interface AnalyticsResponse {
  items: AnalyticsMessage[];
  total: number;
  stats: AnalyticsStats;
}

export async function fetchMessages(params: {
  channel?: string;
  direction?: string;
  limit?: number;
  offset?: number;
}): Promise<AnalyticsResponse> {
  const r = await api.get<AnalyticsResponse>("/analytics/messages", { params });
  return r.data;
}
