# Coherent Outreach — Autonomous Agent Setup

Turns the platform into a self-running LinkedIn growth agent: it boots with your
PC, opens Chrome + LinkedIn, runs your campaigns, and performs conservative
human-like activity — with an in-extension dashboard showing everything.

## Components added

| Piece | Where | What it does |
|---|---|---|
| Data endpoints | `backend/.../routes_extension.py` | `/api/extension/campaigns`, `/activity`, `/status` (extension-token auth) |
| Side-panel dashboard | `extension/sidepanel.{html,js}` | Live campaigns, activity log, health, warm-up stats |
| Warm-up engine | `extension/content_linkedin.js` | Feed browsing + varied reactions + follows, conservative caps |
| Startup agent | `agent/coherent_agent.py` | Boots backend + Chrome + LinkedIn at login, keeps them alive |

## One-time setup

### 1. Extension
1. `chrome://extensions` → reload **Coherent Outreach Bridge** (should show **v0.1.3**).
   - Service-worker console should print `build 2026-06-09-dashboard-warmup-v5`.
2. Click the toolbar icon → **Open dashboard** to launch the side panel.
3. The dashboard's **Warm-up** card has an on/off toggle (default **on**, conservative).

### 2. Startup agent (auto-launch on boot)
```powershell
cd C:\Users\vimarsh.CMI\coherent-outreach\agent
.\install_agent.ps1            # registers a Task Scheduler job at logon
Start-ScheduledTask -TaskName CoherentOutreachAgent   # run it now to test
```
The agent (`coherent_agent.py`):
- waits for real internet,
- starts the backend if it isn't already running,
- launches Chrome in a **dedicated profile** (`%LOCALAPPDATA%\CoherentOutreach\chrome-profile`)
  with the extension loaded + LinkedIn open,
- relaunches anything that dies.

**First run:** log into LinkedIn once in that Chrome profile — it persists. Paste
your channel id + token into the extension popup once (also persists).

Config: copy/edit `agent/agent.config.json` (Chrome path, ports, etc.). Logs:
`agent/agent.log`.

Remove auto-start: `Unregister-ScheduledTask -TaskName CoherentOutreachAgent -Confirm:$false`

## Warm-up behavior (conservative defaults)
Runs only on `/feed/`, never while a real command is executing, heavily jittered.
Daily caps (edit `WARMUP.caps` in `content_linkedin.js`):
- reactions: **5/day** (varied — Like/Celebrate/Support/Love/Insightful/Funny)
- follows: **3/day**
- feed-browse sessions: **8/day**

## Notes / current limitations
- **AI selector-healer is paused** until your Anthropic quota resets (2026-07-01).
  Outreach still works (direct selectors); only the auto-repair fallback is off.
- The agent uses a dedicated Chrome profile so automation never disrupts your
  normal browser. Keep that window logged into the LinkedIn account you outreach from.
