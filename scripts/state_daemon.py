"""
state_daemon.py — Runs on each machine. Two jobs:
  1. Every 60s: write this machine's full state to machine_state table on VPS DB.
  2. Every 10s: check pending_instructions table for items addressed to this machine.
     If found: write to logs/inbox.json so hook_inbox.py can inject into live session.

Run silently:
    pythonw scripts/state_daemon.py
"""
from __future__ import annotations
import json, logging, os, socket, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

CB_DIR  = Path(__file__).resolve().parent.parent
INBOX   = CB_DIR / "logs" / "inbox.json"
LOG     = CB_DIR / "logs" / "state_daemon.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
INBOX.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[logging.FileHandler(str(LOG), encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("state")

for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k=k.strip(); v=v.strip()
        if k and k not in os.environ: os.environ[k] = v

DSN    = os.environ.get("VPS_PG_DSN", "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge")
ROLE   = os.environ.get("CB_SYNC_ROLE", "primary")
APPDATA = Path(os.environ.get("APPDATA", ""))
STARTUP = APPDATA / "Microsoft/Windows/Start Menu/Programs/Startup"

MCP_PORTS    = {"humanizer":8701,"capture":8702,"uia":8703,"browser":8704,
                "gemini":8705,"telegram":8706,"answer":8707,"sqlite":8708,
                "memory":8709,"dom":8710,"cdp":8712,"vps":8713,"schools":8714,"ixbrowser":8715}
TUNNEL_PORTS = {"redis":6380,"postgres":5433,"crawlee":3101,"firecrawl":7788}


def _port_up(port: int) -> bool:
    s = socket.socket(); s.settimeout(0.4)
    up = s.connect_ex(("127.0.0.1", port)) == 0; s.close(); return up


def _hook_paths_ok(settings_path: Path) -> tuple[bool, dict]:
    """Check whether settings.json hook paths point to CB_DIR."""
    if not settings_path.exists():
        return False, {}
    try:
        raw = settings_path.read_text(encoding="utf-8")
        hooks = json.loads(raw).get("hooks", {})
        paths = {}
        for section, entries in hooks.items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    paths[cmd] = str(CB_DIR) in cmd
        all_ok = all(paths.values()) if paths else False
        return all_ok, paths
    except Exception as e:
        return False, {"error": str(e)}


def collect_state() -> dict:
    settings = CB_DIR / ".claude" / "settings.json"
    hooks_ok, hook_paths = _hook_paths_ok(settings)

    try:
        sha = subprocess.run(["git","rev-parse","HEAD"], cwd=str(CB_DIR),
                             capture_output=True, text=True).stdout.strip()
    except Exception:
        sha = "unknown"

    try:
        pip_r = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                               capture_output=True, text=True)
        pip_count = len([l for l in pip_r.stdout.splitlines() if l.strip()])
    except Exception:
        pip_count = -1

    return {
        "machine":       ROLE,
        "cb_dir":        str(CB_DIR),
        "python_exe":    sys.executable,
        "git_sha":       sha,
        "pip_count":     pip_count,
        "mcp_ports":     {k: _port_up(v) for k, v in MCP_PORTS.items()},
        "tunnel_ports":  {k: _port_up(v) for k, v in TUNNEL_PORTS.items()},
        "startup_vbs":   [f.name for f in STARTUP.glob("*.vbs")] if STARTUP.exists() else [],
        "hook_paths_ok": hooks_ok,
        "hook_paths":    hook_paths,
    }


def write_state(state: dict) -> None:
    import psycopg2
    try:
        conn = psycopg2.connect(DSN, connect_timeout=8)
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO machine_state
                (machine, cb_dir, python_exe, git_sha, pip_count, mcp_ports,
                 tunnel_ports, startup_vbs, hook_paths_ok, hook_paths, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (machine) DO UPDATE SET
                cb_dir=EXCLUDED.cb_dir, python_exe=EXCLUDED.python_exe,
                git_sha=EXCLUDED.git_sha, pip_count=EXCLUDED.pip_count,
                mcp_ports=EXCLUDED.mcp_ports, tunnel_ports=EXCLUDED.tunnel_ports,
                startup_vbs=EXCLUDED.startup_vbs, hook_paths_ok=EXCLUDED.hook_paths_ok,
                hook_paths=EXCLUDED.hook_paths, updated_at=NOW()
        """, (
            state["machine"], state["cb_dir"], state["python_exe"], state["git_sha"],
            state["pip_count"],
            json.dumps(state["mcp_ports"]), json.dumps(state["tunnel_ports"]),
            json.dumps(state["startup_vbs"]), state["hook_paths_ok"],
            json.dumps(state["hook_paths"]),
        ))
        conn.commit(); conn.close()
        log.info("State written to DB: sha=%s pip=%d hooks_ok=%s",
                 state["git_sha"][:8], state["pip_count"], state["hook_paths_ok"])
    except Exception as e:
        log.error("write_state failed: %s", e)


def check_inbox() -> None:
    """Poll pending_instructions for items addressed to this machine. Deliver to inbox.json."""
    import psycopg2, psycopg2.extras
    try:
        conn = psycopg2.connect(DSN, connect_timeout=8)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT id, from_machine, instruction FROM pending_instructions
            WHERE to_machine=%s AND status='pending'
            ORDER BY id LIMIT 5
        """, (ROLE,))
        rows = [dict(r) for r in cur.fetchall()]

        if rows:
            # Write to inbox.json (hook reads this)
            existing = []
            if INBOX.exists():
                try: existing = json.loads(INBOX.read_text(encoding="utf-8"))
                except Exception: pass
            existing.extend(rows)
            INBOX.write_text(json.dumps(existing, indent=2, default=str), encoding="utf-8")

            # Mark as delivered
            ids = [r["id"] for r in rows]
            cur.execute(
                "UPDATE pending_instructions SET status='delivered', delivered_at=NOW() WHERE id=ANY(%s)",
                (ids,)
            )
            conn.commit()
            log.info("Delivered %d instruction(s) to inbox.json", len(rows))

        conn.close()
    except Exception as e:
        log.error("check_inbox failed: %s", e)


def _write_heartbeat() -> None:
    """Write cb:heartbeat:{role} to Redis with TTL. VPS MCP reads this to show online nodes."""
    try:
        import redis
        r = redis.Redis(host="127.0.0.1",
                        port=int(os.environ.get("VPS_REDIS_PORT", 6380)),
                        decode_responses=True, socket_connect_timeout=2)
        r.setex(f"cb:heartbeat:{ROLE}", 90, "1")
    except Exception as e:
        log.debug("Heartbeat write failed (tunnel down?): %s", e)


REPORT_FILE = CB_DIR / "logs" / "status_report.json"


def _send_report(state: dict) -> None:
    """Write 30-minute status report to logs/status_report.json.
    hook_report.py (UserPromptSubmit) injects it into the Claude Code session."""
    mcp   = state.get("mcp_ports", {})
    tun   = state.get("tunnel_ports", {})
    vbs   = state.get("startup_vbs", [])
    hooks = state.get("hook_paths_ok", False)
    sha   = (state.get("git_sha") or "")[:8]
    pip   = state.get("pip_count", 0)

    mcp_down = [k for k, v in mcp.items() if not v]
    tun_down = [k for k, v in tun.items() if not v]

    # Pending instructions count
    pending = 0
    try:
        import psycopg2
        conn = psycopg2.connect(DSN, connect_timeout=5)
        cur  = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM pending_instructions WHERE to_machine=%s AND status='pending'", (ROLE,))
        pending = cur.fetchone()[0]
        conn.close()
    except Exception:
        pass

    # Last line from agent_listener log
    last_msg = ""
    listener_log = CB_DIR / "logs" / "agent_listener.log"
    if listener_log.exists():
        try:
            lines = listener_log.read_text(encoding="utf-8", errors="replace").splitlines()
            last = next((l for l in reversed(lines) if "MSG from" in l or "RESPONSE" in l), "")
            if last:
                last_msg = f"\nLast msg: {last[-120:]}"
        except Exception:
            pass

    status_mcp = "all UP" if not mcp_down else f"DOWN: {', '.join(mcp_down)}"
    status_tun = "all UP" if not tun_down else f"DOWN: {', '.join(tun_down)}"
    hooks_str  = "OK" if hooks else "WRONG PATHS"

    report = {
        "machine":  ROLE,
        "time":     time.strftime("%Y-%m-%d %H:%M"),
        "sha":      sha,
        "pip":      pip,
        "hooks":    hooks_str,
        "mcp":      status_mcp,
        "tunnel":   status_tun,
        "vbs":      f"{len(vbs)}/4",
        "pending":  pending,
        "last_msg": last_msg.strip(),
        "shown":    False,
    }
    REPORT_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("30-min report written to %s", REPORT_FILE)


def listen_inbox() -> None:
    """
    Subscription-based inbox delivery using Postgres LISTEN/NOTIFY.
    Runs in a background thread alongside the main loop.

    When pending_instructions INSERT fires NOTIFY cb_inbox_{role},
    this thread wakes immediately and delivers to inbox.json — no polling lag.
    Falls back to check_inbox() poll every 60s as belt-and-suspenders.
    """
    import psycopg2, select as _select

    channel = f"cb_inbox_{ROLE}"
    conn    = None

    def _connect():
        c = psycopg2.connect(DSN, connect_timeout=10)
        c.set_isolation_level(0)   # autocommit required for LISTEN
        c.cursor().execute(f"LISTEN {channel}")
        log.info("[LISTEN] Subscribed to Postgres channel: %s", channel)
        return c

    last_fallback = time.monotonic()

    while True:
        try:
            if conn is None or conn.closed:
                conn = _connect()

            # Block up to 30s waiting for a notification
            readable, _, _ = _select.select([conn], [], [], 30)

            if readable:
                conn.poll()
                while conn.notifies:
                    n = conn.notifies.pop(0)
                    log.info("[LISTEN] NOTIFY on %s payload=%s — delivering now", n.channel, n.payload)
                    check_inbox()

            # Fallback poll every 60s in case a NOTIFY was lost
            if time.monotonic() - last_fallback >= 60:
                check_inbox()
                last_fallback = time.monotonic()

        except Exception as e:
            log.warning("[LISTEN] connection error: %s — reconnecting in 10s", e)
            try:
                if conn and not conn.closed:
                    conn.close()
            except Exception:
                pass
            conn = None
            time.sleep(10)


def main():
    import threading

    log.info("state_daemon starting — role=%s  cb_dir=%s", ROLE, CB_DIR)
    last_state_write  = 0.0
    last_heartbeat    = 0.0
    last_report       = 0.0

    # Write state immediately on startup
    state = collect_state()
    write_state(state)
    last_state_write = time.monotonic()
    _write_heartbeat()
    last_heartbeat = time.monotonic()

    # Start subscription-based inbox listener in background thread
    t = threading.Thread(target=listen_inbox, daemon=True, name="listen_inbox")
    t.start()
    log.info("LISTEN thread started (channel: cb_inbox_%s)", ROLE)

    while True:
        now = time.monotonic()

        if now - last_state_write >= 60:
            state = collect_state()
            write_state(state)
            last_state_write = now

        if now - last_heartbeat >= 30:
            _write_heartbeat()
            last_heartbeat = now

        if now - last_report >= 1800:
            if not state:
                state = collect_state()
            _send_report(state)
            last_report = now

        time.sleep(30)   # main loop only needs to run every 30s now


if __name__ == "__main__":
    main()
