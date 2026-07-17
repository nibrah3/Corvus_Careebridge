"""
VPS SSH tunnel manager — replaces vps_tunnel.ps1.
Run with pythonw.exe for zero console window, ever.
Opens: localhost:6380->VPS:6379 (Redis)
        localhost:5433->VPS:5432 (Postgres)
        localhost:3101->VPS:3100 (Crawlee)
        localhost:7788->VPS:7788 (Firecrawl)
"""
from __future__ import annotations
import json, logging, os, socket, subprocess, sys, time, urllib.request
from pathlib import Path

CB_DIR  = Path(__file__).resolve().parent.parent
LOG     = CB_DIR / "logs" / "tunnel.log"
LOG.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(str(LOG), encoding="utf-8")],
)
log = logging.getLogger("tunnel")

# Load .env
_env = CB_DIR / ".env"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _, _v = _line.partition("=")
            _k = _k.strip(); _v = _v.strip()
            if _k and _k not in os.environ:
                os.environ[_k] = _v

SSH_EXE  = "C:/WINDOWS/System32/OpenSSH/ssh.exe"
SSH_KEY  = Path.home() / ".ssh" / "careerbridge_vps"
VPS_HOST = "root@77.42.91.185"

TUNNEL_PORTS = {
    6380: "Redis (6380→6379)",
    5433: "Postgres (5433→5432)",
    3101: "Crawlee (3101→3100)",
    7788: "Firecrawl (7788→7788)",
}

ALERT_AFTER     = 3      # Telegram alert after this many consecutive failures
RECONNECT_DELAY = 5      # seconds between reconnect attempts


def _port_up(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _all_up() -> bool:
    return all(_port_up(p) for p in TUNNEL_PORTS)


def _tg(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    if not token or not chat:
        return
    try:
        body = json.dumps({"chat_id": int(chat), "text": text}).encode()
        req  = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body, headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass


def _start_tunnel() -> subprocess.Popen:
    args = [
        SSH_EXE,
        "-i", str(SSH_KEY),
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=5",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "TCPKeepAlive=yes",
        "-L", "6380:127.0.0.1:6379",
        "-L", "5433:127.0.0.1:5432",
        "-L", "3101:127.0.0.1:3100",
        "-L", "7788:127.0.0.1:7788",
        "-N", VPS_HOST,
    ]
    return subprocess.Popen(
        args,
        creationflags=subprocess.CREATE_NO_WINDOW,  # zero window flash
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


log.info("VPS tunnel manager starting (pythonw — no window)")

attempt = 0
fails   = 0
proc: subprocess.Popen | None = None

while True:
    attempt += 1
    if attempt > 1:
        log.info("Reconnecting (attempt %d)...", attempt)
        time.sleep(RECONNECT_DELAY)

    try:
        proc = _start_tunnel()
    except Exception as e:
        log.error("Failed to start SSH: %s", e)
        fails += 1
        continue

    # Wait for ports to bind (up to 10 s)
    for _ in range(10):
        time.sleep(1)
        if _all_up():
            break

    if _all_up():
        log.info("Tunnel UP — all 4 ports bound (PID %d)", proc.pid)
        if fails > 0:
            _tg(f"[TUNNEL] Recovered after {fails} failure(s).")
        fails = 0
    else:
        down = [TUNNEL_PORTS[p] for p in TUNNEL_PORTS if not _port_up(p)]
        log.warning("Tunnel started but ports still down: %s", down)

    # Monitor until process exits
    proc.wait()
    alive_sec = 0  # approximate — we don't track start time precisely

    log.info("SSH exited (PID %d). Reconnecting...", proc.pid)
    fails += 1

    if fails == ALERT_AFTER:
        _tg(f"[TUNNEL] SSH tunnel has failed {fails} consecutive times. Checking VPS connectivity.")
    elif fails > ALERT_AFTER and fails % 5 == 0:
        _tg(f"[TUNNEL] SSH tunnel still failing ({fails} attempts). Manual check may be needed.")
