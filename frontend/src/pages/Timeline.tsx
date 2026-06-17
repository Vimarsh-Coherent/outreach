import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
import FollowUpDrafter from "../components/FollowUpDrafter";

interface TimelineItem {
  kind: string;
  at: string;
  sequence_name?: string;
  step_order?: number;
  channel?: string;
  subject?: string;
  body?: string;
  sentiment_label?: string;
  sentiment_confidence?: number;
  sentiment_reasoning?: string;
  external_id?: string;
}

interface LeadTimeline {
  lead_id: number;
  lead: Record<string, any>;
  enrolments: Record<string, any>[];
  items: TimelineItem[];
}

const KIND_STYLE: Record<string, string> = {
  sent: "border-l-sky-400 bg-sky-50",
  delivered: "border-l-emerald-300 bg-emerald-50",
  reply: "border-l-emerald-600 bg-emerald-100",
  auto_reply: "border-l-slate-300 bg-slate-50",
  bounce: "border-l-rose-500 bg-rose-50",
  error: "border-l-rose-300 bg-rose-50",
  skipped: "border-l-slate-300 bg-slate-50",
};

const SENTIMENT_COLOR: Record<string, string> = {
  positive: "text-emerald-700",
  interested: "text-emerald-600",
  neutral: "text-slate-500",
  objection: "text-amber-700",
  negative: "text-rose-700",
  unsubscribe: "text-violet-700",
  auto_reply: "text-slate-500",
};

export default function Timeline() {
  const { id } = useParams();
  const leadId = Number(id);
  const [drafting, setDrafting] = useState(false);

  const { data } = useQuery({
    queryKey: ["timeline", leadId],
    queryFn: async () => (await api.get<LeadTimeline>(`/leads/${leadId}/timeline`)).data,
    enabled: !Number.isNaN(leadId),
  });

  if (!data) return <div className="text-sm text-slate-500">Loading...</div>;
  const l = data.lead;
  return (
    <div className="space-y-6 max-w-4xl">
      <div className="text-sm"><Link to="/dashboard" className="text-sky-600 hover:underline">← Dashboard</Link></div>
      <div className="rounded border bg-white p-4 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">{[l.first_name, l.last_name].filter(Boolean).join(" ") || l.email || "(unknown lead)"}</h2>
          <div className="text-sm text-slate-500 mt-1">
            {l.title ? <span className="mr-2">{l.title}</span> : null}
            {l.company ? <span className="mr-2">@ {l.company}</span> : null}
            {l.email ? <a href={`mailto:${l.email}`} className="text-sky-600 hover:underline">{l.email}</a> : null}
          </div>
        </div>
        <button
          onClick={() => setDrafting(true)}
          disabled={!l.email}
          className="border rounded px-3 py-1.5 text-sm bg-violet-600 text-white hover:bg-violet-700 disabled:bg-slate-300"
        >
          Draft AI follow-up
        </button>
      </div>

      <div className="rounded border bg-white p-4">
        <h3 className="font-semibold mb-2">Enrolments</h3>
        {data.enrolments.length === 0 ? (
          <p className="text-sm text-slate-500">Not in any sequence yet.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {data.enrolments.map((e: any) => (
              <li key={e.id}>
                <Link to={`/sequences/${e.sequence_id}`} className="text-sky-700 hover:underline">{e.sequence_name}</Link>
                {" — "}
                <span className="text-xs px-1.5 py-0.5 rounded bg-slate-100">{e.status}</span>
                {e.stopped_reason && <span className="text-xs text-slate-500 ml-2 font-mono">{e.stopped_reason}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="rounded border bg-white p-4">
        <h3 className="font-semibold mb-3">Conversation ({data.items.length})</h3>
        {data.items.length === 0 ? (
          <p className="text-sm text-slate-500">No activity yet.</p>
        ) : (
          <ol className="space-y-3">
            {data.items.map((item, idx) => (
              <li key={idx} className={`border-l-4 rounded-r p-3 ${KIND_STYLE[item.kind] ?? "border-l-slate-300 bg-slate-50"}`}>
                <div className="flex items-center justify-between mb-1">
                  <div className="text-xs font-semibold uppercase tracking-wide">
                    {item.kind}
                    {item.step_order ? ` · step ${item.step_order}` : ""}
                    {item.channel ? ` · ${item.channel}` : ""}
                  </div>
                  <div className="text-xs text-slate-500">{new Date(item.at).toLocaleString()}</div>
                </div>
                {item.subject && <div className="text-sm font-medium">{item.subject}</div>}
                {item.body && (
                  <pre className="text-xs whitespace-pre-wrap mt-1 text-slate-700">{item.body.slice(0, 600)}</pre>
                )}
                {item.sentiment_label && (
                  <div className="text-xs mt-2">
                    sentiment: <span className={`font-semibold ${SENTIMENT_COLOR[item.sentiment_label] ?? ""}`}>{item.sentiment_label}</span>
                    {item.sentiment_confidence != null && <span className="text-slate-400"> ({item.sentiment_confidence.toFixed(2)})</span>}
                    {item.sentiment_reasoning && <span className="ml-2 text-slate-500 italic">— {item.sentiment_reasoning}</span>}
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
      {drafting && (
        <FollowUpDrafter
          lead={{
            id: leadId,
            email: l.email ?? null,
            name: [l.first_name, l.last_name].filter(Boolean).join(" ") || null,
            company: l.company ?? null,
            title: l.title ?? null,
          }}
          onClose={() => setDrafting(false)}
        />
      )}
    </div>
  );
}
