"""
watchdog.py — Corvus health guardian for Mike's session daemons.

Runs as a persistent background process at Mike's logon (Task Scheduler).
Every 2 minutes: checks master_dispatcher (port 9200) and telegram_claude_bridge
(process list). Restarts anything that's down and sends a Telegram alert.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
sys.path.insert(0, str(ROOT))

BOT_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
PYTHONW        = r"C:\Python314\pythonw.exe"
MASTER_PORT    = 9200
CAPTURE_PORT   = 8703
CHECK_INTERVAL = 60  # seconds

# Mike-only mode: if CORVUS_ACCOUNT is set, capture_server + worker also run here.
CORVUS_ACCOUNT = os.environ.get("CORVUS_ACCOUNT", "")
MIKE_MODE      = bool(CORVUS_ACCOUNT)

MASTER_SCRIPT  = str(ROOT / "corvus_hack" / "master_dispatcher.py")
BRIDGE_SCRIPT  = str(ROOT / "telegram_claude_bridge.py")
CAPTURE_SCRIPT = str(ROOT / "corvus_hack" / "capture_server.py")
WORKER_SCRIPT  = str(ROOT / "corvus_hack" / "worker.py")


def _admin_chat_ids() -> list[int]:
    try:
        from telegram_mcp._bot import admin_chat_ids
        return admin_chat_ids()
    except Exception:
        return []


def _port_open(port: int) -> bool:
    try:
        s = socket.create_connection(("localhost", port), timeout=2)
        s.close()
        return True
    except Exception:
        return False


def _process_running(script_fragment: str) -> bool:
    """True if any python process has this script name in its command line."""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        result = subprocess.run(
            [
                "powershell", "-NonInteractive", "-Command",
                f"(Get-WmiObject Win32_Process | Where-Object {{$_.CommandLine -like '*{script_fragment}*'}}).Count",
            ],
            capture_output=True, text=True, timeout=10,
            startupinfo=si,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        count = result.stdout.strip()
        return count.isdigit() and int(count) > 0
    except Exception:
        return True  # assume running on check failure


def _start_bg(script: str, args: list | None = None) -> None:
    cmd = [PYTHONW, script] + (args or [])
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        startupinfo=si,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
    )


def _alert(msg: str) -> None:
    if not BOT_TOKEN:
        return
    try:
        import requests
        for chat_id in _admin_chat_ids():
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": f"[Corvus Watchdog] {msg}"},
                timeout=10,
            )
    except Exception:
        pass


def _check() -> None:
    issues: list[str] = []

    if not _port_open(MASTER_PORT):
        print("[watchdog] master_dispatcher DOWN — restarting")
        _start_bg(MASTER_SCRIPT)
        time.sleep(5)
        issues.append("master_dispatcher was down — restarted automatically")

    if not _process_running("telegram_claude_bridge"):
        print("[watchdog] telegram_claude_bridge DOWN — restarting")
        _start_bg(BRIDGE_SCRIPT, args=["--stream"])
        issues.append("telegram_claude_bridge was down — restarted automatically")

    if MIKE_MODE:
        if not _port_open(CAPTURE_PORT):
            print("[watchdog] capture_server DOWN — restarting")
            _start_bg(CAPTURE_SCRIPT, args=["8703"])
            time.sleep(3)
            issues.append("capture_server was down — restarted automatically")

        if not _process_running("worker.py"):
            print("[watchdog] worker DOWN — restarting")
            _start_bg(WORKER_SCRIPT)
            issues.append("worker was down — restarted automatically")

    if issues:
        _alert("\n".join(issues))
        print(f"[watchdog] Issues resolved: {issues}")
    else:
        print("[watchdog] All healthy.")


def main() -> None:
    mode = f"Mike-only (CORVUS_ACCOUNT={CORVUS_ACCOUNT})" if MIKE_MODE else "multi-account"
    print(f"[watchdog] Starting — {mode} mode, health checks every {CHECK_INTERVAL}s")
    # Wait for Task Scheduler entries and startup-folder VBS scripts to finish
    # launching their processes before the first health check, to avoid spawning
    # duplicate instances of master_dispatcher or telegram_claude_bridge.
    print("[watchdog] Waiting 45s for boot-time processes to settle...")
    time.sleep(45)
    while True:
        try:
            _check()
        except Exception as e:
            print(f"[watchdog] Check error: {e}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
