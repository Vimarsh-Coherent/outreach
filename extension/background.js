// Coherent Outreach extension service worker.
// Polls the backend every 30s for new LinkedIn commands and dispatches them
// to the content script running on linkedin.com.
//
// Auth: pastes platformUrl + channelId + token into the popup. Every backend
// request sends X-Extension-Channel-Id and X-Extension-Token headers. The
// backend HMAC-verifies the token against a hash stored on the channel.
//
// v2: robust tab targeting:
//   1. Prefer the LinkedIn tab whose URL matches command's target_li_url
//   2. If chrome.tabs.sendMessage fails ("Receiving end does not exist"),
//      auto-inject content_linkedin.js via chrome.scripting.executeScript
//      and retry once.
//   3. If all candidate tabs are unresponsive, report failure to backend
//      so the command doesn't stay silently stuck in 'claimed' state.

// Build marker — printed every time the service worker boots. If you do NOT see
// this exact line in the service-worker console, Chrome is running a stale,
// cached worker and the fixes below are NOT active.
const COHERENT_BUILD = "2026-06-12-sendbtn-v29";
console.log(`[coherent] background.js loaded — build ${COHERENT_BUILD}`);

const POLL_ALARM = "coherent-poll";       // responsive command drain (30s)
const PATROL_ALARM = "coherent-patrol";   // jittered human-mimicry patrol (15-20 min)
const ACTIVE_HOURS = { start: 8, end: 22 }; // local-time window patrols run in

async function getConfig() {
  const { platformUrl, channelId, token } = await chrome.storage.local.get([
    "platformUrl", "channelId", "token",
  ]);
  return {
    platformUrl: platformUrl || "http://localhost:8000",
    channelId: channelId || "",
    token: token || "",
  };
}

async function api(path, init = {}) {
  const { platformUrl, channelId, token } = await getConfig();
  if (!token || !channelId) return null;
  const res = await fetch(`${platformUrl}${path}`, {
    ...init,
    headers: {
      "X-Extension-Channel-Id": String(channelId),
      "X-Extension-Token": token,
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
  if (res.status === 204) return null;
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function heartbeat() {
  try { await api("/api/extension/heartbeat", { method: "GET" }); }
  catch (e) { console.warn("[coherent] heartbeat failed", e); }
}

async function getLinkedInTabs() {
  return chrome.tabs.query({ url: "https://www.linkedin.com/*" });
}

function normaliseUrl(u) {
  if (!u) return "";
  return u.replace(/\/$/, "").split("?")[0].split("#")[0];
}

// Try to send a message to a specific tab. If the content script isn't there,
// inject it manually via chrome.scripting and retry once.
async function trySendToTab(tab, message) {
  try {
    const r = await chrome.tabs.sendMessage(tab.id, message);
    return { ok: true, response: r };
  } catch (e) {
    const msg = e?.message || String(e);
    console.log(`[coherent] tab ${tab.id} (${tab.url?.slice(0, 60)}) sendMessage failed: ${msg} — attempting content-script injection`);
    // Auto-inject and retry
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content_linkedin.js"],
      });
      // Brief delay so the listener registers
      await new Promise(r => setTimeout(r, 600));
      const r = await chrome.tabs.sendMessage(tab.id, message);
      console.log(`[coherent] tab ${tab.id} responded after injection`);
      return { ok: true, response: r, injected: true };
    } catch (e2) {
      console.warn(`[coherent] tab ${tab.id} still unresponsive after injection: ${e2?.message || e2}`);
      return { ok: false, error: e2?.message || String(e2) };
    }
  }
}

// Resolve once a tab finishes loading (status === "complete"), or on timeout.
function waitForTabComplete(tabId, timeoutMs = 15000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      try { chrome.tabs.onUpdated.removeListener(listener); } catch {}
      clearTimeout(timer);
      resolve(ok);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    const listener = (id, info) => { if (id === tabId && info.status === "complete") finish(true); };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId, (t) => {
      if (chrome.runtime.lastError) return finish(false);
      if (t && t.status === "complete") finish(true);
    });
  });
}

