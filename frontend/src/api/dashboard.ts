import { api } from "./client";

export interface DashboardSummary {
  period_days: number;
  enrolled: number;
  contacted: number;
  replied: number;
  positive_replies: number;
  bounced: number;
  unsubscribed: number;
  reply_rate: number;
  positive_rate: number;
}

export interface SentimentBucket {
  bucket_start: string;
  positive: number;
  interested: number;
  objection: number;
  negative: number;
  unsubscribe: number;
  auto_reply: number;
  neutral: number;
}

export interface SentimentTimeseriesResponse {
  bucket: "day" | "week";
  points: SentimentBucket[];
}

export interface SequenceStats {
  id: number;
  name: string;
  status: string;
  sends: number;
  replies: number;
  bounces: number;
  positive_replies: number;
  reply_rate: number;
}

export interface HotLead {
  lead_id: number;
  name: string | null;
  email: string | null;
  company: string | null;
  title: string | null;
  latest_sentiment: string;
  latest_confidence: number;
  latest_reply_at: string;
  sequence_id: number;
  sequence_name: string | null;
}

export interface AtRiskEnrolment {
  enrolment_id: number;
  lead_id: number;
  lead_email: string | null;
  lead_name: string | null;
  sequence_id: number;
  sequence_name: string | null;
  status: string;
  stuck_since: string | null;
  reason: string | null;
}

export async function getSummary(days = 30) {
  return (await api.get<DashboardSummary>("/dashboard/summary", { params: { days } })).data;
}
export async function getSentimentTimeseries(days = 30, bucket: "day" | "week" = "day") {
  return (await api.get<SentimentTimeseriesResponse>("/dashboard/sentiment-timeseries", {
    params: { days, bucket },
  })).data;
}
export async function getSequenceStats(days = 30) {
  return (await api.get<SequenceStats[]>("/dashboard/sequences", { params: { days } })).data;
}
export async function getHotLeads(days = 7, limit = 25) {
  return (await api.get<HotLead[]>("/dashboard/hot-leads", { params: { days, limit } })).data;
}
export async function getAtRisk(hours = 24, limit = 25) {
  return (await api.get<AtRiskEnrolment[]>("/dashboard/at-risk", { params: { hours, limit } })).data;
}
