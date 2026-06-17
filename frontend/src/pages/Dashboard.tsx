import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  getAtRisk,
  getHotLeads,
  getSentimentTimeseries,
  getSequenceStats,
  getSummary,
} from "../api/dashboard";
import FollowUpDrafter from "../components/FollowUpDrafter";

const SENTIMENT_COLORS: Record<string, string> = {
  positive: "#059669",
  interested: "#10b981",
  neutral: "#94a3b8",
  objection: "#f59e0b",
  negative: "#ef4444",
  unsubscribe: "#7c3aed",
  auto_reply: "#cbd5e1",
};

function KPI({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: string }) {
  return (
    <div className="rounded border bg-white p-4">
      <div className="text-xs text-slate-500 uppercase tracking-wide">{label}</div>
      <div className={`text-3xl font-semibold mt-1 ${accent ?? ""}`}>{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-1">{sub}</div>}
    </div>
  );
}

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function dateLabel(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export default function Dashboard() {
  const [drafting, setDrafting] = useState<null | {
    id: number; email: string | null; name: string | null; company: string | null; title: string | null;
  }>(null);
  const { data: summary } = useQuery({ queryKey: ["dash-summary"], queryFn: () => getSummary(30), refetchInterval: 60_000 });
  const { data: timeseries } = useQuery({ queryKey: ["dash-sentiment"], queryFn: () => getSentimentTimeseries(30, "day") });
  const { data: sequences } = useQuery({ queryKey: ["dash-sequences"], queryFn: () => getSequenceStats(30) });
  const { data: hotLeads } = useQuery({ queryKey: ["dash-hot"], queryFn: () => getHotLeads(7, 10) });
  const { data: atRisk } = useQuery({ queryKey: ["dash-at-risk"], queryFn: () => getAtRisk(24, 10) });

  const chartData = (timeseries?.points ?? []).map(p => ({
    ...p,
    label: dateLabel(p.bucket_start),
  }));

  const funnel = summary ? [
    { stage: "Enrolled",   value: summary.enrolled },
    { stage: "Contacted",  value: summary.contacted },
    { stage: "Replied",    value: summary.replied },
    { stage: "Positive",   value: summary.positive_replies },
  ] : [];

  return (
    <div className="space-y-6 max-w-7xl">
      <div className="flex items-end justify-between">
        <h2 className="text-2xl font-semibold">Dashboard</h2>
        {summary && <div className="text-xs text-slate-500">Last {summary.period_days} days</div>}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <KPI label="Enrolled"      value={String(summary?.enrolled ?? "—")} />
        <KPI label="Contacted"     value={String(summary?.contacted ?? "—")} />
        <KPI label="Replied"       value={String(summary?.replied ?? "—")} sub={summary ? `${pct(summary.reply_rate)} reply rate` : ""} />
        <KPI label="Positive"      value={String(summary?.positive_replies ?? "—")} sub={summary ? `${pct(summary.positive_rate)} of replies` : ""} accent="text-emerald-700" />
        <KPI label="Bounced"       value={String(summary?.bounced ?? "—")} accent="text-rose-700" />
        <KPI label="Unsubscribed"  value={String(summary?.unsubscribed ?? "—")} accent="text-violet-700" />
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        <section className="rounded border bg-white p-4">
          <h3 className="font-semibold text-slate-800 mb-3">Funnel — last 30d</h3>
          {funnel.every(f => f.value === 0) ? (
            <p className="text-sm text-slate-500">No outreach activity in the last 30 days.</p>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={funnel} layout="vertical" margin={{ left: 10, right: 30 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" />
                <YAxis dataKey="stage" type="category" width={80} />
                <Tooltip />
                <Bar dataKey="value">
                  {funnel.map((_, i) => <Cell key={i} fill={["#0ea5e9", "#0284c7", "#0369a1", "#059669"][i]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </section>

        <section className="rounded border bg-white p-4">
          <h3 className="font-semibold text-slate-800 mb-3">Reply sentiment — last 30d</h3>
          {chartData.length === 0 ? (
            <p className="text-sm text-slate-500">No classified replies yet. Once replies come in they'll appear here, color-coded by sentiment label.</p>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={chartData} margin={{ left: 0, right: 10 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="label" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Legend />
                {Object.keys(SENTIMENT_COLORS).map(k => (
                  <Bar key={k} dataKey={k} stackId="s" fill={SENTIMENT_COLORS[k]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          )}
        </section>
      </div>

      <section className="rounded border bg-white p-4">
        <h3 className="font-semibold text-slate-800 mb-3">Top sequences</h3>
        {!sequences?.length ? (
          <p className="text-sm text-slate-500">No sequences yet. <Link to="/sequences" className="text-sky-600 underline">Create one</Link>.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-1 pr-3">Sequence</th>
                <th className="py-1 pr-3">Status</th>
                <th className="py-1 pr-3 text-right">Sends</th>
                <th className="py-1 pr-3 text-right">Replies</th>
                <th className="py-1 pr-3 text-right">Reply rate</th>
                <th className="py-1 pr-3 text-right">Positive</th>
                <th className="py-1 pr-3 text-right">Bounces</th>
              </tr>
            </thead>
            <tbody>
              {sequences.slice(0, 8).map(s => (
                <tr key={s.id} className="border-t">
                  <td className="py-2 pr-3"><Link to={`/sequences/${s.id}`} className="text-sky-700 hover:underline font-medium">{s.name}</Link></td>
                  <td className="py-2 pr-3"><span className="text-xs px-2 py-0.5 bg-slate-100 rounded">{s.status}</span></td>
                  <td className="py-2 pr-3 text-right font-mono">{s.sends}</td>
                  <td className="py-2 pr-3 text-right font-mono">{s.replies}</td>
                  <td className="py-2 pr-3 text-right font-mono">{pct(s.reply_rate)}</td>
                  <td className="py-2 pr-3 text-right font-mono text-emerald-700">{s.positive_replies}</td>
                  <td className="py-2 pr-3 text-right font-mono text-rose-700">{s.bounces}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <div className="grid md:grid-cols-2 gap-4">
        <section className="rounded border bg-white p-4">
          <h3 className="font-semibold text-slate-800 mb-3">Hot leads — last 7d</h3>
          {!hotLeads?.length ? (
            <p className="text-sm text-slate-500">No hot leads yet. As replies are classified `positive` or `interested`, they'll surface here.</p>
          ) : (
            <ul className="divide-y">
              {hotLeads.map(h => (
                <li key={h.lead_id} className="py-2 flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium">{h.name || h.email || "(unknown)"}</div>
                    <div className="text-xs text-slate-500">{h.title} {h.company ? `· ${h.company}` : ""} · <span style={{ color: SENTIMENT_COLORS[h.latest_sentiment] }}>{h.latest_sentiment}</span> ({h.latest_confidence.toFixed(2)}) · {dateLabel(h.latest_reply_at)}</div>
                  </div>
                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => setDrafting({ id: h.lead_id, email: h.email, name: h.name, company: h.company, title: h.title })}
                      className="text-xs text-violet-700 hover:underline"
                    >
                      Draft follow-up
                    </button>
                    <Link to={`/leads/${h.lead_id}/timeline`} className="text-xs text-sky-600 hover:underline">timeline →</Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded border bg-white p-4">
          <h3 className="font-semibold text-slate-800 mb-3">At-risk enrolments</h3>
          {!atRisk?.length ? (
            <p className="text-sm text-slate-500">No stuck or errored enrolments.</p>
          ) : (
            <ul className="divide-y">
              {atRisk.map(e => (
                <li key={e.enrolment_id} className="py-2">
                  <div className="text-sm font-medium">{e.lead_name || e.lead_email || `enrolment #${e.enrolment_id}`}</div>
                  <div className="text-xs text-slate-500">
                    {e.sequence_name} · <span className="px-1.5 py-0.5 bg-rose-50 text-rose-700 rounded">{e.status}</span>
                    {e.reason && <span className="ml-2 font-mono">{e.reason}</span>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      {drafting && <FollowUpDrafter lead={drafting} onClose={() => setDrafting(null)} />}
    </div>
  );
}
