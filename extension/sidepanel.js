// Coherent Outreach side-panel dashboard.
// Pulls campaigns / activity / health from the backend (extension-token auth)
// and warm-up stats from chrome.storage, refreshing every few seconds.

const $ = (id) => document.getElementById(id);

async function getConfig() {
  const { platformUrl, channelId, token } = await chrome.storage.local.get([
    "platformUrl", "channelId", "token",
  ]);
  return { platformUrl: platformUrl || "http://localhost:8000", channelId, token };
}

async function api(path) {
  const { platformUrl, channelId, token } = await getConfig();
  if (!channelId || !token) throw new Error("not_configured");
  const res = await fetch(`${platformUrl}${path}`, {
    headers: { "X-Extension-Channel-Id": String(channelId), "X-Extension-Token": token },
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

function setConn(state, text) {
  $("conn").innerHTML = `<span class="dot ${state}"></span>${text}`;
}

function fmtAgo(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
}

function renderStatus(st) {
  const hbFresh = st.channel.heartbeat_at &&
    (Date.now() - new Date(st.channel.heartbeat_at).getTime()) < 90_000;
  setConn(hbFresh ? "ok" : "warn",
    hbFresh ? "online" : `last seen ${fmtAgo(st.channel.heartbeat_at)}`);

  $("k-sent").textContent = st.queue.done_today;
  $("k-accepted").textContent = st.watchdog.connection_accepts_today;
  $("k-cap").textContent = `${st.channel.sent_today}/${st.channel.daily_cap}`;

  const alert = $("alert");
  if (st.watchdog.circuit_breaker_open) {
    alert.style.display = "block";
    alert.textContent = "⚠ Watchdog circuit breaker is OPEN — automation paused.";
  } else if (st.watchdog.acceptance_detection_stalled) {
    alert.style.display = "block";
    alert.textContent = "⚠ Acceptance detection stalled — open LinkedIn → My Network so accepts are seen.";
  } else {
    alert.style.display = "none";
  }

  const h = st.watchdog;
  $("health").innerHTML = `
    <div class="row"><span class="muted">Queue</span><span>${st.queue.pending} pending · ${st.queue.claimed} running</span></div>
    <div class="row"><span class="muted">LinkedIn today</span><span>${h.li_successes_today} ok · ${h.li_failures_today} failed</span></div>
    <div class="row"><span class="muted">Awaiting acceptance</span><span>${h.awaiting_acceptance}</span></div>
    <div class="row"><span class="muted">DB</span><span>${h.db_alive ? "✅" : "❌"}</span></div>
    ${h.fragile_intents.length ? `<div class="row"><span class="muted">Fragile selectors</span><span class="err">${h.fragile_intents.join(", ")}</span></div>` : ""}
  `;
}

function renderCampaigns(list) {
  const el = $("campaigns");
  if (!list.length) { el.innerHTML = `<div class="empty">No campaigns yet.</div>`; return; }
  el.innerHTML = list.map((c) => `
    <div class="card">
      <div class="row">
        <strong>${escapeHtml(c.name)}</strong>
        <span class="pill ${c.status}">${c.status}</span>
      </div>
      <div class="stats">
        <div class="stat"><b>${c.enrolled}</b><span>leads</span></div>
        <div class="stat"><b>${c.connects_sent}</b><span>invited</span></div>
        <div class="stat"><b>${c.accepted}</b><span>accepted</span></div>
        <div class="stat"><b>${c.dms_sent}</b><span>DMs</span></div>
      </div>
      <div class="row" style="margin-top:6px"><span class="muted">${c.active} active · ${c.awaiting_acceptance} awaiting · ${c.replied} replied · ${c.done} done</span></div>
    </div>
  `).join("");
}

function renderLeads(list) {
  const el = $("leads");
  if (!list.length) { el.innerHTML = `<div class="empty">No leads yet.</div>`; return; }
  const BADGE = {
    connected: ["Connected", "#0f1f15", "#7fe3a0"],
    pending:   ["Pending",   "#0e1a2e", "#9bc1f0"],
    dm_sent:   ["DM sent",   "#1a1530", "#c5a3f0"],
    not_sent:  ["Not sent",  "#1c1c22", "#9aa6bb"],
  };
  // Summary counts at the top.
  const counts = list.reduce((a, l) => (a[l.connection] = (a[l.connection] || 0) + 1, a), {});
  const summary = ["connected", "pending", "dm_sent", "not_sent"]
    .filter(k => counts[k]).map(k => `${counts[k]} ${BADGE[k][0].toLowerCase()}`).join(" · ");
  const badge = (c) => {
    const [t, bg, fg] = BADGE[c] || BADGE.not_sent;
    return `<span class="pill" style="background:${bg};color:${fg};border-color:${bg}">${t}</span>`;
  };
  el.innerHTML =
    `<div class="muted" style="margin-bottom:6px">${summary}</div>` +
    list.slice(0, 60).map(l => `
      <div class="card" style="padding:7px 10px;margin-bottom:6px">
        <div class="row">
          <span style="flex:1">${escapeHtml(l.name)} <span class="muted" style="font-size:10px">· ${escapeHtml(l.campaign)}</span></span>
          ${badge(l.connection)}
        </div>
      </div>`).join("");
}

function renderActivity(items) {
  const el = $("activity");
  if (!items.length) { el.innerHTML = `<div class="empty">No activity yet.</div>`; return; }
  el.innerHTML = items.slice(0, 25).map((a) => {
    const who = (a.target || "").replace("https://www.linkedin.com/in/", "").replace(/\/$/, "");
    const when = fmtAgo(a.completed_at || a.created_at);
    return `<div class="item">
      <span class="tag ${a.status}">${a.type}</span>
      <span style="flex:1">${escapeHtml(who) || "—"}${a.error ? ` <span class="err">· ${escapeHtml(a.error).slice(0, 40)}</span>` : ""}</span>
      <span class="muted">${when}</span>
    </div>`;
  }).join("");
}

async function renderWarmup() {
  const { warmupEnabled = true, warmup = {} } = await chrome.storage.local.get(["warmupEnabled", "warmup"]);
  const today = new Date().toISOString().slice(0, 10);
  const stats = (warmup.day === today) ? warmup : { likes: 0, follows: 0, feedViews: 0, profileViews: 0 };
  $("warm-state").textContent = warmupEnabled ? "· on" : "· off";
  $("warm-toggle").textContent = warmupEnabled ? "Turn off" : "Turn on";
  $("warm-summary").textContent = warmupEnabled
    ? "browsing feed + following + reacting (conservative)"
    : "paused";
  $("warm-stats").innerHTML = `
    <div class="stat"><b>${stats.feedViews || 0}</b><span>feed</span></div>
    <div class="stat"><b>${stats.likes || 0}</b><span>reactions</span></div>
    <div class="stat"><b>${stats.follows || 0}</b><span>follows</span></div>
    <div class="stat"><b>${stats.profileViews || 0}</b><span>views</span></div>
  `;
}

$("warm-toggle").addEventListener("click", async () => {
  const { warmupEnabled = true } = await chrome.storage.local.get(["warmupEnabled"]);
  await chrome.storage.local.set({ warmupEnabled: !warmupEnabled });
  renderWarmup();
});

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function refresh() {
  try {
    const [status, campaigns, leads, activity] = await Promise.all([
      api("/api/extension/status"),
      api("/api/extension/campaigns"),
      api("/api/extension/leads"),
      api("/api/extension/activity?limit=30"),
    ]);
    renderStatus(status);
    renderCampaigns(campaigns);
    renderLeads(leads);
    renderActivity(activity);
  } catch (e) {
    if (e.message === "not_configured") {
      setConn("bad", "not configured");
      $("campaigns").innerHTML = `<div class="empty">Open the popup and save your Channel ID + token first.</div>`;
    } else {
      setConn("bad", `backend error (${e.message})`);
    }
  }
  renderWarmup();
}

refresh();
setInterval(refresh, 8000);