// Drive a tab to the target profile and WAIT for it to finish loading, so the
// content script is already on the right page when it receives the command.
// Without this the content script would navigate itself and bail mid-command,
// leaving the li_command stuck in 'claimed' forever.
async function ensureTabOnTarget(tab, targetUrl) {
  const target = normaliseUrl(targetUrl);
  const current = normaliseUrl(tab.url);
  if (current === target || current.startsWith(target)) return tab;
  console.log(`[coherent] navigating tab ${tab.id} → ${targetUrl} (was ${tab.url?.slice(0, 60)})`);
  await chrome.tabs.update(tab.id, { url: targetUrl });
  await waitForTabComplete(tab.id);
  // Let LinkedIn's SPA finish hydrating the profile action buttons.
  await new Promise((r) => setTimeout(r, 1500));
  try { return await chrome.tabs.get(tab.id); } catch { return tab; }
}

// Send message to the best-matching LinkedIn tab. Returns { ok, response, tabId, ... }.
async function sendToBestLinkedInTab(message, targetUrl) {
  const tabs = await getLinkedInTabs();
  if (tabs.length === 0) return { ok: false, reason: "no_linkedin_tab_open" };

  // Order tabs by preference: exact target match > prefix match > any LinkedIn
  const target = normaliseUrl(targetUrl);
  const ordered = [...tabs].sort((a, b) => {
    const au = normaliseUrl(a.url);
    const bu = normaliseUrl(b.url);
    const aScore = au === target ? 100 : au.startsWith(target) ? 50 : target && au.includes(target.split("/").pop() || "") ? 10 : 0;
    const bScore = bu === target ? 100 : bu.startsWith(target) ? 50 : target && bu.includes(target.split("/").pop() || "") ? 10 : 0;
    return bScore - aScore;
  });

  console.log(`[coherent] dispatching to ${ordered.length} candidate LinkedIn tab(s), preferred: tab ${ordered[0].id} (${ordered[0].url?.slice(0, 80)})`);

  // Navigate the preferred tab onto the target profile, THEN execute there.
  let primary = ordered[0];
  try { primary = await ensureTabOnTarget(primary, targetUrl); }
  catch (e) { console.warn(`[coherent] navigation failed: ${e?.message || e}`); }

  let r = await trySendToTab(primary, message);
  if (r.ok) return { ok: true, response: r.response, tabId: primary.id, injected: r.injected || false };

  const isCommand = message && message.kind === "execute-command";
  if (isCommand) {
    // Commands must ONLY run on the tab we navigated to the target. The old
    // "try the remaining tabs as-is" fallback dispatched commands to whatever
    // page a leftover tab was sitting on — observed live executing against the
    // wrong person's profile (the content-script page guards reject it now,
    // but it can never succeed either). Instead: give the primary tab more
    // time to finish hydrating and retry it once, then report failure so the
    // backend reclaims the command.
    await new Promise((res) => setTimeout(res, 4000));
    r = await trySendToTab(primary, message);
    if (r.ok) return { ok: true, response: r.response, tabId: primary.id, injected: r.injected || false };
    return { ok: false, reason: "all_tabs_unresponsive" };
  }

  // Non-command messages (patrol session triggers) may use any responsive tab.
  for (const tab of ordered.slice(1)) {
    const rr = await trySendToTab(tab, message);
    if (rr.ok) return { ok: true, response: rr.response, tabId: tab.id, injected: rr.injected || false };
  }
  return { ok: false, reason: "all_tabs_unresponsive" };
}

// Only one command may execute at a time. Each command drives the SHARED
// LinkedIn tab to its target profile; if a second poll claims another command
// and navigates the tab while the first is still running, it yanks the page out
// from under the first and both time out.
//
// The lock is PERSISTED in chrome.storage (not an in-memory flag) so it survives
// a service-worker restart mid-command — otherwise a SW kill would reset the
// flag and let a second command overlap. A stale lock (older than LOCK_STALE_MS,
// i.e. a crashed command) is reclaimed so dispatch can never deadlock forever.
const LOCK_STALE_MS = 4 * 60_000;

