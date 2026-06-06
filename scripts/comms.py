"""
comms.py — Read/write machine_comms channel on VPS DB.
Both machines use this as their engineer-to-engineer message bus.

Usage:
    python3 comms.py send   <recipient> <subject> <message>
    python3 comms.py read   [--all]
    python3 comms.py watch  (poll for new messages every 5s)
"""
import json, os, sys, time
from pathlib import Path

CB_DIR = Path(__file__).resolve().parent.parent
for line in (CB_DIR / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("="); k=k.strip(); v=v.strip()
        if k and k not in os.environ: os.environ[k] = v

DSN  = os.environ.get("VPS_PG_DSN", "postgresql://corvus:corvus-local-password@127.0.0.1:5433/careerbridge")
ROLE = os.environ.get("CB_SYNC_ROLE", "primary")

import psycopg2, psycopg2.extras


def send(recipient: str, subject: str, message: str, msg_type: str = "MSG") -> int:
    conn = psycopg2.connect(DSN, connect_timeout=10)
    cur  = conn.cursor()
    cur.execute(
        "INSERT INTO machine_comms (sender, recipient, msg_type, payload) VALUES (%s,%s,%s,%s) RETURNING id",
        (ROLE, recipient, msg_type, json.dumps({"subject": subject, "text": message}))
    )
    row_id = cur.fetchone()[0]
    conn.commit(); conn.close()
    return row_id


def read_unread(mark_read: bool = True) -> list[dict]:
    conn = psycopg2.connect(DSN, connect_timeout=10)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT * FROM machine_comms WHERE recipient=%s AND read=FALSE ORDER BY id",
        (ROLE,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    if mark_read and rows:
        ids = [r["id"] for r in rows]
        cur.execute("UPDATE machine_comms SET read=TRUE WHERE id=ANY(%s)", (ids,))
        conn.commit()
    conn.close()
    return rows


def read_all() -> list[dict]:
    conn = psycopg2.connect(DSN, connect_timeout=10)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM machine_comms ORDER BY id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def watch(poll_secs: int = 5):
    print(f"[comms] Watching for messages to '{ROLE}'... (Ctrl+C to stop)")
    seen = set()
    while True:
        msgs = read_unread()
        for m in msgs:
            if m["id"] not in seen:
                seen.add(m["id"])
                print(f"\n{'='*60}")
                print(f"[{m['ts']}] FROM: {m['sender']} | TYPE: {m['msg_type']}")
                payload = m["payload"] if isinstance(m["payload"], dict) else json.loads(m["payload"])
                print(f"SUBJECT: {payload.get('subject','')}")
                print(f"MESSAGE: {payload.get('text','')}")
                print('='*60)
        time.sleep(poll_secs)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "read"
    if cmd == "send" and len(sys.argv) >= 5:
        rid = send(sys.argv[2], sys.argv[3], " ".join(sys.argv[4:]))
        print(f"Sent message ID {rid} to {sys.argv[2]}")
    elif cmd == "read":
        msgs = read_unread()
        if not msgs:
            print("No unread messages.")
        for m in msgs:
            p = m["payload"] if isinstance(m["payload"], dict) else json.loads(m["payload"])
            print(f"[{m['id']}] {m['ts']} | {m['sender']} -> {m['recipient']} | {p.get('subject')}")
            print(f"  {p.get('text','')[:300]}")
    elif cmd == "read" and "--all" in sys.argv:
        for m in read_all():
            p = m["payload"] if isinstance(m["payload"], dict) else json.loads(m["payload"])
            print(f"[{m['id']}] {m['sender']} -> {m['recipient']}: {p.get('subject')}")
    elif cmd == "watch":
        watch()
    else:
        print(__doc__)
