"""
agent_listener.py — Always-on agent listener. Runs on both machines.

Subscribes to all three communication tiers simultaneously via agent_comms.py:
  Tier 1: Redis pub/sub  (fastest)
  Tier 2: Postgres NOTIFY (fallback)
  Tier 3: Telegram       (last-resort, human-visible)

When an instruction arrives addressed to this machine, it pipes it into
claude --print --dangerously-skip-permissions and publishes the response back
on the best available tier.

Run silently:  pythonw scripts/agent_listener.py
Run verbosely: python  scripts/agent_listener.py
"""
from __future__ import annotations
import json, logging, os, subprocess, sys, threading, time
from pathlib import Path

CB_DIR = Path(__file__).resolve().parent.parent
LOG    = CB_DIR / "logs" / "agent_listener.log"
LOG.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    handlers=[
        logging.FileHandler(str(LOG), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("listener")

for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k=k.strip(); v=v.strip()
        if k and k not in os.environ: os.environ[k] = v

ROLE = os.environ.get("CB_SYNC_ROLE", "primary")

sys.path.insert(0, str(CB_DIR))
from scripts.agent_comms import send, listen, write_heartbeat

# Find claude executable — pythonw doesn't inherit shell PATH
_CLAUDE_CANDIDATES = [
    Path(os.environ.get("APPDATA","")) / "npm/claude.cmd",
    Path(os.environ.get("APPDATA","")) / "npm/claude",
    Path.home() / "AppData/Roaming/npm/claude.cmd",
    Path("/usr/local/bin/claude"),
]
CLAUDE_EXE = next((str(p) for p in _CLAUDE_CANDIDATES if p.exists()), "claude")
log.info("claude executable: %s", CLAUDE_EXE)


def run_claude(instruction: str) -> str:
    """Invoke claude --print with prompt via stdin to avoid Windows
    cmd.exe mangling newlines/special chars in command-line arguments."""
    log.info("Invoking claude --print for: %s", instruction[:100])
    prompt = (
        f"You are the CareerBridge agent on the {ROLE} machine ({CB_DIR}). "
        f"A peer engineer sent this instruction:\n\n{instruction}\n\n"
        f"Execute it using your tools. Reply concisely with what you did and any issues."
    )
    try:
        result = subprocess.run(
            [CLAUDE_EXE, "--print", "--dangerously-skip-permissions", "-"],
            input=prompt,
            cwd=str(CB_DIR),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=300,
        )
        out = result.stdout.strip()
        if not out:
            # Fallback: pass as direct arg (some versions don't support stdin via -)
            result = subprocess.run(
                [CLAUDE_EXE, "--print", "--dangerously-skip-permissions", prompt],
                cwd=str(CB_DIR),
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=300,
            )
            out = result.stdout.strip()
        return out or f"[exit {result.returncode}] {result.stderr[:300]}"
    except subprocess.TimeoutExpired:
        return "[TIMEOUT after 300s]"
    except Exception as e:
        return f"[ERROR: {e}]"


def on_message(payload: dict) -> None:
    sender   = payload.get("from", "unknown")
    msg_type = payload.get("type", "instruction")
    text     = payload.get("msg", "")
    msg_id   = payload.get("id", "?")

    log.info("MSG from %s (id=%s type=%s): %s", sender, msg_id, msg_type, text[:100])

    if msg_type in ("response", "status"):
        log.info("RESPONSE from %s: %s", sender, text[:400])
        return

    response = run_claude(text)
    send(to=sender, msg=response, msg_type="response")


def _watch_for_updates() -> None:
    """Background thread — checks agent_listener.py and agent_comms.py mtimes
    every 15s. If either file changes on disk (new git pull), re-execs the
    process so the update takes effect without manual restart."""
    watched = [
        CB_DIR / "scripts" / "agent_listener.py",
        CB_DIR / "scripts" / "agent_comms.py",
    ]
    mtimes = {p: p.stat().st_mtime for p in watched if p.exists()}

    while True:
        time.sleep(15)
        for p in watched:
            if not p.exists():
                continue
            try:
                new_mtime = p.stat().st_mtime
                if new_mtime != mtimes.get(p):
                    log.info("Detected change in %s — restarting agent_listener...", p.name)
                    time.sleep(2)  # let the write finish
                    # Spawn fresh process then exit this one
                    subprocess.Popen(
                        [sys.executable, str(CB_DIR / "scripts" / "agent_listener.py")],
                        cwd=str(CB_DIR),
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                    sys.exit(0)
            except Exception as e:
                log.debug("Watcher error for %s: %s", p.name, e)


def main():
    log.info("agent_listener starting — role=%s", ROLE)

    # Watch for file changes and auto-restart on update
    threading.Thread(target=_watch_for_updates, daemon=True).start()

    # Announce online
    send("broadcast", f"{ROLE} agent online at {CB_DIR}", msg_type="status")

    # Listen blocks forever; heartbeat is handled by state_daemon
    listen(on_message)


if __name__ == "__main__":
    main()