async function acquireDispatchLock(holder) {
  const { dispatchLock } = await chrome.storage.local.get(["dispatchLock"]);
  if (dispatchLock && dispatchLock.at && (Date.now() - dispatchLock.at) < LOCK_STALE_MS) {
    return false;  // held and fresh
  }
  await chrome.storage.local.set({ dispatchLock: { holder, at: Date.now() } });
  return true;
}

async function releaseDispatchLock() {
  await chrome.storage.local.set({ dispatchLock: null, cmdBusyUntil: 0 });
}

// MV3 kills a service worker after ~30s WITHOUT extension-API activity — and a
// long `await chrome.tabs.sendMessage(...)` (our minutes-long DOM automation)
// counts as idle. Observed live: commands claimed, then total silence (no
// attempt notes, no /complete), then the backend's 5-min reclaim — the SW was
// being killed mid-dispatch. Touching an extension API resets the idle timer.
function startDispatchKeepalive() {
  return setInterval(() => {
    try { chrome.runtime.getPlatformInfo(() => {}); } catch { /* sw dying */ }
  }, 20_000);
}

// Breadcrumbs posted to the backend watchdog log so a SW death mid-dispatch is
// VISIBLE (the SW console dies with the SW; these survive on the server).
async function dispatchNote(message, extra = {}) {
  try {
    await api("/api/extension/watchdog-note", {
      method: "POST",
      body: JSON.stringify({ tier: "extension_failure", status: "issue", message, ...extra }),
    });
  } catch { /* never block dispatch on telemetry */ }
}

async function pollOnce() {
  await heartbeat();
  // Defense in depth against off-hours sends: the backend schedules into the
  // sequence send window, but watchdog retries used to re-pend commands at any
  // hour (observed: a DM executed at 02:55). Never EXECUTE commands outside
  // active hours — heartbeat above still runs so the backend knows we're alive.
  if (!inActiveHours()) {
    console.log("[coherent] poll skipped — outside active hours");
    return;
  }
  if (!(await acquireDispatchLock("poll"))) {
    console.log("[coherent] poll skipped — dispatch lock held");
    return;
  }
  const keepalive = startDispatchKeepalive();
  let cmd = null;
  try {
    cmd = await api("/api/extension/next-command");
    if (!cmd) return;  // released in finally
    // Tell the content-script warm-up/patrol engine to stand down while we drive the tab.
    chrome.storage.local.set({ cmdBusyUntil: Date.now() + 180_000 });
    console.log(`[coherent] poll got command ${cmd.id} (${cmd.command_type}) → ${cmd.target_li_url}`);
    dispatchNote(`cmd#${cmd.id} dispatch: claimed by SW build ${COHERENT_BUILD}, navigating`, { cmd_id: cmd.id });
    let result = await sendToBestLinkedInTab({ kind: "execute-command", cmd }, cmd.target_li_url);
    // Safety net: if the content script reported it had to navigate, wait for the
    // page and resend ONCE so the command is never abandoned in 'claimed'. Only
    // retries on the navigating signal — a real execution never returns it, so
    // this can't double-send.
    if (result.ok && result.response && result.response.navigating) {
      console.log(`[coherent] cmd ${cmd.id} reported navigation — waiting then retrying once`);
      await new Promise((r) => setTimeout(r, 3500));
      result = await sendToBestLinkedInTab({ kind: "execute-command", cmd }, cmd.target_li_url);
    }
    dispatchNote(
      `cmd#${cmd.id} dispatch finished: ok=${result.ok}${result.reason ? ` reason=${result.reason}` : ""}`,
      { cmd_id: cmd.id },
    );
    if (!result.ok) {
      console.warn(`[coherent] failed to dispatch command ${cmd.id}: ${result.reason}`);
      await reportFailure(cmd.id, result.reason || "dispatch_failed");
    }
  } catch (e) {
    console.error("[coherent] poll failed", e);
    // A dispatch exception used to be swallowed here — the claimed command
    // stayed silently stuck until the 5-min reclaim (observed live as cmd#82's
    // missing completion). Report it so the backend finalises immediately.
    if (cmd) {
      dispatchNote(`cmd#${cmd.id} dispatch crashed: ${String(e?.message || e).slice(0, 150)}`, { cmd_id: cmd.id });
      try { await reportFailure(cmd.id, `dispatch_exception: ${String(e?.message || e).slice(0, 150)}`); }
      catch { /* backend unreachable — reclaim will handle it */ }
    }
  } finally {
    clearInterval(keepalive);
    await releaseDispatchLock();
  }
}

