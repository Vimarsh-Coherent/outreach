import { Link } from "react-router-dom";

/* ── tiny inline icon helper ─────────────────────────────────────────────── */
const Icon = (p: React.ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" className="w-6 h-6">{p}</svg>
);
const IcMail = Icon(<><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></>);
const IcChat = Icon(<><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></>);
const IcBrain = Icon(<><path d="M12 5a3 3 0 0 0-6 0 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 0" /><path d="M12 5a3 3 0 0 1 6 0 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 0" /></>);
const IcFlow = Icon(<><rect x="3" y="3" width="6" height="6" rx="1.5" /><rect x="15" y="15" width="6" height="6" rx="1.5" /><path d="M9 6h6a3 3 0 0 1 3 3v6" /></>);
const IcPulse = Icon(<><path d="M3 12h4l3 8 4-16 3 8h4" /></>);
const IcPlug = Icon(<><path d="M9 2v6M15 2v6M7 8h10v3a5 5 0 0 1-10 0z" /><path d="M12 16v6" /></>);
const IcTarget = Icon(<><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></>);
const IcShield = Icon(<><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" /><path d="m9 12 2 2 4-4" /></>);

const FEATURES = [
  { icon: IcMail, title: "Multi-channel sending", body: "Email, LinkedIn (connect, DM, visit + like), and WhatsApp — orchestrated from one sequence with caps, send windows and jitter for safe delivery." },
  { icon: IcBrain, title: "AI sequence generation", body: "Describe your campaign (and drop in your pitch docs) and AI drafts a grounded, on-brand cadence. Per-lead personalization at send time." },
  { icon: IcFlow, title: "Visual flow builder", body: "An n8n-style drag-and-drop canvas with conditional branching — “if replied → path A, if opened → path B, else → follow-up.”" },
  { icon: IcPulse, title: "Reply intelligence", body: "Detects replies across every channel and classifies sentiment into 7 buckets — positive, objection, unsubscribe and more — auto-stopping on reply." },
  { icon: IcPlug, title: "CRM & integrations", body: "Native HubSpot two-way sync, signed webhooks to any CRM via Zapier/Make, and a public API to push leads in and pull replies out." },
  { icon: IcTarget, title: "Deliverability tooling", body: "Open & click tracking, A/B subject/body testing with winner stats, and a spam-score linter to keep you landing in the inbox." },
];

const STEPS = [
  { n: "01", title: "Import your leads", body: "Upload a CSV, push via the API, or pull contacts straight from HubSpot." },
  { n: "02", title: "Build a sequence", body: "Generate it with AI or drag steps on the canvas — add channels, delays and branches." },
  { n: "03", title: "Launch & watch", body: "Coherent sends, detects replies, scores sentiment, and logs everything back to your CRM." },
];

export default function Landing() {
  return (
    <div className="min-h-screen bg-white text-slate-700">
      {/* ── Nav ───────────────────────────────────────────────────────────── */}
      <header className="absolute inset-x-0 top-0 z-20">
        <nav className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">
          <div className="flex items-center gap-2.5">
            <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-brand-400 to-fuchsia-500 font-bold text-white shadow-lg">C</div>
            <span className="text-lg font-semibold text-white">Coherent <span className="font-normal text-white/60">Outreach</span></span>
          </div>
          <div className="hidden items-center gap-8 text-sm font-medium text-white/70 md:flex">
            <a href="#features" className="hover:text-white">Features</a>
            <a href="#how" className="hover:text-white">How it works</a>
            <a href="#channels" className="hover:text-white">Channels</a>
          </div>
          <Link to="/dashboard" className="btn-gradient btn-sm rounded-lg">Open dashboard →</Link>
        </nav>
      </header>

      {/* ── Hero ──────────────────────────────────────────────────────────── */}
      <section className="hero-bg relative overflow-hidden">
        <div className="blob absolute -left-32 top-10 h-72 w-72 rounded-full bg-brand-500/30" />
        <div className="blob absolute right-0 top-40 h-72 w-72 rounded-full bg-fuchsia-500/20" />
        <div className="relative mx-auto max-w-7xl px-6 pb-24 pt-36 text-center">
          <div className="animate-rise mx-auto inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-4 py-1.5 text-xs font-medium text-white/80 backdrop-blur">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> AI-powered · Email · LinkedIn · WhatsApp
          </div>
          <h1 className="animate-rise mx-auto mt-6 max-w-4xl text-5xl font-bold leading-[1.05] tracking-tight text-white sm:text-6xl md:text-7xl">
            Cold outreach that <span className="gradient-text">runs itself</span>.
          </h1>
          <p className="animate-rise mx-auto mt-6 max-w-2xl text-lg text-white/70">
            One platform to generate sequences with AI, send across every channel, branch on what leads do,
            read the sentiment of every reply, and sync it all to your CRM.
          </p>
          <div className="animate-rise mt-9 flex flex-wrap items-center justify-center gap-3">
            <Link to="/dashboard" className="btn-gradient btn-lg">Open dashboard →</Link>
            <a href="#how" className="btn btn-lg rounded-xl border border-white/20 bg-white/5 text-white backdrop-blur hover:bg-white/10">See how it works</a>
          </div>

          {/* Product mockup */}
          <div className="animate-rise mx-auto mt-16 max-w-5xl">
            <div className="glass overflow-hidden p-2 sm:p-3">
              <div className="rounded-xl bg-slate-950/60 ring-1 ring-white/10">
                <div className="flex items-center gap-1.5 border-b border-white/10 px-4 py-3">
                  <span className="h-3 w-3 rounded-full bg-rose-400/80" />
                  <span className="h-3 w-3 rounded-full bg-amber-400/80" />
                  <span className="h-3 w-3 rounded-full bg-emerald-400/80" />
                  <span className="ml-3 text-xs text-white/40">app.coherent-outreach.local/dashboard</span>
                </div>
                <div className="grid grid-cols-12 gap-3 p-4 text-left">
                  {/* mini sidebar */}
                  <div className="col-span-3 hidden space-y-2 sm:block">
                    {["Dashboard", "Leads", "Sequences", "Channels", "Integrations"].map((x, i) => (
                      <div key={x} className={`rounded-lg px-3 py-2 text-xs ${i === 0 ? "bg-gradient-to-r from-brand-500/40 to-fuchsia-500/30 text-white" : "text-white/50"}`}>{x}</div>
                    ))}
                  </div>
                  {/* mini content */}
                  <div className="col-span-12 space-y-3 sm:col-span-9">
                    <div className="grid grid-cols-4 gap-3">
                      {[["Enrolled", "1,284"], ["Replied", "11.4%"], ["Positive", "38%"], ["Meetings", "27"]].map(([k, v]) => (
                        <div key={k} className="rounded-lg border border-white/10 bg-white/5 p-3">
                          <div className="text-[10px] uppercase tracking-wide text-white/40">{k}</div>
                          <div className="mt-1 text-lg font-semibold text-white">{v}</div>
                        </div>
                      ))}
                    </div>
                    <div className="rounded-lg border border-white/10 bg-white/5 p-4">
                      <div className="mb-3 text-xs text-white/50">Reply sentiment — last 30 days</div>
                      <div className="flex h-24 items-end gap-2">
                        {[40, 70, 55, 90, 65, 80, 50, 95, 60, 75].map((h, i) => (
                          <div key={i} className="flex-1 rounded-t bg-gradient-to-t from-brand-500 to-fuchsia-400" style={{ height: `${h}%` }} />
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Channels strip ────────────────────────────────────────────────── */}
      <section id="channels" className="border-b border-slate-100 bg-white">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-center gap-x-12 gap-y-4 px-6 py-10 text-slate-400">
          <span className="text-xs font-semibold uppercase tracking-widest">Sends across</span>
          <span className="flex items-center gap-2 text-slate-700"><span className="text-sky-500">{IcMail}</span> Email</span>
          <span className="flex items-center gap-2 text-slate-700"><span className="text-blue-600 font-bold">in</span> LinkedIn</span>
          <span className="flex items-center gap-2 text-slate-700"><span className="text-green-500">{IcChat}</span> WhatsApp</span>
          <span className="flex items-center gap-2 text-slate-700"><span className="text-orange-500">⬢</span> HubSpot sync</span>
        </div>
      </section>

      {/* ── Features ──────────────────────────────────────────────────────── */}
      <section id="features" className="mx-auto max-w-7xl px-6 py-24">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-sm font-semibold uppercase tracking-widest gradient-text">Everything in one place</p>
          <h2 className="mt-3 text-4xl font-bold tracking-tight text-slate-900">A complete outreach engine</h2>
          <p className="mt-4 text-lg text-slate-500">From the first AI-drafted email to the reply landing in your CRM — every step is built in.</p>
        </div>
        <div className="mt-14 grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map(f => (
            <div key={f.title} className="glass-light p-6 transition-transform hover:-translate-y-1">
              <div className="feature-icon">{f.icon}</div>
              <h3 className="mt-4 text-lg font-semibold text-slate-900">{f.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-500">{f.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── How it works ──────────────────────────────────────────────────── */}
      <section id="how" className="bg-slate-50 py-24">
        <div className="mx-auto max-w-7xl px-6">
          <div className="mx-auto max-w-2xl text-center">
            <p className="text-sm font-semibold uppercase tracking-widest gradient-text">Three steps</p>
            <h2 className="mt-3 text-4xl font-bold tracking-tight text-slate-900">Live in minutes</h2>
          </div>
          <div className="mt-14 grid gap-6 md:grid-cols-3">
            {STEPS.map(s => (
              <div key={s.n} className="relative rounded-2xl border border-slate-200 bg-white p-7 shadow-card">
                <div className="text-5xl font-bold gradient-text">{s.n}</div>
                <h3 className="mt-3 text-lg font-semibold text-slate-900">{s.title}</h3>
                <p className="mt-2 text-sm text-slate-500">{s.body}</p>
              </div>
            ))}
          </div>
          <div className="mt-12 flex items-center justify-center gap-3 text-sm text-slate-500">
            <span className="flex items-center gap-1.5"><span className="text-brand-600">{IcShield}</span> Self-healing watchdog keeps every channel monitored 24/7.</span>
          </div>
        </div>
      </section>

      {/* ── CTA ───────────────────────────────────────────────────────────── */}
      <section className="px-6 py-20">
        <div className="hero-bg relative mx-auto max-w-6xl overflow-hidden rounded-3xl px-8 py-16 text-center">
          <div className="blob absolute -right-20 -top-10 h-64 w-64 rounded-full bg-fuchsia-500/30" />
          <h2 className="relative text-4xl font-bold tracking-tight text-white sm:text-5xl">Ready to automate your outreach?</h2>
          <p className="relative mx-auto mt-4 max-w-xl text-lg text-white/70">Open the dashboard and launch your first AI-built, multi-channel sequence today.</p>
          <div className="relative mt-8 flex justify-center">
            <Link to="/dashboard" className="btn-gradient btn-lg">Open dashboard →</Link>
          </div>
        </div>
      </section>

      {/* ── Footer ────────────────────────────────────────────────────────── */}
      <footer className="border-t border-slate-100 bg-white">
        <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-4 px-6 py-10 text-sm text-slate-400 sm:flex-row">
          <div className="flex items-center gap-2">
            <div className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-brand-400 to-fuchsia-500 text-xs font-bold text-white">C</div>
            <span className="font-medium text-slate-600">Coherent Outreach</span>
          </div>
          <div className="flex gap-6">
            <a href="#features" className="hover:text-slate-700">Features</a>
            <a href="#how" className="hover:text-slate-700">How it works</a>
            <Link to="/dashboard" className="hover:text-slate-700">Dashboard</Link>
          </div>
          <span>© 2026 Coherent Outreach</span>
        </div>
      </footer>
    </div>
  );
}
