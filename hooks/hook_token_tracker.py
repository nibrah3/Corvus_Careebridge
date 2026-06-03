"""
hook_token_tracker.py — PostToolUse hook (all tools)

Logs every tool call to SQLite token_events table.
No token counting (Claude Code doesn't expose per-call counts in hooks),
but frequency × tool correlation reveals which tools drive session costs.

Weekly Telegram report identifies top cost-driving tools and sessions.
Low overhead: SQLite write takes < 1ms.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CB_DIR  = Path(os.environ.get("CB_DIR", Path(__file__).resolve().parent.parent))
DB_PATH = CB_DIR / "careerbridge.db"

# Only track these high-volume/high-cost tool categories
# (tracking everything generates too many rows for low-value tools)
TRACK_PREFIXES = [
    "mcp__vps__",
    "mcp__gemini__",
    "mcp__capture__",
    "mcp__cdp__",
    "mcp__ixbrowser__",
    "mcp__firecrawl__",
    "Bash",
    "Read",
    "Write",
    "WebFetch",
    "WebSearch",
]


def _ensure_table() -> None:
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         INTEGER NOT NULL,
                session_id TEXT,
                tool_name  TEXT NOT NULL,
                skill_hint TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS token_events_ts ON token_events (ts DESC)"
        )


def _should_track(tool_name: str) -> bool:
    return any(tool_name.startswith(p) for p in TRACK_PREFIXES)


def main() -> None:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        data = json.loads(raw)
    except Exception:
        sys.exit(0)

    tool_name = (data.get("tool_name") or data.get("tool") or "").strip()
    if not tool_name or not _should_track(tool_name):
        sys.exit(0)

    # Session ID: use PID as rough session proxy
    session_id = str(os.getpid())

    # Skill hint: try to infer from tool name (for reporting clarity)
    skill_hint = ""
    if "vps__get_raw_discoveries" in tool_name or "vps__mark_raw" in tool_name:
        skill_hint = "gate_skill"
    elif "vps__get_raw_schools" in tool_name or "vps__mark_school" in tool_name:
        skill_hint = "schools_skill"
    elif "gemini__upload" in tool_name or "gemini__analyse" in tool_name:
        skill_hint = "annotation_skill"
    elif "ixbrowser__" in tool_name or "cdp__" in tool_name:
        skill_hint = "assessment_skill"

    try:
        _ensure_table()
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute(
                "INSERT INTO token_events (ts, session_id, tool_name, skill_hint) "
                "VALUES (?,?,?,?)",
                (int(time.time()), session_id, tool_name, skill_hint),
            )
    except Exception:
        pass  # Never block Claude Code for a logging failure

    sys.exit(0)


if __name__ == "__main__":
    main()
