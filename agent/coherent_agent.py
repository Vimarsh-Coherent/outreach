"""Coherent Outreach — Windows startup agent.

Runs at login (via Task Scheduler — see install_agent.ps1). It:
  1. waits until the machine actually has internet,
  2. (optionally) starts the backend (uvicorn) if it isn't already up,
  3. launches Chrome with the unpacked extension loaded + LinkedIn open, in a
     dedicated profile so the login + extension persist,
  4. monitors everything and relaunches whatever dies.

Pure standard-library — no pip installs needed. Configure via agent.config.json
next to this file (copy agent.config.example.json).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "agent.config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(HERE / "agent.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("coherent.agent")


def load_config() -> dict:
    defaults = {
        "repo_dir": str(HERE.parent),
        "backend_dir": str(HERE.parent / "backend"),
        "extension_dir": str(HERE.parent / "extension"),
        "start_backend": True,
        "backend_host": "127.0.0.1",
        "backend_port": 8000,
        # WhatsApp (Baileys) sidecar — the agent keeps it alive like the backend.
        # On first run it auto-installs node deps if node_modules is missing.
        "start_whatsapp_sidecar": True,
        "whatsapp_sidecar_dir": str(HERE.parent / "whatsapp-sidecar"),
        "whatsapp_port": 8085,
        "whatsapp_api_key": "",   # shared secret; must match backend WA_API_KEY (empty = local only)
        "node_path": "",          # auto-detected if empty
        "linkedin_url": "https://www.linkedin.com/feed/",
        # Auto-open LinkedIn when online. Set false to stop the agent opening
        # LinkedIn tabs (it still manages the backend + WhatsApp sidecar).
        "open_linkedin": True,
        # dedicated_profile=True  → launch an isolated Chrome profile w/ the
        #   unpacked extension auto-loaded (relaunched if it dies).
        # dedicated_profile=False → open LinkedIn in the user's EXISTING default
        #   Chrome (already has the extension + login), once per online session.
        "dedicated_profile": True,
        "chrome_path": "",          # auto-detected if empty
        "chrome_profile_dir": "",   # dedicated mode: defaults to %LOCALAPPDATA%\\CoherentOutreach\\chrome-profile
        # existing-chrome mode: which installed Chrome profile to open LinkedIn
        # in (the directory name under "User Data", e.g. "Default", "Profile 1").
        # Empty = Chrome's last-used profile.
        "chrome_profile_directory": "",
        "check_interval_seconds": 30,
    }
    if CONFIG_PATH.exists():
        try:
            defaults.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            log.warning("could not read config (%s) — using defaults", e)
    if not defaults["chrome_profile_dir"]:
        base = os.environ.get("LOCALAPPDATA", str(Path.home()))
        defaults["chrome_profile_dir"] = str(Path(base) / "CoherentOutreach" / "chrome-profile")
    return defaults


def find_chrome(cfg: dict) -> str | None:
    if cfg.get("chrome_path") and Path(cfg["chrome_path"]).exists():
        return cfg["chrome_path"]
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    found = shutil.which("chrome") or shutil.which("chrome.exe")
    return found


def find_node(cfg: dict) -> str | None:
    if cfg.get("node_path") and Path(cfg["node_path"]).exists():
        return cfg["node_path"]
    candidates = [
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\nodejs\node.exe"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return shutil.which("node") or shutil.which("node.exe")


def port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def have_internet() -> bool:
    # DNS-level + a real HTTPS HEAD to LinkedIn so captive portals don't fool us.
    if not port_open("1.1.1.1", 53, timeout=3):
        return False
    try:
        req = urllib.request.Request("https://www.linkedin.com/", method="HEAD")
        urllib.request.urlopen(req, timeout=6)
        return True
    except Exception:  # noqa: BLE001
        return False


def wait_for_internet() -> None:
    waited = 0
    while not have_internet():
        if waited % 60 == 0:
            log.info("waiting for internet…")
        time.sleep(5)
        waited += 5


# ── Backend ─────────────────────────────────────────────────────────────────
_backend_proc: subprocess.Popen | None = None


def ensure_backend(cfg: dict) -> None:
    global _backend_proc
    if port_open(cfg["backend_host"], cfg["backend_port"]):
        return  # already up (maybe started manually) — leave it alone
    if _backend_proc and _backend_proc.poll() is None:
        return  # we started it and it's still booting
    py = Path(cfg["backend_dir"]) / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        log.error("backend venv python not found at %s — start the backend manually", py)
        return
    log.info("starting backend (uvicorn)…")
    _backend_proc = subprocess.Popen(
        [str(py), "-m", "uvicorn", "outreach.main:app",
         "--host", cfg["backend_host"], "--port", str(cfg["backend_port"]),
         "--app-dir", "src"],
        cwd=cfg["backend_dir"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    # Give it a moment to bind the port.
    for _ in range(20):
        if port_open(cfg["backend_host"], cfg["backend_port"]):
            log.info("backend is up on %s:%s", cfg["backend_host"], cfg["backend_port"])
            return
        time.sleep(1)


# ── WhatsApp sidecar (Baileys) ───────────────────────────────────────────────
_wa_proc: subprocess.Popen | None = None
_wa_deps_checked = False


def _ensure_wa_deps(cfg: dict, npm: str | None) -> bool:
    """Install node deps once if node_modules is missing. Returns True if ready."""
    global _wa_deps_checked
    sidecar = Path(cfg["whatsapp_sidecar_dir"])
    if (sidecar / "node_modules").is_dir():
        return True
    if _wa_deps_checked:
        return (sidecar / "node_modules").is_dir()
    _wa_deps_checked = True
    if not npm:
        log.error("npm not found — cannot install WhatsApp sidecar deps")
        return False
    log.info("installing WhatsApp sidecar deps (first run, one-time)…")
    try:
        subprocess.run(
            [npm, "install", "--no-audit", "--no-fund"],
            cwd=str(sidecar), check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:  # noqa: BLE001
        log.error("npm install failed for WhatsApp sidecar: %s", e)
        return False
    return (sidecar / "node_modules").is_dir()


def ensure_whatsapp_sidecar(cfg: dict, node: str) -> None:
    global _wa_proc
    if port_open(cfg["backend_host"], int(cfg["whatsapp_port"])):
        return  # already up (we or a manual run) — leave it
    if _wa_proc and _wa_proc.poll() is None:
        return  # we started it and it's still booting
    sidecar = Path(cfg["whatsapp_sidecar_dir"])
    server = sidecar / "server.js"
    if not server.exists():
        log.error("whatsapp sidecar not found at %s", server)
        return
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not _ensure_wa_deps(cfg, npm):
        return
    env = dict(os.environ)
    env["WA_PORT"] = str(cfg["whatsapp_port"])
    env["WA_API_KEY"] = cfg.get("whatsapp_api_key", "") or ""
    env["WA_SESSION_PATH"] = str(sidecar / "data" / "wa-session")
    env["WA_BACKEND_INBOUND_URL"] = (
        f"http://{cfg['backend_host']}:{cfg['backend_port']}/api/whatsapp/inbound"
    )
    log.info("starting WhatsApp sidecar (Baileys) on port %s…", cfg["whatsapp_port"])
    _wa_proc = subprocess.Popen(
        [node, "server.js"],
        cwd=str(sidecar), env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(15):
        if port_open(cfg["backend_host"], int(cfg["whatsapp_port"])):
            log.info("WhatsApp sidecar is up on port %s", cfg["whatsapp_port"])
            return
        time.sleep(1)


# ── Chrome ──────────────────────────────────────────────────────────────────
_chrome_proc: subprocess.Popen | None = None


def ensure_chrome(cfg: dict, chrome: str) -> None:
    global _chrome_proc
    if _chrome_proc and _chrome_proc.poll() is None:
        return  # still running
    Path(cfg["chrome_profile_dir"]).mkdir(parents=True, exist_ok=True)
    log.info("launching Chrome with extension + LinkedIn…")
    _chrome_proc = subprocess.Popen([
        chrome,
        f"--user-data-dir={cfg['chrome_profile_dir']}",
        f"--load-extension={cfg['extension_dir']}",
        "--no-first-run",
        "--no-default-browser-check",
        "--restore-last-session",
        cfg["linkedin_url"],
    ])


def open_linkedin_existing(cfg: dict, chrome: str) -> None:
    """Open LinkedIn in the user's DEFAULT Chrome (existing profile + already-
    installed extension). When Chrome is already running this just adds a tab and
    the launched process returns immediately — so we do NOT track it; the main
    loop calls this only on an offline→online transition (incl. boot), so it
    opens one tab per reconnect instead of spamming a tab every interval."""
    prof = cfg.get("chrome_profile_directory") or ""
    log.info("opening LinkedIn in existing Chrome (profile=%s)…", prof or "last-used")
    args = [chrome]
    if prof:
        args.append(f"--profile-directory={prof}")
    args.append(cfg["linkedin_url"])
    try:
        subprocess.Popen(args)
    except Exception as e:  # noqa: BLE001
        log.warning("could not open LinkedIn in existing Chrome: %s", e)


def main() -> int:
    cfg = load_config()
    dedicated = bool(cfg.get("dedicated_profile", True))
    log.info("agent starting; repo=%s; mode=%s", cfg["repo_dir"],
             "dedicated-profile" if dedicated else "existing-chrome")
    chrome = find_chrome(cfg)
    if not chrome:
        log.error("Chrome not found. Set 'chrome_path' in agent.config.json.")
        return 1

    node = find_node(cfg) if cfg.get("start_whatsapp_sidecar") else None
    if cfg.get("start_whatsapp_sidecar") and not node:
        log.warning("Node.js not found — WhatsApp sidecar disabled. Install Node or set 'node_path'.")

    was_online = False
    while True:
        try:
            online = have_internet()
            if online:
                if cfg.get("start_backend"):
                    ensure_backend(cfg)
                if cfg.get("start_whatsapp_sidecar") and node:
                    ensure_whatsapp_sidecar(cfg, node)
                # LinkedIn auto-open (set "open_linkedin": false to disable).
                if cfg.get("open_linkedin", True):
                    if dedicated:
                        # Keep the isolated Chrome alive (relaunch if it died).
                        ensure_chrome(cfg, chrome)
                    elif not was_online:
                        # Existing-Chrome mode: open LinkedIn once per reconnect.
                        open_linkedin_existing(cfg, chrome)
            elif was_online:
                log.info("internet lost")
            was_online = online
        except Exception as e:  # noqa: BLE001
            log.exception("agent loop error: %s", e)
        time.sleep(int(cfg["check_interval_seconds"]))


if __name__ == "__main__":
    sys.exit(main())
