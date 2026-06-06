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


def main():
    log.info("state_daemon starting — role=%s  cb_dir=%s", ROLE, CB_DIR)
    last_state_write  = 0.0
    last_heartbeat    = 0.0

    # Write state immediately on startup
    state = collect_state()
    write_state(state)
    last_state_write = time.monotonic()
    _write_heartbeat()
    last_heartbeat = time.monotonic()

    while True:
        now = time.monotonic()

        # Write full state every 60s
        if now - last_state_write >= 60:
            state = collect_state()
            write_state(state)
            last_state_write = now

        # Write heartbeat every 30s (TTL=90s so two missed beats before expiry)
        if now - last_heartbeat >= 30:
            _write_heartbeat()
            last_heartbeat = now

        # Check pending_instructions inbox every 10s
        check_inbox()
        time.sleep(10)


if __name__ == "__main__":
    main()
