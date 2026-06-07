"""
collab_send.py — Post a message into the other machine's inbox via pending_instructions.

The Postgres NOTIFY trigger fires instantly on INSERT, waking the other machine's
state_daemon LISTEN thread, which delivers to inbox.json within milliseconds.
The other machine's hook_inbox.py then injects the message into its live Claude Code
session before the next tool call — no claude --print, no API credits consumed.

Usage (Claude Code calls this as a Bash tool):
    python scripts/collab_send.py primary  "Phase 1 done. Schema created."
    python scripts/collab_send.py secondary "Start your N-Z batch now."
    python scripts/collab_send.py broadcast "Both: git pull origin master"

The script exits immediately after INSERT — delivery confirmation comes via
the other machine's response appearing in YOUR inbox.json on the next exchange.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CB_DIR = Path(__file__).resolve().parent.parent

for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k = k.strip(); v = v.strip()
        if k and k not in os.environ:
            os.environ[k] = v

DSN  = os.environ.get("VPS_PG_DSN", "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge")
ROLE = os.environ.get("CB_SYNC_ROLE", "secondary")


def send(to_machine: str, message: str) -> int:
    """Insert into pending_instructions. Returns the new row ID."""
    import psycopg2

    targets = ["primary", "secondary"] if to_machine == "broadcast" else [to_machine]
    ids = []
    conn = psycopg2.connect(DSN, connect_timeout=8)
    cur  = conn.cursor()
    for target in targets:
        cur.execute(
            """INSERT INTO pending_instructions
               (to_machine, from_machine, instruction, status, created_at)
               VALUES (%s, %s, %s, 'pending', NOW())
               RETURNING id""",
            (target, ROLE, message),
        )
        row_id = cur.fetchone()[0]
        ids.append(row_id)
        # NOTIFY fires automatically via trigger — no manual NOTIFY needed
    conn.commit()
    conn.close()
    return ids[0] if len(ids) == 1 else ids


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    to_machine = sys.argv[1].lower()
    message    = " ".join(sys.argv[2:])

    if to_machine not in ("primary", "secondary", "broadcast"):
        print(f"ERROR: to_machine must be primary/secondary/broadcast, got {to_machine!r}")
        sys.exit(1)

    try:
        row_id = send(to_machine, message)
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts}] [{ROLE} -> {to_machine}] Sent (id={row_id}): {message[:100]}")
        print(f"         NOTIFY fired — other machine will receive within milliseconds.")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