// ── Human-mimicry patrol (jittered, alarm-driven, MV3-safe) ───────────────
function inActiveHours() {
  const h = new Date().getHours();
  return h >= ACTIVE_HOURS.start && h < ACTIVE_HOURS.end;
}

function schedulePatrol() {
  // One-off alarm, 15–20 min jittered (never a fixed period). Re-armed after
  // EVERY patrol (incl. failures) so the loop survives SW restarts and errors.
  const minutes = 15 + Math.random() * 5;
  chrome.alarms.create(PATROL_ALARM, { delayInMinutes: minutes });
  console.log(`[coherent] next patrol scheduled in ${minutes.toFixed(1)} min`);
}

async function ensureLinkedInTab() {
  const tabs = await getLinkedInTabs();
  let tab = tabs[0];
  if (!tab) {
    console.log("[coherent] patrol opening a background LinkedIn tab");
    tab = await chrome.tabs.create({ url: "https://www.linkedin.com/feed/", active: false });
  }
  const ok = await waitForTabComplete(tab.id, 20_000);
  if (!ok) console.warn("[coherent] patrol tab did not finish loading in time");
  try { return await chrome.tabs.get(tab.id); } catch { return tab; }
}

// The content script's scans are page-gated: scanInbox only works on
// /messaging/, the connections scan on /mynetwork/.../connections/. A patrol
// that only ever sits on /feed/ therefore NEVER detects replies or accepts in
// the autonomous (dedicated-profile) setup, where no human browses LinkedIn.
// Rotate the patrol destination so every scan page gets visited regularly —
// feed appears twice so warm-up still gets most of the time share.
const PATROL_PAGES = [
  "https://www.linkedin.com/feed/",
  "https://www.linkedin.com/messaging/",
  "https://www.linkedin.com/mynetwork/invite-connect/connections/",
  "https://www.linkedin.com/feed/",
  "https://www.linkedin.com/notifications/",
];

async function nextPatrolPage() {
  const { patrolPageIdx = 0 } = await chrome.storage.local.get(["patrolPageIdx"]);
  await chrome.storage.local.set({ patrolPageIdx: (patrolPageIdx + 1) % PATROL_PAGES.length });
  return PATROL_PAGES[patrolPageIdx % PATROL_PAGES.length];
}

async function dispatchLockHeld() {
  const { dispatchLock } = await chrome.storage.local.get(["dispatchLock"]);
  return !!(dispatchLock && dispatchLock.at && (Date.now() - dispatchLock.at) < LOCK_STALE_MS);
}

async function runPatrol() {
  try {
    if (!inActiveHours()) { console.log("[coherent] patrol skipped — outside active hours"); return; }
    if (Math.random() < 0.15) { console.log("[coherent] patrol skipped — random ~15% skip"); return; }
    let tab = await ensureLinkedInTab();
    if (!tab) { console.log("[coherent] patrol — no LinkedIn tab available"); return; }
    // 1) Drain one pending command (reuses the normal dispatch + persistent lock).
    await pollOnce();
    // If another poll is mid-command, do NOT navigate the shared tab out from
    // under it — skip this patrol round entirely (the alarm re-arms below).
    if (await dispatchLockHeld()) {
      console.log("[coherent] patrol skipped navigation — a command is executing");
      return;
    }
    // 2) Rotate to the next scan page so replies + accepts get detected even
    //    with nobody browsing manually.
    try {
      const dest = await nextPatrolPage();
      tab = await ensureTabOnTarget(tab, dest);
      console.log(`[coherent] patrol on ${dest}`);
    } catch (e) {
      console.warn(`[coherent] patrol navigation failed: ${e?.message || e}`);
    }
    // 3) Hand the minutes-long human-mimicry session to the content script.
    const r = await trySendToTab(tab, { kind: "RUN_PATROL_SESSION" });
    console.log(`[coherent] patrol session dispatched (ok=${r.ok})`);
  } catch (e) {
    console.warn("[coherent] patrol error", e);
  } finally {
    schedulePatrol();   // always re-arm
  }
}

