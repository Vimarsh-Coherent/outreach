import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";

import Channels from "./pages/Channels";
import Dashboard from "./pages/Dashboard";
import Enrolments from "./pages/Enrolments";
import Leads from "./pages/Leads";
import SequenceEditor from "./pages/SequenceEditor";
import Sequences from "./pages/Sequences";
import Timeline from "./pages/Timeline";
import Watchdog from "./pages/Watchdog";

const NAV = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/leads", label: "Leads" },
  { to: "/sequences", label: "Sequences" },
  { to: "/enrolments", label: "Enrolments" },
  { to: "/channels", label: "Channels" },
  { to: "/watchdog", label: "Watchdog" },
];

function Sidebar() {
  const loc = useLocation();
  return (
    <aside className="w-56 bg-slate-900 text-slate-100 flex-shrink-0 h-screen sticky top-0 p-4">
      <h1 className="text-lg font-semibold mb-6">Coherent Outreach</h1>
      <nav className="flex flex-col gap-1">
        {NAV.map(n => {
          const active = loc.pathname.startsWith(n.to);
          return (
            <Link
              key={n.to}
              to={n.to}
              className={`px-3 py-2 rounded text-sm ${
                active ? "bg-slate-700 text-white" : "hover:bg-slate-800"
              }`}
            >
              {n.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}

export default function App() {
  return (
    <div className="flex">
      <Sidebar />
      <main className="flex-1 p-8 min-h-screen">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/leads" element={<Leads />} />
          <Route path="/leads/:id/timeline" element={<Timeline />} />
          <Route path="/sequences" element={<Sequences />} />
          <Route path="/sequences/:id" element={<SequenceEditor />} />
          <Route path="/enrolments" element={<Enrolments />} />
          <Route path="/channels" element={<Channels />} />
          <Route path="/watchdog" element={<Watchdog />} />
        </Routes>
      </main>
    </div>
  );
}
