import { useEffect, useState } from "react";
import { Cell, Pie, PieChart, Tooltip } from "recharts";
import { type AnalyticsMessage, fetchMessages, type AnalyticsStats } from "../api/analytics";
import { DateRangeSelect } from "../components/DateRangeSelect";
import PageHero from "../components/PageHero";

/* ── helpers ──────────────────────────────────────────────────────────────── */
function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

/* ── channel meta ─────────────────────────────────────────────────────────── */
const CHANNEL_META: Record<string, { label: string; color: string }> = {
  email:            { label: "Email",      color: "bg-blue-50 text-blue-700 border-blue-200" },
  linkedin_dm:      { label: "LinkedIn",   color: "bg-sky-50 text-sky-700 border-sky-200" },
  linkedin_connect: { label: "LinkedIn",   color: "bg-sky-50 text-sky-700 border-sky-200" },
  linkedin_like:    { label: "LinkedIn",   color: "bg-sky-50 text-sky-700 border-sky-200" },
  linkedin:         { label: "LinkedIn",   color: "bg-sky-50 text-sky-700 border-sky-200" },
  whatsapp:         { label: "WhatsApp",   color: "bg-green-50 text-green-700 border-green-200" },
};

function channelMeta(ch: string) {
  return CHANNEL_META[ch] ?? { label: ch, color: "bg-slate-50 text-slate-600 border-slate-200" };
}

/* ── sentiment ───────────────────────────────────────────────────────────── */
const SENTIMENT: Record<string, string> = {
  positive: "bg-emerald-50 text-emerald-700 border-emerald-200",
  neutral:  "bg-slate-100 text-slate-600 border-slate-200",
  negative: "bg-red-50 text-red-600 border-red-200",
};

