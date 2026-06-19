import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import Analytics from "./pages/Analytics";
import Channels from "./pages/Channels";
import Dashboard from "./pages/Dashboard";
import Enrolments from "./pages/Enrolments";
import Leads from "./pages/Leads";
import SequenceEditor from "./pages/SequenceEditor";
import SequenceGenerate from "./pages/SequenceGenerate";
import Sequences from "./pages/Sequences";
import Timeline from "./pages/Timeline";
import Watchdog from "./pages/Watchdog";

/* ── Inline icon set (no extra deps) ─────────────────────────────────────── */
type IconProps = { className?: string };
const I = (path: React.ReactNode) => ({ className = "w-[18px] h-[18px]" }: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" className={className}>{path}</svg>
);
const IconDashboard = I(<><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>);
const IconLeads = I(<><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M22 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></>);
const IconSequences = I(<><path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h10" /><circle cx="19" cy="18" r="2" /></>);
const IconEnrolments = I(<><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></>);
const IconChannels = I(<><path d="M12 22v-5" /><path d="M9 8V2" /><path d="M15 8V2" /><path d="M18 8v0a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2v0" /><rect x="6" y="8" width="12" height="9" rx="2" /></>);
const IconWatchdog = I(<><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" /><path d="m9 12 2 2 4-4" /></>);
const IconAnalytics = I(<><path d="M3 3v18h18" /><path d="m19 9-5 5-4-4-3 3" /></>);

const NAV = [
  { to: "/dashboard", label: "Dashboard", Icon: IconDashboard },
  { to: "/leads", label: "Leads", Icon: IconLeads },
  { to: "/sequences", label: "Sequences", Icon: IconSequences },
  { to: "/enrolments", label: "Enrolments", Icon: IconEnrolments },
  { to: "/channels", label: "Channels", Icon: IconChannels },
  { to: "/analytics", label: "Data Analytics", Icon: IconAnalytics },
  { to: "/watchdog", label: "Watchdog", Icon: IconWatchdog },
];

const TITLES: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/leads": "Leads",
  "/sequences": "Sequences",
  "/enrolments": "Enrolments",
  "/channels": "Channels",
  "/analytics": "Data Analytics",
  "/watchdog": "Watchdog",
};

function Sidebar() {
  const loc = useLocation();
  return (
    <aside className="w-60 flex-shrink-0 h-screen sticky top-0 flex flex-col bg-slate-900 text-slate-300">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-5 h-16 border-b border-white/5">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-400 to-brand-600 grid place-items-center text-white font-bold text-sm shadow-lg shadow-brand-900/40">C</div>
        <div className="leading-tight">
          <div className="text-[13px] font-semibold text-white">Coherent</div>
          <div className="text-[10px] uppercase tracking-wider text-slate-500">Outreach</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        <div className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-600">Workspace</div>
        {NAV.map(({ to, label, Icon }) => {
          const active = loc.pathname.startsWith(to);
          return (
            <Link
              key={to}
              to={to}
              className={`group flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                active
                  ? "bg-brand-600/90 text-white shadow-sm"
                  : "text-slate-400 hover:text-white hover:bg-white/5"
              }`}
            >
              <Icon className={`w-[18px] h-[18px] ${active ? "text-white" : "text-slate-500 group-hover:text-slate-300"}`} />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-white/5">
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
          </span>
          Engine running
        </div>
      </div>
    </aside>
  );
}

function Topbar() {
  const loc = useLocation();
  const seg = "/" + (loc.pathname.split("/")[1] || "dashboard");
  const title = TITLES[seg] || "Coherent Outreach";
  return (
    <header className="sticky top-0 z-10 h-16 bg-white/80 backdrop-blur border-b border-slate-200 flex items-center justify-between px-8">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-slate-400">Coherent Outreach</span>
        <span className="text-slate-300">/</span>
        <span className="font-medium text-slate-800">{title}</span>
      </div>
      <div className="badge-brand">Multi-channel outreach</div>
    </header>
  );
}

export default function App() {
  return (
    <div className="flex bg-slate-50 min-h-screen">
      <Sidebar />
      <div className="flex-1 min-w-0 flex flex-col">
        <Topbar />
        <main className="flex-1 p-8">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/leads" element={<Leads />} />
            <Route path="/leads/:id/timeline" element={<Timeline />} />
            <Route path="/sequences" element={<Sequences />} />
            <Route path="/sequences/generate" element={<SequenceGenerate />} />
            <Route path="/sequences/:id" element={<SequenceEditor />} />
            <Route path="/enrolments" element={<Enrolments />} />
            <Route path="/channels" element={<Channels />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/watchdog" element={<Watchdog />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
