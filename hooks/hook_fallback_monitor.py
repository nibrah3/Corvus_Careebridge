"""
hook_fallback_monitor.py — PostToolUse hook on mcp__capture__screenshot

Fires after any screenshot tool call. If the screenshot was taken for task
work (not debugging), activates fallback mode:
  - Sets corvus:fallback_mode in Redis (TTL 3600s)
  - Logs the event to SQLite
  - Injects a mandatory instruction: all answer submissions must be gated

This enforces the rule from sop_fallback_rules.md without relying on Claude
remembering to apply it.
"""
import json
import os
import sys
import time

CB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CB_DIR)

REDIS_HOST = os.environ.get("VPS_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("VPS_REDIS_PORT", "6380"))
SQLITE_PATH = os.path.join(CB_DIR, "data", "careerbridge.db")


def _redis_setex(key: str, seconds: int, value: str) -> None:
    import socket
    try:
        with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=3) as sock:
            cmd = f"*4\r\n$5\r\nSETEX\r\n${len(key)}\r\n{key}\r\n${len(str(seconds))}\r\n{seconds}\r\n${len(value)}\r\n{value}\r\n"
            sock.sendall(cmd.encode())
            sock.recv(64)
    except Exception:
        pass


def _log_fallback(purpose: str, session_context: str) -> None:
    try:
        import sqlite3
        conn = sqlite3.connect(SQLITE_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fallback_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                purpose TEXT,
                context TEXT
            )
        """)
        conn.execute(
            "INSERT INTO fallback_events (ts, purpose, context) VALUES (?,?,?)",
            (int(time.time()), purpose, session_context[:500]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        data = json.loads(raw)
    except Exception:
        sys.exit(0)

    # Read the purpose argument from the tool call input
    tool_input = data.get("tool_input") or {}
    purpose = (tool_input.get("purpose") or "").lower().strip()

    # Debug screenshots are allowed — only flag task-work screenshots
    if purpose == "debug":
        sys.exit(0)

    # Non-debug screenshot during task work: activate fallback mode
    _redis_setex("corvus:fallback_mode", 3600, "1")

    # Log the event
    context = json.dumps({
        "tool":    data.get("tool_name", "screenshot"),
        "purpose": purpose or "(none)",
        "ts":      int(time.time()),
    })
    _log_fallback(purpose or "unspecified", context)

    # Inject mandatory instruction into Claude's context
    print(
        "⚠️  FALLBACK MODE ACTIVATED\n"
        "A screenshot was taken for task work (purpose != 'debug').\n"
        "For the remainder of this session:\n"
        "  • ALL answer submissions must go to supervised gate before executing.\n"
        "  • Do NOT auto-submit any answer — call mcp__vps__push_gate_answer first.\n"
        "  • Call mcp__telegram__notify('⚠️ Session in fallback mode — gate active') now.\n"
        "This rule cannot be bypassed mid-session."
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
