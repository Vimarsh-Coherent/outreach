import { api } from "./client";

export interface SpamFinding {
  category: string;
  severity: "low" | "medium" | "high";
  message: string;
  suggestion?: string | null;
}

export interface SpamCheckResult {
  score: number;
  verdict: "good" | "warning" | "poor";
  findings: SpamFinding[];
}

export async function spamCheck(subject: string | null, body: string): Promise<SpamCheckResult> {
  return (await api.post<SpamCheckResult>("/tools/spam-check", { subject, body })).data;
}
