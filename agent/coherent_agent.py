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
        "linkedin_url": "https://www.linkedin.com/feed/",
        "chrome_path": "",          # auto-detected if empty
        "chrome_profile_dir": "",   # defaults to %LOCALAPPDATA%\\CoherentOutreach\\chrome-profile
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


def main() -> int:
    cfg = load_config()
    log.info("agent starting; repo=%s", cfg["repo_dir"])
    chrome = find_chrome(cfg)
    if not chrome:
        log.error("Chrome not found. Set 'chrome_path' in agent.config.json.")
        return 1

    while True:
        try:
            wait_for_internet()
            if cfg.get("start_backend"):
                ensure_backend(cfg)
            ensure_chrome(cfg, chrome)
        except Exception as e:  # noqa: BLE001
            log.exception("agent loop error: %s", e)
        time.sleep(int(cfg["check_interval_seconds"]))


if __name__ == "__main__":
    sys.exit(main())
