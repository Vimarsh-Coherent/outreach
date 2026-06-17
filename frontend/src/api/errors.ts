import axios from "axios";

/** Extract a user-visible message from an axios/API error. */
export function apiErrorMessage(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d: { msg?: string; loc?: string[] }) =>
          d.msg ? `${(d.loc || []).slice(-1)[0] || "field"}: ${d.msg}` : JSON.stringify(d),
        )
        .join("; ");
    }
    if (detail && typeof detail === "object") return JSON.stringify(detail);
    if (err.code === "ECONNABORTED") return "Request timed out — AI generation can take up to 2 minutes. Please retry.";
    if (err.response?.status) {
      return `${fallback} (HTTP ${err.response.status})`;
    }
    if (err.message) return err.message;
  }
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}