async function reportSuccess(cmdId, providerMessageId) {
  await api(`/api/extension/commands/${cmdId}/complete`, {
    method: "POST",
    body: JSON.stringify({ status: "done", provider_message_id: providerMessageId }),
  });
}

async function reportFailure(cmdId, error) {
  await api(`/api/extension/commands/${cmdId}/complete`, {
    method: "POST",
    body: JSON.stringify({ status: "failed", error }),
  });
}

async function requestHeal(payload) {
  try {
    return await api("/api/extension/heal-selector", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  } catch (e) {
    console.warn("[coherent] heal-selector request failed", e);
    return null;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      if (msg.kind === "command-result") {
        console.log(`[coherent] command ${msg.cmdId} reported back: status=${msg.status}`);
        if (msg.status === "done") await reportSuccess(msg.cmdId, msg.providerMessageId);
        else await reportFailure(msg.cmdId, msg.error || "unknown");
        sendResponse({ ok: true });
      } else if (msg.kind === "inbound-replies") {
        if (Array.isArray(msg.replies) && msg.replies.length > 0) {
          await api("/api/extension/replies", {
            method: "POST",
            body: JSON.stringify(msg.replies),
          });
        }
        sendResponse({ ok: true });
      } else if (msg.kind === "connections-seen") {
        // Observed 1st-degree connections → backend releases gated DM steps for
        // any matching lead that was awaiting acceptance.
        if (Array.isArray(msg.connections) && msg.connections.length > 0) {
          await api("/api/extension/connections-seen", {
            method: "POST",
            body: JSON.stringify(msg.connections),
          });
        }
        sendResponse({ ok: true });
      } else if (msg.kind === "heal-request") {
        const result = await requestHeal(msg.payload || {});
        if (result && Array.isArray(result.selectors) && result.selectors.length > 0) {
          console.log("[coherent] heal:", msg.payload.intent, "→", result.selectors[0]);
          sendResponse({ ok: true, selectors: result.selectors, cost_usd: result.cost_usd });
        } else {
          sendResponse({ ok: false, selectors: [] });
        }
      } else if (msg.kind === "watchdog-note") {
        // Realtime self-heal telemetry from the content script. Forwarded to
        // the backend watchdog so the dashboard reflects recovery as it
        // happens. Fire-and-forget — never block the content script.
        try {
          await api("/api/extension/watchdog-note", {
            method: "POST",
            body: JSON.stringify(msg),
          });
        } catch (e) { console.warn("[coherent] watchdog-note failed", e); }
        sendResponse({ ok: true });
      } else {
        sendResponse({ ok: false, error: "unknown_kind" });
      }
    } catch (e) {
      console.error("[coherent] message handling failed", e);
      sendResponse({ ok: false, error: String(e) });
    }
  })();
  return true; // async sendResponse
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5 });
  schedulePatrol();
  console.log("[coherent] installed — command poll (30s) + jittered patrol registered");
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5 });
  schedulePatrol();
});

// On SW boot, ensure the patrol alarm EXISTS, but never reset an existing one
// (that would push the timer back on every 30s wake and the patrol would never
// fire). Alarms persist across SW restarts, so this only arms it if missing.
chrome.alarms.get(PATROL_ALARM, (a) => { if (!a) schedulePatrol(); });

chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === POLL_ALARM) pollOnce();
  else if (a.name === PATROL_ALARM) runPatrol();
});