function SentimentBadge({ label }: { label: string | null }) {
  if (!label) return null;
  const cls = SENTIMENT[label] ?? "bg-slate-100 text-slate-500 border-slate-200";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border ${cls} capitalize`}>
      {label}
    </span>
  );
}

/* ── visualization modal ─────────────────────────────────────────────────── */
const VIZ_PALETTE = [
  "#6366f1", "#a5b4fc",   // email sent / received
  "#0ea5e9", "#7dd3fc",   // linkedin sent / received
  "#10b981", "#6ee7b7",   // whatsapp sent / received
];

function VizModal({ stats, onClose }: { stats: AnalyticsStats; onClose: () => void }) {
  const segments: { name: string; value: number; color: string }[] = [];
  const channels = [
    { key: "email",       label: "Email",    si: 0 },
    { key: "linkedin_dm", label: "LinkedIn", si: 2 },
    { key: "whatsapp",    label: "WhatsApp", si: 4 },
  ];
  for (const { key, label, si } of channels) {
    const s = stats[key];
    if (s?.sent)     segments.push({ name: `${label} Sent`,     value: s.sent,     color: VIZ_PALETTE[si] });
    if (s?.received) segments.push({ name: `${label} Received`, value: s.received, color: VIZ_PALETTE[si + 1] });
  }
  const total = segments.reduce((a, s) => a + s.value, 0);

  const totalSent     = channels.reduce((a, { key }) => a + (stats[key]?.sent     ?? 0), 0);
  const totalReceived = channels.reduce((a, { key }) => a + (stats[key]?.received ?? 0), 0);
  const sentPct     = total ? Math.round((totalSent / total) * 100) : 0;
  const receivedPct = total ? 100 - sentPct : 0;

  const renderLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, percent }: {
    cx: number; cy: number; midAngle: number; innerRadius: number; outerRadius: number; percent: number;
  }) => {
    if (percent < 0.04) return null;
    const RADIAN = Math.PI / 180;
    const r = innerRadius + (outerRadius - innerRadius) * 0.55;
    const x = cx + r * Math.cos(-midAngle * RADIAN);
    const y = cy + r * Math.sin(-midAngle * RADIAN);
    return (
      <text x={x} y={y} fill="white" textAnchor="middle" dominantBaseline="central"
        fontSize={11} fontWeight={600}>
        {`${Math.round(percent * 100)}%`}
      </text>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg mx-4 p-6 flex flex-col gap-5"
        onClick={e => e.stopPropagation()}>

        {/* header */}
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-slate-800">Message Breakdown</h2>
            <p className="text-xs text-slate-400 mt-0.5">{total} total messages across all channels</p>
          </div>
          <button onClick={onClose}
            className="text-slate-400 hover:text-slate-600 text-xl leading-none px-1">✕</button>
        </div>

        {/* summary badges */}
        <div className="flex gap-3">
          <div className="flex-1 rounded-xl bg-indigo-50 border border-indigo-100 px-4 py-3 text-center">
            <div className="text-2xl font-bold text-indigo-600">{sentPct}%</div>
            <div className="text-xs text-indigo-400 mt-0.5">Sent by us</div>
            <div className="text-xs text-slate-400">{totalSent} messages</div>
          </div>
          <div className="flex-1 rounded-xl bg-emerald-50 border border-emerald-100 px-4 py-3 text-center">
            <div className="text-2xl font-bold text-emerald-600">{receivedPct}%</div>
            <div className="text-xs text-emerald-400 mt-0.5">Received</div>
            <div className="text-xs text-slate-400">{totalReceived} messages</div>
          </div>
        </div>

        {/* pie chart */}
        {segments.length > 0 ? (
          <>
            <div className="flex justify-center" style={{ outline: "none" }}>
              <PieChart width={280} height={220} style={{ outline: "none" }}>
                <Pie
                  data={segments}
                  cx={135}
                  cy={105}
                  innerRadius={55}
                  outerRadius={100}
                  paddingAngle={2}
                  dataKey="value"
                  labelLine={false}
                  label={renderLabel}
                  stroke="none"
                  style={{ outline: "none", cursor: "default" }}
                >
                  {segments.map((s, i) => (
                    <Cell key={i} fill={s.color} />
                  ))}
                </Pie>
                <Tooltip
                  formatter={(value: number, name: string) => [
                    `${value} (${total ? Math.round((value / total) * 100) : 0}%)`, name
                  ]}
                  contentStyle={{ borderRadius: 8, fontSize: 12 }}
                />
              </PieChart>
            </div>

            {/* custom legend — fully separate from chart */}
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 pt-2 border-t border-slate-100">
              {segments.map((s) => (
                <div key={s.name} className="flex items-center gap-2 min-w-0">
                  <span className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: s.color }} />
                  <span className="text-xs text-slate-600 truncate">{s.name}</span>
                  <span className="ml-auto text-xs font-semibold text-slate-700 shrink-0">
                    {s.value}
                    <span className="text-slate-400 font-normal ml-1">
                      ({total ? Math.round((s.value / total) * 100) : 0}%)
                    </span>
                  </span>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="text-center text-slate-400 text-sm py-8">No data yet</div>
        )}
      </div>
    </div>
  );
}

/* ── stat card ───────────────────────────────────────────────────────────── */
function StatCard({ ch, sent, received }: { ch: string; sent: number; received: number }) {
  const meta = channelMeta(ch);
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 flex items-center gap-4 shadow-sm">
      <div className="flex-1 min-w-0">
        <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">{meta.label}</div>
        <div className="flex items-center gap-3 mt-1">
          <div>
            <span className="text-lg font-bold text-slate-800">{sent}</span>
            <span className="text-xs text-slate-400 ml-1">sent</span>
          </div>
          <div className="text-slate-200">·</div>
          <div>
            <span className="text-lg font-bold text-slate-800">{received}</span>
            <span className="text-xs text-slate-400 ml-1">received</span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── filter tabs ─────────────────────────────────────────────────────────── */
const CHANNEL_TABS = [
  { value: "all", label: "All channels" },
  { value: "email", label: "Email" },
  { value: "linkedin_dm", label: "LinkedIn" },
  { value: "whatsapp", label: "WhatsApp" },
];

const DIR_TABS = [
  { value: "all", label: "All" },
  { value: "sent", label: "Sent" },
  { value: "received", label: "Received" },
];

/* ── row ─────────────────────────────────────────────────────────────────── */
function MessageRow({ msg, expanded, onToggle }: {
  msg: AnalyticsMessage;
  expanded: boolean;
  onToggle: () => void;
}) {
  const ch = channelMeta(msg.channel);
  const isSent = msg.direction === "sent";
  const contactLabel = msg.contact_name || msg.contact_email || msg.contact_phone || "Unknown";
  const preview = (msg.body || "").replace(/\s+/g, " ").trim().slice(0, 120);

  return (
    <>
      <tr
        className="hover:bg-slate-50 cursor-pointer transition-colors"
        onClick={onToggle}
      >
        {/* time */}
        <td className="px-4 py-3 whitespace-nowrap">
          <div className="text-xs font-medium text-slate-700">{fmtTime(msg.occurred_at)}</div>
          <div className="text-[10px] text-slate-400 mt-0.5">{timeAgo(msg.occurred_at)}</div>
        </td>

        {/* contact */}
        <td className="px-4 py-3 max-w-[160px]">
          <div className="text-sm font-medium text-slate-800 truncate">{contactLabel}</div>
          {msg.contact_email && msg.contact_name && (
            <div className="text-[11px] text-slate-400 truncate">{msg.contact_email}</div>
          )}
        </td>

        {/* channel */}
        <td className="px-4 py-3">
          <span className={`inline-flex items-center px-2 py-1 rounded-full text-[11px] font-medium border ${ch.color}`}>
            {ch.label}
          </span>
        </td>

        {/* direction */}
        <td className="px-4 py-3">
          {isSent ? (
            <span className="inline-flex items-center px-2 py-1 rounded-full text-[11px] font-medium bg-violet-50 text-violet-700 border border-violet-200">
              Sent
            </span>
          ) : (
            <span className="inline-flex items-center px-2 py-1 rounded-full text-[11px] font-medium bg-amber-50 text-amber-700 border border-amber-200">
              Received
            </span>
          )}
        </td>

        {/* preview */}
        <td className="px-4 py-3 max-w-xs">
          {msg.subject && (
            <div className="text-[11px] font-semibold text-slate-500 truncate mb-0.5">{msg.subject}</div>
          )}
          <div className="text-xs text-slate-600 truncate">{preview || <span className="text-slate-400 italic">no content</span>}</div>
        </td>

        {/* sentiment */}
        <td className="px-4 py-3">
          {!isSent && <SentimentBadge label={msg.sentiment_label} />}
        </td>

        {/* expand chevron */}
        <td className="px-4 py-3 text-slate-300">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            className={`w-4 h-4 transition-transform ${expanded ? "rotate-180" : ""}`}>
            <path d="m6 9 6 6 6-6" />
          </svg>
        </td>
      </tr>

      {expanded && (
        <tr className="bg-slate-50">
          <td colSpan={7} className="px-8 py-4">
            <div className="bg-white rounded-lg border border-slate-200 p-4 space-y-2">
              {msg.subject && (
                <div className="text-xs font-semibold text-slate-500">
                  Subject: <span className="text-slate-700">{msg.subject}</span>
                </div>
              )}
              <div className="flex items-center gap-2 flex-wrap">
                <SentimentBadge label={msg.sentiment_label} />
                {msg.sentiment_confidence != null && (
                  <span className="text-xs text-slate-400">
                    {Math.round(msg.sentiment_confidence * 100)}% confidence
                  </span>
                )}
                <span className="text-xs text-slate-400 ml-auto">{fmtTime(msg.occurred_at)}</span>
              </div>
              <pre className="text-xs text-slate-700 whitespace-pre-wrap font-sans leading-relaxed bg-slate-50 rounded p-3 border border-slate-100 max-h-64 overflow-y-auto">
                {msg.body || <span className="text-slate-400 italic">No content</span>}
              </pre>
              <div className="flex gap-4 text-[11px] text-slate-400 pt-1">
                {msg.contact_email && <span>{msg.contact_email}</span>}
                {msg.contact_phone && <span>{msg.contact_phone}</span>}
                {msg.contact_li_url && (
                  <a href={msg.contact_li_url} target="_blank" rel="noreferrer"
                    className="text-sky-500 hover:underline" onClick={e => e.stopPropagation()}>
                    LinkedIn profile
                  </a>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/* ── main page ───────────────────────────────────────────────────────────── */
const PAGE_SIZE = 50;

export default function Analytics() {
  const [channel, setChannel] = useState("all");
  const [direction, setDirection] = useState("all");
  const [days, setDays] = useState(30);
  const [page, setPage] = useState(0);
  const [data, setData] = useState<{ items: AnalyticsMessage[]; total: number; stats: AnalyticsStats } | null>(null);
  const [loading, setLoading] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [showViz, setShowViz] = useState(false);

  useEffect(() => {
    setPage(0);
    setExpandedId(null);
  }, [channel, direction, days]);

  useEffect(() => {
    setLoading(true);
    fetchMessages({ channel, direction, days, limit: PAGE_SIZE, offset: page * PAGE_SIZE })
      .then(setData)
      .finally(() => setLoading(false));
  }, [channel, direction, days, page]);

  const stats = data?.stats ?? {};
  const totalSent = Object.values(stats).reduce((a, s) => a + s.sent, 0);
  const totalReceived = Object.values(stats).reduce((a, s) => a + s.received, 0);
  const totalPages = Math.ceil((data?.total ?? 0) / PAGE_SIZE);

  return (
    <div className="space-y-6">
      {showViz && data && <VizModal stats={data.stats} onClose={() => setShowViz(false)} />}

      {/* header */}
      <PageHero
        eyebrow="Insights"
        title="Data Analytics"
        subtitle="All messages sent and received across every channel"
        actions={
          <>
            <DateRangeSelect value={days} onChange={setDays} />
            <button
              onClick={() => setShowViz(true)}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-indigo-200 bg-indigo-50 text-indigo-700 text-sm font-medium hover:bg-indigo-100 transition-colors shadow-sm"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-4 h-4">
                <path d="M11 11V3a1 1 0 0 1 1-1h0a9 9 0 0 1 9 9v0a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1z" />
                <path d="M11 13v8a9 9 0 0 1-9-9h8a1 1 0 0 1 1 1z" />
              </svg>
              Visualization
            </button>
            <div className="flex items-center gap-2 text-sm text-slate-500 bg-white rounded-lg border border-slate-200 px-3 py-1.5 shadow-sm">
              <span className="font-semibold text-slate-700">{totalSent + totalReceived}</span> total messages
            </div>
          </>
        }
      />

      {/* stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="bg-white rounded-xl border border-slate-200 p-4 flex items-center gap-4 shadow-sm col-span-2 lg:col-span-1">
          <div>
            <div className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Total</div>
            <div className="flex items-center gap-3 mt-1">
              <div><span className="text-lg font-bold text-slate-800">{totalSent}</span><span className="text-xs text-slate-400 ml-1">sent</span></div>
              <div className="text-slate-200">·</div>
              <div><span className="text-lg font-bold text-slate-800">{totalReceived}</span><span className="text-xs text-slate-400 ml-1">received</span></div>
            </div>
          </div>
        </div>
        {["email", "linkedin_dm", "whatsapp"].map(ch => (
          <StatCard
            key={ch}
            ch={ch}
            sent={stats[ch]?.sent ?? 0}
            received={stats[ch]?.received ?? 0}
          />
        ))}
      </div>

      {/* filters */}
      <div className="flex items-center gap-6 flex-wrap">
        <div className="flex gap-1 bg-white border border-slate-200 rounded-lg p-1 shadow-sm">
          {CHANNEL_TABS.map(t => (
            <button
              key={t.value}
              onClick={() => setChannel(t.value)}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                channel === t.value
                  ? "bg-brand-600 text-white shadow-sm"
                  : "text-slate-500 hover:text-slate-700 hover:bg-slate-50"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex gap-1 bg-white border border-slate-200 rounded-lg p-1 shadow-sm">
          {DIR_TABS.map(t => (
            <button
              key={t.value}
              onClick={() => setDirection(t.value)}
              className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                direction === t.value
                  ? "bg-brand-600 text-white shadow-sm"
                  : "text-slate-500 hover:text-slate-700 hover:bg-slate-50"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        {data && (
          <span className="text-sm text-slate-400 ml-auto">
            {data.total} messages
          </span>
        )}
      </div>

      {/* table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16 text-slate-400 text-sm">Loading…</div>
        ) : data?.items.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-slate-400">
            <div className="text-sm">No messages found for this filter</div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Time</th>
                  <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Contact</th>
                  <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Channel</th>
                  <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Direction</th>
                  <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Message</th>
                  <th className="px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Sentiment</th>
                  <th className="px-4 py-3 w-8" />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data?.items.map((msg, i) => {
                  const rowId = `${msg.ref_type}-${msg.ref_id}-${i}`;
                  return (
                    <MessageRow
                      key={rowId}
                      msg={msg}
                      expanded={expandedId === rowId}
                      onToggle={() => setExpandedId(expandedId === rowId ? null : rowId)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-slate-500">
            Page {page + 1} of {totalPages}
          </span>
          <div className="flex gap-2">
            <button
              disabled={page === 0}
              onClick={() => setPage(p => p - 1)}
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-200 bg-white text-slate-600 disabled:opacity-40 hover:bg-slate-50"
            >
              Prev
            </button>
            <button
              disabled={page >= totalPages - 1}
              onClick={() => setPage(p => p + 1)}
              className="px-3 py-1.5 text-sm rounded-lg border border-slate-200 bg-white text-slate-600 disabled:opacity-40 hover:bg-slate-50"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
